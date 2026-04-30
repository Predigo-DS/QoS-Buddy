import os
import asyncio
from typing import Optional
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from embeddings import get_embedder, download_progress
from vector_store import VectorStoreClient
from contextlib import asynccontextmanager
from dotenv import load_dotenv

load_dotenv()

models = {}
warmup_state = {
    "status": "idle",
    "last_error": None,
}
warmup_lock = asyncio.Lock()

init_state = {
    "status": "idle",  # idle | loading | ready | error
    "last_error": None,
}
init_lock = asyncio.Lock()


def _warmup_response():
    return {
        "status": warmup_state["status"],
        "last_error": warmup_state["last_error"],
    }


async def _run_warmup():
    if "embedder" not in models or "vs" not in models:
        return

    async with warmup_lock:
        if warmup_state["status"] == "ready":
            return

        warmup_state["status"] = "warming"
        warmup_state["last_error"] = None

        try:
            warmup_vec = models["embedder"].encode(["qosentry warmup"])
            _ = warmup_vec.tolist()
            await asyncio.to_thread(models["vs"].total_chunks)
            warmup_state["status"] = "ready"
        except Exception as e:
            warmup_state["status"] = "error"
            warmup_state["last_error"] = str(e)


async def _init_models():
    async with init_lock:
        if init_state["status"] in ("loading", "ready"):
            return

        init_state["status"] = "loading"
        init_state["last_error"] = None

        try:
            embedder = await asyncio.to_thread(get_embedder)
            models["embedder"] = embedder
        except Exception as e:
            init_state["status"] = "error"
            init_state["last_error"] = f"Embedder init failed: {e}"
            return

        max_retries = int(os.getenv("QDRANT_INIT_MAX_RETRIES", "30"))
        retry_delay = float(os.getenv("QDRANT_INIT_RETRY_DELAY_SEC", "2"))
        last_error: Exception | None = None
        for attempt in range(1, max_retries + 1):
            try:
                models["vs"] = VectorStoreClient(embedder=models["embedder"])
                last_error = None
                break
            except Exception as e:
                last_error = e
                if attempt == max_retries:
                    break
                print(
                    f"Qdrant not ready yet (attempt {attempt}/{max_retries}): {e}. "
                    f"Retrying in {retry_delay}s..."
                )
                await asyncio.sleep(retry_delay)

        if last_error is not None:
            init_state["status"] = "error"
            init_state["last_error"] = f"Vector store init failed: {last_error}"
            return

        init_state["status"] = "ready"
        init_state["last_error"] = None

        warmup_state["status"] = "idle"
        warmup_state["last_error"] = None
        asyncio.create_task(_run_warmup())


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("Loading AI models and connecting to DB...")
    models.clear()
    warmup_state["status"] = "idle"
    warmup_state["last_error"] = None
    init_state["status"] = "idle"
    init_state["last_error"] = None

    asyncio.create_task(_init_models())
    print("System starting (background init).")
    yield
    models.clear()
    warmup_state["status"] = "idle"
    warmup_state["last_error"] = None
    init_state["status"] = "idle"
    init_state["last_error"] = None


app = FastAPI(title="QoS-Buddy RAG Service", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class IngestTextRequest(BaseModel):
    text: str
    metadata: Optional[dict] = {}


class RetrieveRequest(BaseModel):
    query: str
    top_k: int = int(os.getenv("TOP_K", 5))
    search_type: str = "hybrid"  # Options: "hybrid", "semantic", "keyword"
    tenant_id: Optional[str] = None
    data_category: Optional[str] = None
    access_levels: Optional[list[str]] = None
    rrf_dense_weight: Optional[float] = 0.7  # For hybrid: 0.0-1.0 (dense weight)
    min_relevance_score: Optional[float] = 0.5  # Minimum threshold (0.0-1.0)


def _require_ready():
    if init_state["status"] == "error":
        raise HTTPException(
            status_code=503,
            detail=init_state["last_error"] or "RAG service failed to initialize",
        )
    if "embedder" not in models or "vs" not in models:
        raise HTTPException(status_code=503, detail="Models still loading")


@app.get("/health")
async def health():
    progress = 100.0 if init_state["status"] == "ready" else download_progress["percentage"]

    payload = {
        "service": "rag",
        "status": "starting",
        "init": {
            "status": init_state["status"],
            "last_error": init_state["last_error"],
            "progress": progress
        },
        "warmup": _warmup_response(),
    }

    if init_state["status"] == "error":
        payload["status"] = "degraded"
        return payload

    if "embedder" in models and "vs" in models and init_state["status"] == "ready":
        payload["status"] = "ok"
    return payload


@app.post("/warmup")
async def warmup():
    if init_state["status"] != "ready":
        asyncio.create_task(_init_models())

    _require_ready()

    if warmup_state["status"] != "warming" and warmup_state["status"] != "ready":
        asyncio.create_task(_run_warmup())

    return _warmup_response()


@app.post("/ingest/text")
async def ingest_text(req: IngestTextRequest):
    _require_ready()
    try:
        ids = models["vs"].ingest_text(req.text, req.metadata, models["embedder"])
        return {"ingested_chunks": len(ids), "ids": ids}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/ingest/file")
async def ingest_file(file: UploadFile = File(...)):
    _require_ready()
    try:
        content = await file.read()
        filename = file.filename or "unknown"
        text = (
            models["vs"].extract_pdf_text(content)
            if filename.endswith(".pdf")
            else content.decode("utf-8")
        )
        ids = models["vs"].ingest_text(text, {"source": filename}, models["embedder"])
        return {"filename": filename, "ingested_chunks": len(ids), "ids": ids}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/retrieve")
async def retrieve(req: RetrieveRequest):
    _require_ready()
    try:
        if req.search_type == "hybrid":
            chunks = models["vs"].hybrid_search(
                query=req.query,
                embedder=models["embedder"],
                top_k=req.top_k,
                tenant_id=req.tenant_id,
                data_category=req.data_category,
                access_levels=req.access_levels,
                dense_weight=req.rrf_dense_weight,
                min_score=req.min_relevance_score,
            )
        elif req.search_type == "semantic":
            semantic_result = models["embedder"].encode([req.query], return_dense=True, return_sparse=False, return_colbert_vecs=False)
            vec = semantic_result["dense_vecs"].tolist()[0]
            chunks = models["vs"].search(vec, top_k=req.top_k)
        elif req.search_type == "keyword":
            # Keyword-only search using sparse vectors
            chunks = models["vs"].keyword_search(
                query=req.query,
                embedder=models["embedder"],
                top_k=req.top_k,
                tenant_id=req.tenant_id,
                data_category=req.data_category,
                access_levels=req.access_levels,
                min_score=req.min_relevance_score,
            )
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid search_type: {req.search_type}",
            )
        return {"chunks": chunks, "query": req.query, "search_type": req.search_type}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/documents")
async def list_documents(limit: int = 500):
    _require_ready()
    try:
        rows = models["vs"].list_documents(limit=limit)
        total_chunks = models["vs"].total_chunks()
        return {
            "data": rows,
            "total_documents": len(rows),
            "total_chunks": total_chunks,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/documents/{source}")
async def delete_document(source: str):
    _require_ready()
    try:
        deleted_chunks = models["vs"].delete_document(source)
        if deleted_chunks == 0:
            raise HTTPException(status_code=404, detail="Document not found")
        return {
            "source": source,
            "deleted_chunks": deleted_chunks,
            "status": "deleted",
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/collection")
async def reset_collection():
    _require_ready()
    try:
        models["vs"].reset_collection()
        return {"status": "collection reset"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
