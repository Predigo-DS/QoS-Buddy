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
    rrf_dense_weight: float = 0.7
    min_score: float = 0.5

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
                    "score": c.get("score", 0.7),  # Include score in metadata
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
                    "score": c.get("score", 0.7),  # Include score in metadata
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
    model: NotRequired[str]
    base_url: NotRequired[str]
    search_type: NotRequired[str]
    rewritten_queries: NotRequired[list[str]]


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
    min_relevance = cfg.get("min_relevance_score", 0.7)
    enable_rewriting = cfg.get("enable_query_rewriting", True)

    # Query rewriting (if enabled)
    queries = [query]
    rewritten_queries = []

    if enable_rewriting:
        try:
            llm = get_llm(
                model_name=cfg.get("model", "llama-3.1-8b-instant"),
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

    system = SystemMessage(
        content=(
            "You are QoSentry, an expert assistant on Quality of Service in networks. "
            "Use the retrieved context below to answer accurately. "
            "If the context is insufficient, say so clearly.\n\n"
            f"Context:\n{state.get('context', 'No context available.')}"
            "Only answer questions pertaining to QoSentry, Networking, Quality of Experience/Service"
            "Do not answer generic or vague questions not relevant to the task at hand"
        )
    )

    response = await llm.ainvoke([system] + list(state["messages"]))
    return {"messages": [AIMessage(content=response.content)]}


# ── Graph definition ──────────────────────────────────────────────────────────
def build_graph(checkpointer=None):
    builder = StateGraph(AgentState)
    builder.add_node("retrieve", retrieve_node)
    builder.add_node("generate", generate_node)
    builder.set_entry_point("retrieve")
    builder.add_edge("retrieve", "generate")
    builder.add_edge("generate", END)
    if checkpointer is not None:
        return builder.compile(checkpointer=checkpointer)
    return builder.compile()
