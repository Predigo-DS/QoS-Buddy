import os
from typing import TypedDict, Annotated, NotRequired
import httpx
from langchain_openai import ChatOpenAI
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage
from langchain_core.retrievers import BaseRetriever
from langchain_core.documents import Document
from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.runnables import RunnableConfig
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from dotenv import load_dotenv

load_dotenv()

RAG_URL = os.getenv("RAG_SERVICE_URL", "http://rag:8001")


# Role-based system prompts
TECHNICAL_SYSTEM_PROMPT = """You are QoSentry, a senior network engineer specializing in Quality of Service.
Audience: Technical staff (network engineers, DevOps, SREs).

Response guidelines:
- Use technical terminology (MOS, PLR, jitter, TC/netem, OpenFlow, DSCP, etc.)
- Include specific metric values, thresholds, and protocol details
- Provide actionable troubleshooting steps and configuration guidance
- Reference RFCs, ITU-T standards, or vendor documentation where relevant
- Be precise and detailed — the audience understands networking concepts"""

EXECUTIVE_SYSTEM_PROMPT = """You are QoSentry, a network operations advisor for leadership.
Audience: Executives and business stakeholders.

Response guidelines:
- Explain in plain language — avoid jargon or explain it briefly when necessary
- Focus on business impact: service quality, customer experience, risk, and cost
- Provide high-level summaries with key takeaways, not deep technical details
- Frame issues in terms of risk levels (low/medium/high/critical) and recommended actions
- Use analogies when helpful (e.g., "packet loss is like dropped phone calls")
- Keep responses concise and decision-oriented"""


def get_system_prompt(user_role: str, retrieved_context: str, log_context: str = "") -> str:
    """Build system prompt based on user role."""
    if user_role == "executive":
        base = EXECUTIVE_SYSTEM_PROMPT
    else:
        base = TECHNICAL_SYSTEM_PROMPT

    parts = [base]

    if log_context:
        parts.append(f"\n{log_context}")

    parts.append(
        f"\n\nUse the retrieved context below to answer accurately. "
        f"If the context is insufficient, say so clearly.\n\n"
        f"Context:\n{retrieved_context or 'No context available.'}"
    )

    parts.append(
        "\nOnly answer questions pertaining to QoSentry, Networking, Quality of Experience/Service. "
        "Do not answer generic or vague questions not relevant to the task at hand."
    )

    return "\n".join(parts)

QUERY_REWRITE_PROMPT = """You are a query rewriter for a RAG system about Quality of Service (QoS) in networking.
Rewrite the user's query into 2 alternative versions that might match technical documentation better.

Original query: {query}

Generate exactly 2 rewritten versions that:
1. Use more technical terminology (e.g., "latency" instead of "delay", "packet loss" instead of "dropped packets")
2. Include related QoS concepts where appropriate (e.g., "jitter", "throughput", "bandwidth", "traffic prioritization")
3. Are phrased as they might appear in technical documents or research papers
4. Maintain the original intent but expand on technical details

Return ONLY the 2 rewritten queries, one per line. Do not include the original query or any explanations.

Examples:
Original: "What causes slow network?"
Rewritten:
network latency causes and troubleshooting
network performance degradation factors

Original: "How to prioritize video traffic?"
Rewritten:
video traffic QoS prioritization techniques
multimedia traffic classification and marking for QoS

Now rewrite this query:
"""


# ── Custom LangChain Retriever wrapping the RAG microservice ──────────────────
class QoSRetriever(BaseRetriever):
    rag_url: str = RAG_URL
    top_k: int = 10
    search_type: str = "hybrid"
    rrf_dense_weight: float = 0.8
    min_score: float = 0.4
    enable_reranking: bool = True
    rerank_top_n: int = 50

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun
    ) -> list[Document]:
        with httpx.Client() as client:
            resp = client.post(
                f"{self.rag_url}/retrieve",
                json={
                    "query": query,
                    "top_k": self.top_k,
                    "search_type": self.search_type,
                    "rrf_dense_weight": self.rrf_dense_weight,
                    "min_relevance_score": self.min_score,
                    "rerank": self.enable_reranking,
                    "rerank_top_n": self.rerank_top_n,
                },
                timeout=30.0,
            )
            resp.raise_for_status()
            data = resp.json()
        return [
            Document(
                page_content=c["text"],
                metadata={
                    **c.get("metadata", {}),
                    "score": c.get("score", 0.7),
                    "rerank_score": c.get("rerank_score"),
                    "is_reranked": c.get("is_reranked", False),
                },
            )
            for c in data.get("chunks", [])
        ]

    async def _aget_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun
    ) -> list[Document]:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.rag_url}/retrieve",
                json={
                    "query": query,
                    "top_k": self.top_k,
                    "search_type": self.search_type,
                    "rrf_dense_weight": self.rrf_dense_weight,
                    "min_relevance_score": self.min_score,
                    "rerank": self.enable_reranking,
                    "rerank_top_n": self.rerank_top_n,
                },
                timeout=30.0,
            )
            resp.raise_for_status()
            data = resp.json()
        return [
            Document(
                page_content=c["text"],
                metadata={
                    **c.get("metadata", {}),
                    "score": c.get("score", 0.7),
                    "rerank_score": c.get("rerank_score"),
                    "is_reranked": c.get("is_reranked", False),
                },
            )
            for c in data.get("chunks", [])
        ]


# ── LLM : OpenAI-compatible providers via LangChain ──────────────────────────
def get_llm(
    model_name: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
) -> ChatOpenAI:
    temperature = 1.0 if model_name and "gpt-oss-120b" in model_name else 0.2
    return ChatOpenAI(
        model=model_name,
        api_key=api_key,
        base_url=base_url,
        temperature=temperature,
    )


# ── LangGraph State ───────────────────────────────────────────────────────────
class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    sources: list[dict]
    context: str
    log_context: NotRequired[str]
    model: NotRequired[str]
    base_url: NotRequired[str]
    user_role: NotRequired[str]
    search_type: NotRequired[str]
    rewritten_queries: NotRequired[list[str]]
    intent: NotRequired[str]


# ── Intent classification ─────────────────────────────────────────────────────
_GREETING_PATTERNS = [
    r"^\s*(hi|hello|hey|howdy|greetings)\b",
    r"^\s*(good\s+(morning|afternoon|evening|day))\b",
    r"^\s*(what'?s\s+up|sup|yo)\b",
    r"^\s*thanks?\s*(for\s+\w+)?\s*$",
    r"^\s*(cheers|bye|see\s+you|take\s+care)\s*$",
    r"^\s*(please|sorry|excuse\s+me)\s*$",
]


def _classify_intent(query: str) -> str:
    """Classify message intent: greeting or technical."""
    import re
    lower = query.strip().lower()
    if len(lower.split()) <= 4:
        for pattern in _GREETING_PATTERNS:
            if re.search(pattern, lower):
                return "greeting"
    return "technical"


# ── Query Rewriting ───────────────────────────────────────────────────────────
async def rewrite_query(query: str, llm) -> list[str]:
    """Generate 2 rewritten versions of the query using LLM."""
    prompt = QUERY_REWRITE_PROMPT.format(query=query)

    response = await llm.ainvoke([SystemMessage(content=prompt)])

    # Parse response (one query per line)
    rewritten = response.content.strip().split("\n")
    rewritten = [q.strip() for q in rewritten if q.strip()]

    return rewritten[:2]


# ── Node 1 : retrieve via LangChain Retriever ─────────────────────────────────
import asyncio


async def retrieve_node(state: AgentState, config: RunnableConfig) -> AgentState:
    last_human = next(
        (m for m in reversed(state["messages"]) if isinstance(m, HumanMessage)), None
    )
    query = last_human.content if last_human else ""

    cfg = (config or {}).get("configurable", {}) if config else {}
    search_type = cfg.get("search_type", "hybrid")
    rrf_dense_weight = cfg.get("rrf_dense_weight", 0.7)
    min_relevance = cfg.get("min_relevance_score", 0.4)
    enable_rewriting = cfg.get("enable_query_rewriting", True)
    enable_reranking = cfg.get("enable_reranking", True)
    rerank_top_n = cfg.get("rerank_top_n", 50)

    # Query rewriting (if enabled)
    queries = [query]
    rewritten_queries = []

    if enable_rewriting:
        try:
            llm = get_llm(
                model_name=cfg.get("model", "qwen/qwen3-32b"),
                base_url=cfg.get("base_url"),
                api_key=cfg.get("api_key"),
            )
            rewritten_queries = await rewrite_query(query, llm)
            queries.extend(rewritten_queries)
        except Exception as e:
            print(f"Warning: Query rewriting failed: {e}. Using original query only.")

    retriever = QoSRetriever(
        rag_url=RAG_URL,
        top_k=10,
        search_type=search_type,
        rrf_dense_weight=rrf_dense_weight,
        min_score=min_relevance,
        enable_reranking=enable_reranking,
        rerank_top_n=rerank_top_n,
    )

    # Retrieve for each query (parallel if multiple)
    if len(queries) > 1:
        all_docs = await asyncio.gather(*[retriever.ainvoke(q) for q in queries])
        all_docs_flat = [doc for docs in all_docs for doc in docs]

        # Deduplicate while preserving order
        seen_texts = set()
        ranked_docs = []
        for doc in all_docs_flat:
            text_hash = hash(doc.page_content[:100])
            if text_hash not in seen_texts and len(ranked_docs) < 10:
                seen_texts.add(text_hash)
                ranked_docs.append(doc)
    else:
        ranked_docs = await retriever.ainvoke(query)

    context = "\n\n".join(doc.page_content for doc in ranked_docs)
    sources = [
        {
            "text": doc.page_content,
            "metadata": doc.metadata,
            "score": doc.metadata.get("score", 0.7),
            "rerank_score": doc.metadata.get("rerank_score"),
            "is_reranked": doc.metadata.get("is_reranked", False),
        }
        for doc in ranked_docs
    ]

    return {
        "context": context,
        "sources": sources,
        "search_type": search_type,
        "rewritten_queries": rewritten_queries if rewritten_queries else None,
    }


# ── Node 2 : generate with via LangChain ─────────────────────────────────
async def generate_node(state: AgentState, config: RunnableConfig) -> AgentState:
    cfg = (config or {}).get("configurable", {}) if config else {}
    model_name = cfg.get("model") or state.get("model")
    base_url = cfg.get("base_url") or state.get("base_url")
    api_key = cfg.get("api_key")
    llm = get_llm(model_name=model_name, base_url=base_url, api_key=api_key)

    user_role = cfg.get("user_role") or state.get("user_role", "technical")
    retrieved_context = state.get("context", "No context available.")
    log_context = state.get("log_context", "")

    system = SystemMessage(content=get_system_prompt(user_role, retrieved_context, log_context))

    response = await llm.ainvoke([system] + list(state["messages"]))
    sources = state.get("sources", [])
    search_type = state.get("search_type")
    rewritten_queries = state.get("rewritten_queries")

    ai_message = AIMessage(content=response.content)
    if sources:
        ai_message.additional_kwargs["metadata"] = {
            "sources": sources,
            "search_type": search_type,
            "rewritten_queries": rewritten_queries,
        }
    return {"messages": [ai_message]}


# ── Log context node ──────────────────────────────────────────────────────────
async def log_context_node(state: AgentState, config: RunnableConfig) -> AgentState:
    """Fetch recent logs and format as context for the LLM."""
    from logging_service import get_recent_logs_for_rag, format_rag_log_context
    try:
        logs = await get_recent_logs_for_rag()
        log_ctx = format_rag_log_context(logs)
        return {"log_context": log_ctx}
    except Exception as e:
        print(f"[WARN] Failed to fetch log context: {e}")
        return {"log_context": ""}


# ── Intent classification node ────────────────────────────────────────────────
def classify_node(state: AgentState) -> AgentState:
    """Classify user intent and route accordingly."""
    last_human = next(
        (m for m in reversed(state["messages"]) if isinstance(m, HumanMessage)), None
    )
    query = last_human.content if last_human else ""
    intent = _classify_intent(query)
    return {"intent": intent}


def route_by_intent(state: AgentState) -> str:
    """Route to full pipeline for technical, skip to generate for greetings."""
    return state.get("intent", "technical")


# ── Graph definition ──────────────────────────────────────────────────────────
def build_graph(checkpointer=None):
    builder = StateGraph(AgentState)
    builder.add_node("classify", classify_node)
    builder.add_node("retrieve", retrieve_node)
    builder.add_node("fetch_logs", log_context_node)
    builder.add_node("generate", generate_node)
    builder.set_entry_point("classify")
    builder.add_conditional_edges("classify", route_by_intent, {
        "technical": "retrieve",
        "greeting": "generate",
    })
    builder.add_edge("retrieve", "fetch_logs")
    builder.add_edge("fetch_logs", "generate")
    builder.add_edge("generate", END)
    if checkpointer is not None:
        return builder.compile(checkpointer=checkpointer)
    return builder.compile()
