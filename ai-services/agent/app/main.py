import asyncio
import os
import time
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from pydantic import BaseModel, Field
from graph import build_graph
from incident_graph import build_incident_graph, available_placeholder_tools
from optimization_graph import build_optimization_graph
from dotenv import load_dotenv
from config import PROVIDERS, DEFAULT_PROVIDER, LLM_MODEL, FULL_CONFIG

load_dotenv()

CHECKPOINT_DB_URI = os.getenv("CHECKPOINT_DB_URI", "").strip()


def _read_models_cache_ttl_seconds() -> int:
    raw = os.getenv("MODEL_CACHE_TTL_SECONDS", "300").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 300


MODELS_CACHE_TTL_SECONDS = _read_models_cache_ttl_seconds()


class ChatRequest(BaseModel):
    message: str
    messages: list["OpenAIMessage"] | None = None
    thread_id: str | None = None
    model: str | None = None
    provider: str | None = None
    base_url: str | None = None
    search_type: str | None = "hybrid"
    rrf_dense_weight: float | None = 0.7
    min_relevance_score: float | None = 0.7
    enable_query_rewriting: bool | None = True


class ChatResponse(BaseModel):
    thread_id: str
    response: str
    model: str
    provider: str
    sources: list[dict] = []
    search_type: str = "hybrid"
    rewritten_queries: list[str] | None = None


class OpenAIMessage(BaseModel):
    role: str
    content: str


class OpenAIChatRequest(BaseModel):
    model: str | None = None
    messages: list[OpenAIMessage]
    thread_id: str | None = None
    provider: str | None = None
    base_url: str | None = None
    stream: bool = False


class IncidentRequest(BaseModel):
    device: str
    latency: float | None = None
    cpu: float | None = None
    memory: float | None = None
    packet_loss: float | None = None
    dry_run: bool = True


class IncidentResponse(BaseModel):
    incident: dict
    risk: dict
    plan: list[str] = Field(default_factory=list)
    tool_trace: list[dict] = Field(default_factory=list)
    validation: dict = Field(default_factory=dict)
    decision: str
    expected_recovery_seconds: int | None = None


class OptimizationRequest(BaseModel):
    anomaly_result: dict | None = None
    sla_result: dict | None = None
    avg_30s: dict[str, float] = Field(default_factory=dict)
    device: str | None = None
    context: str | None = None


class OptimizationResponse(BaseModel):
    decision: dict = Field(default_factory=dict)
    recommended_actions: list[str] = Field(default_factory=list)
    tool_trace: list[dict] = Field(default_factory=list)
    confidence: float = 0.5
    risk_level: str = "medium"


async def _run_setup_maybe_async(checkpointer):
    setup = getattr(checkpointer, "setup", None)
    if not callable(setup):
        return

    result = setup()
    if asyncio.iscoroutine(result):
        await result


async def _get_db_connection() -> AsyncConnection:
    if not CHECKPOINT_DB_URI:
        raise RuntimeError("CHECKPOINT_DB_URI is not configured")
    return await AsyncConnection.connect(
        CHECKPOINT_DB_URI,
        autocommit=True,
        row_factory=dict_row,
    )


async def _ensure_threads_table():
    if not CHECKPOINT_DB_URI:
        return

    conn = await _get_db_connection()
    async with conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_threads (
                thread_id TEXT PRIMARY KEY,
                preview TEXT,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )


async def _upsert_thread_meta(thread_id: str, preview: str):
    if not CHECKPOINT_DB_URI:
        return

    conn = await _get_db_connection()
    async with conn:
        await conn.execute(
            """
            INSERT INTO chat_threads (thread_id, preview, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (thread_id)
            DO UPDATE SET
              preview = EXCLUDED.preview,
              updated_at = NOW()
            """,
            (thread_id, preview[:400]),
        )


async def _delete_thread_meta(thread_id: str):
    if not CHECKPOINT_DB_URI:
        return

    conn = await _get_db_connection()
    async with conn:
        await conn.execute(
            "DELETE FROM chat_threads WHERE thread_id = %s", (thread_id,)
        )


async def _list_thread_meta(limit: int, offset: int) -> list[dict]:
    if not CHECKPOINT_DB_URI:
        return []

    conn = await _get_db_connection()
    async with conn:
        cur = await conn.execute(
            """
            SELECT thread_id, preview, updated_at
            FROM chat_threads
            ORDER BY updated_at DESC
            LIMIT %s OFFSET %s
            """,
            (limit, offset),
        )
        rows = await cur.fetchall()

    return [
        {
            "thread_id": row["thread_id"],
            "preview": row.get("preview") or "",
            "updated_at": row["updated_at"].isoformat()
            if row.get("updated_at")
            else None,
        }
        for row in rows
    ]


def _thread_id_or_new(thread_id: str | None) -> str:
    return thread_id or str(uuid4())


def _extract_user_message(messages: list[OpenAIMessage]) -> str:
    for msg in reversed(messages):
        if msg.role == "user":
            return msg.content
    return messages[-1].content if messages else ""


def _extract_message_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
                continue
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
        return " ".join(part for part in parts if part).strip()
    if isinstance(content, dict):
        value = content.get("text") or content.get("content")
        return str(value or "")
    return str(content or "")


def _build_graph_messages(messages: list[OpenAIMessage]) -> list[BaseMessage]:
    converted: list[BaseMessage] = []
    for msg in messages:
        content = (msg.content or "").strip()
        if not content:
            continue

        role = (msg.role or "").lower()
        if role == "assistant":
            converted.append(AIMessage(content=content))
        else:
            converted.append(HumanMessage(content=content))
    return converted


def _normalize_state_messages(messages: list) -> list[dict]:
    normalized = []
    for msg in messages or []:
        role = "user"
        content = ""

        if isinstance(msg, BaseMessage):
            msg_type = getattr(msg, "type", "")
            role = "assistant" if msg_type == "ai" else "user"
            content = _extract_message_text(getattr(msg, "content", ""))
        elif isinstance(msg, dict):
            msg_type = (msg.get("role") or msg.get("type") or "").lower()
            if msg_type in {"assistant", "ai"}:
                role = "assistant"
            elif msg_type in {"system"}:
                role = "system"
            else:
                role = "user"
            content = _extract_message_text(msg.get("content", ""))
        else:
            content = _extract_message_text(msg)

        if not content:
            continue
        normalized.append({"role": role, "content": content})

    return normalized


def _resolve_provider(
    provider: str | None, base_url: str | None
) -> tuple[str, str, str | None]:
    selected_provider = provider or DEFAULT_PROVIDER

    if selected_provider not in PROVIDERS:
        raise HTTPException(
            status_code=400, detail=f"Unknown provider '{selected_provider}'"
        )

    selected = PROVIDERS[selected_provider]
    resolved_base_url = (base_url or selected["base_url"] or "").strip()
    if not resolved_base_url:
        raise HTTPException(
            status_code=400,
            detail=f"Provider '{selected_provider}' has no configured base URL",
        )

    return selected_provider, resolved_base_url, selected.get("api_key")


async def _fetch_models_for_provider(
    provider: str, base_url: str, api_key: str | None, timeout: float = 10.0
) -> list[dict]:
    endpoint = base_url.rstrip("/") + "/models"
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(endpoint, headers=headers)
            resp.raise_for_status()
            payload = resp.json()
            data = payload.get("data", [])
            models = [
                {
                    "id": item.get("id"),
                    "object": "model",
                    "owned_by": item.get("owned_by", provider),
                    "provider": provider,
                    "base_url": base_url,
                }
                for item in data
                if item.get("id")
            ]
            print(f"Loaded {len(models)} models from {provider}")
            return models
    except httpx.TimeoutException as e:
        print(f"Warning: Timeout fetching models from {provider} after {timeout}s: {e}")
        return []
    except httpx.HTTPStatusError as e:
        print(
            f"Warning: HTTP error fetching models from {provider}: {e.response.status_code} {e.response.text[:100] if e.response.text else ''}"
        )
        return []
    except Exception as e:
        print(f"Warning: Failed to fetch models from {provider}: {e}")
        return []


async def _fetch_all_models() -> list[dict]:
    models: list[dict] = []
    for provider, cfg in PROVIDERS.items():
        timeout = cfg.get("timeout_seconds", 10.0)
        fetched_models = await _fetch_models_for_provider(
            provider=provider,
            base_url=cfg["base_url"],
            api_key=cfg.get("api_key"),
            timeout=timeout,
        )
        for model in fetched_models:
            model["display_name"] = cfg.get("display_name", provider)
        models.extend(fetched_models)

    if models:
        return models

    fallback_provider, fallback_base_url, _ = _resolve_provider(DEFAULT_PROVIDER, None)
    display_name = PROVIDERS.get(fallback_provider, {}).get(
        "display_name", fallback_provider
    )
    return [
        {
            "id": LLM_MODEL,
            "object": "model",
            "owned_by": "configured-default",
            "provider": fallback_provider,
            "base_url": fallback_base_url,
            "display_name": display_name,
        }
    ]


def _is_models_cache_fresh(app: FastAPI, now: float) -> bool:
    cached_models = getattr(app.state, "models_cache_data", None)
    cached_at = getattr(app.state, "models_cache_updated_at", 0.0)
    return bool(cached_models) and (now - cached_at) < MODELS_CACHE_TTL_SECONDS


async def _get_models_with_cache(app: FastAPI) -> list[dict]:
    now = time.time()
    if _is_models_cache_fresh(app, now):
        return app.state.models_cache_data

    lock = app.state.models_cache_lock
    async with lock:
        now = time.time()
        if _is_models_cache_fresh(app, now):
            return app.state.models_cache_data

        models = await _fetch_all_models()
        app.state.models_cache_data = models
        app.state.models_cache_updated_at = now
        return models


async def _get_graph_state(graph, thread_id: str):
    config = {"configurable": {"thread_id": thread_id}}
    aget_state = getattr(graph, "aget_state", None)
    if callable(aget_state):
        return await aget_state(config)
    return graph.get_state(config)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.checkpointer = None
    app.state.checkpointer_ctx = None
    app.state.persistence_enabled = False
    app.state.graph = build_graph()
    app.state.incident_graph = build_incident_graph()
    app.state.optimization_graph = None  # built lazily with provider config
    app.state.models_cache_data = None
    app.state.models_cache_updated_at = 0.0
    app.state.models_cache_lock = asyncio.Lock()

    if CHECKPOINT_DB_URI:
        checkpointer_ctx = AsyncPostgresSaver.from_conn_string(CHECKPOINT_DB_URI)
        checkpointer = await checkpointer_ctx.__aenter__()
        await _run_setup_maybe_async(checkpointer)

        app.state.checkpointer = checkpointer
        app.state.checkpointer_ctx = checkpointer_ctx
        app.state.persistence_enabled = True
        app.state.graph = build_graph(checkpointer=checkpointer)

        await _ensure_threads_table()

    try:
        yield
    finally:
        if app.state.checkpointer_ctx is not None:
            await app.state.checkpointer_ctx.__aexit__(None, None, None)


app = FastAPI(title="QoS-Buddy Agent Service", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "agent",
        "persistence": bool(getattr(app.state, "persistence_enabled", False)),
    }


@app.get("/incident/tools")
async def get_incident_tools():
    return {
        "mode": "simulated",
        "tools": available_placeholder_tools(),
    }


@app.post("/incident/respond", response_model=IncidentResponse)
async def incident_respond(req: IncidentRequest):
    try:
        incident_graph = app.state.incident_graph
        incident = req.model_dump(exclude_none=True)
        result = await incident_graph.ainvoke({"incident": incident})

        tool_trace = result.get("tool_trace", [])
        executed_plan = [entry.get("tool", "") for entry in tool_trace if entry.get("tool")]

        return IncidentResponse(
            incident=incident,
            risk=result.get("risk", {}),
            plan=executed_plan,
            tool_trace=tool_trace,
            validation=result.get("validation", {}),
            decision=result.get("decision", "No decision produced."),
            expected_recovery_seconds=result.get("expected_recovery_seconds"),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/config")
async def get_config():
    """
    Get provider configuration for frontend.

    Returns provider metadata (display names, descriptions, enabled status)
    without exposing API keys.
    """
    return FULL_CONFIG


@app.get("/models")
async def models():
    data = await _get_models_with_cache(app)
    return {"object": "list", "data": data}


@app.get("/v1/models")
async def openai_models():
    data = await _get_models_with_cache(app)
    return {"object": "list", "data": data}


@app.post("/threads")
async def create_thread():
    """Create a new empty thread with unique ID."""
    new_thread_id = str(uuid4())

    if getattr(app.state, "persistence_enabled", False):
        await _upsert_thread_meta(new_thread_id, "")

    return {"thread_id": new_thread_id, "created_at": time.time(), "status": "created"}


@app.get("/threads")
async def list_threads(limit: int = 100, offset: int = 0):
    rows = await _list_thread_meta(limit=max(1, min(limit, 500)), offset=max(0, offset))

    data = [
        {
            "thread_id": row["thread_id"],
            "updated_at": row.get("updated_at"),
            "values": {
                "messages": [
                    {
                        "type": "human",
                        "content": row.get("preview") or row["thread_id"],
                    }
                ]
            },
        }
        for row in rows
    ]
    return {
        "object": "list",
        "data": data,
        "persistence": bool(getattr(app.state, "persistence_enabled", False)),
    }


@app.get("/threads/{thread_id}")
async def get_thread(thread_id: str):
    if not getattr(app.state, "persistence_enabled", False):
        raise HTTPException(status_code=404, detail="Thread persistence is not enabled")

    graph = app.state.graph
    snapshot = await _get_graph_state(graph, thread_id)
    values = getattr(snapshot, "values", {}) or {}
    normalized_messages = _normalize_state_messages(values.get("messages", []))

    if not normalized_messages:
        raise HTTPException(status_code=404, detail="Thread not found")

    return {
        "thread_id": thread_id,
        "messages": normalized_messages,
        "values": {
            "messages": [
                {
                    "type": "ai" if msg["role"] == "assistant" else "human",
                    "content": msg["content"],
                }
                for msg in normalized_messages
            ]
        },
    }


@app.delete("/threads/{thread_id}")
async def delete_thread(thread_id: str):
    if not getattr(app.state, "persistence_enabled", False):
        raise HTTPException(status_code=404, detail="Thread persistence is not enabled")

    checkpointer = app.state.checkpointer
    deleted = False

    adelete_thread = getattr(checkpointer, "adelete_thread", None)
    if callable(adelete_thread):
        await adelete_thread(thread_id)
        deleted = True
    else:
        delete_thread_fn = getattr(checkpointer, "delete_thread", None)
        if callable(delete_thread_fn):
            maybe_result = delete_thread_fn(thread_id)
            if asyncio.iscoroutine(maybe_result):
                await maybe_result
            deleted = True

    if not deleted:
        raise HTTPException(
            status_code=500,
            detail="Thread deletion is not supported by current checkpointer",
        )

    await _delete_thread_meta(thread_id)
    return {"thread_id": thread_id, "status": "deleted"}


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    try:
        graph = app.state.graph
        convo_id = _thread_id_or_new(req.thread_id)
        provider, resolved_base_url, resolved_api_key = _resolve_provider(
            req.provider, req.base_url
        )
        model_name = req.model or LLM_MODEL

        incoming_messages = req.messages or [
            OpenAIMessage(role="user", content=req.message)
        ]
        user_message = (
            _extract_user_message(incoming_messages).strip()
            or (req.message or "").strip()
        )
        if not user_message:
            raise HTTPException(status_code=400, detail="No valid messages provided")

        if getattr(app.state, "persistence_enabled", False):
            # Persisted threads should append only the newest turn.
            graph_messages = [HumanMessage(content=user_message)]
        else:
            graph_messages = _build_graph_messages(incoming_messages)
            if not graph_messages:
                raise HTTPException(
                    status_code=400, detail="No valid messages provided"
                )

        result = await graph.ainvoke(
            {
                "messages": graph_messages,
                "model": model_name,
                "base_url": resolved_base_url,
            },
            config={
                "configurable": {
                    "thread_id": convo_id,
                    "model": model_name,
                    "provider": provider,
                    "base_url": resolved_base_url,
                    "api_key": resolved_api_key,
                    "search_type": req.search_type or "hybrid",
                    "rrf_dense_weight": req.rrf_dense_weight or 0.7,
                    "min_relevance_score": req.min_relevance_score or 0.7,
                    "enable_query_rewriting": req.enable_query_rewriting or True,
                }
            },
        )

        await _upsert_thread_meta(convo_id, user_message)

        # Extract rewritten queries from result
        rewritten_queries = result.get("rewritten_queries")
        search_type = result.get("search_type", "hybrid")

        return ChatResponse(
            thread_id=convo_id,
            response=result["messages"][-1].content,
            model=model_name,
            provider=provider,
            sources=result.get("sources", []),
            search_type=search_type,
            rewritten_queries=rewritten_queries,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/optimization/respond", response_model=OptimizationResponse)
async def optimization_respond(req: OptimizationRequest):
    try:
        _, resolved_base_url, resolved_api_key = _resolve_provider(None, None)

        if app.state.optimization_graph is None:
            app.state.optimization_graph = build_optimization_graph(
                base_url=resolved_base_url,
                api_key=resolved_api_key,
                model=LLM_MODEL,
            )

        opt_graph = app.state.optimization_graph
        result = await opt_graph.ainvoke({
            "anomaly_result": req.anomaly_result or {},
            "sla_result": req.sla_result or {},
            "avg_30s": req.avg_30s,
            "device": req.device or "unknown",
            "context": req.context or "",
            "messages": [],
            "tool_trace": [],
            "decision_output": {},
        })

        decision = result.get("decision_output") or {}
        tool_trace = result.get("tool_trace") or []

        return OptimizationResponse(
            decision=decision,
            recommended_actions=decision.get("recommended_actions", []),
            tool_trace=tool_trace,
            confidence=float(decision.get("confidence", 0.5)),
            risk_level=str(decision.get("risk_level", "medium")),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/v1/chat/completions")
async def openai_chat_completions(req: OpenAIChatRequest):
    if req.stream:
        raise HTTPException(status_code=400, detail="Streaming is not supported yet")

    graph = app.state.graph
    convo_id = _thread_id_or_new(req.thread_id)
    provider, resolved_base_url, resolved_api_key = _resolve_provider(
        req.provider, req.base_url
    )
    model_name = req.model or LLM_MODEL
    user_message = _extract_user_message(req.messages).strip()
    if not user_message:
        raise HTTPException(status_code=400, detail="No valid messages provided")

    if getattr(app.state, "persistence_enabled", False):
        graph_messages = [HumanMessage(content=user_message)]
    else:
        graph_messages = _build_graph_messages(req.messages)
        if not graph_messages:
            raise HTTPException(status_code=400, detail="No valid messages provided")

    try:
        result = await graph.ainvoke(
            {
                "messages": graph_messages,
                "model": model_name,
                "base_url": resolved_base_url,
            },
            config={
                "configurable": {
                    "thread_id": convo_id,
                    "model": model_name,
                    "provider": provider,
                    "base_url": resolved_base_url,
                    "api_key": resolved_api_key,
                }
            },
        )
        response_text = result["messages"][-1].content
        now = int(time.time())

        await _upsert_thread_meta(convo_id, user_message)

        return {
            "id": f"chatcmpl-{uuid4().hex[:24]}",
            "object": "chat.completion",
            "created": now,
            "model": model_name,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": response_text},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            },
            "thread_id": convo_id,
            "provider": provider,
            "base_url": resolved_base_url,
            "sources": result.get("sources", []),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
