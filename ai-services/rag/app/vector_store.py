import os
import uuid
import io
import math
from datetime import datetime, timezone
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    VectorParams,
    PointStruct,
    Filter,
    FieldCondition,
    MatchValue,
    FilterSelector,
    SparseVector,
    SparseVectorParams,
    Modifier,
    PayloadSchemaType,
    Prefetch,
    FusionQuery,
    Fusion,
    RrfQuery,
    Rrf,
    DatetimeRange,
)
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader
from dotenv import load_dotenv

load_dotenv()


def normalize_score(score: float, search_type: str, is_rrf: bool = False) -> float:
    """
    Normalize search scores to 0-1 range for consistent display.

    Args:
        score: Raw score from Qdrant
        search_type: "hybrid", "semantic", or "keyword"
        is_rrf: Whether this is an RRF-fused score

    Returns:
        Normalized score between 0 and 1
    """
    if is_rrf or search_type == "hybrid":
        # RRF scores from Qdrant: typically 0.1-0.5 for good matches
        # Use sigmoid-like scaling to map to 0-1
        # Score of 0.5 -> ~0.73, 0.3 -> ~0.52, 0.2 -> ~0.39
        return min(1.0, max(0.0, score * 1.5))

    elif search_type == "semantic":
        # Dense vectors (cosine similarity): -1 to 1
        # Normalize to 0-1
        return max(0.0, min(1.0, (score + 1.0) / 2.0))

    elif search_type == "keyword":
        # Sparse vectors: can be 0-50+, use sigmoid normalization
        return 1.0 / (1.0 + math.exp(-score / 10.0))

    return max(0.0, min(1.0, score))  # Fallback


COLLECTION = os.getenv("QDRANT_COLLECTION", "qos_buddy")
VECTOR_SIZE = 384  # dimension all-MiniLM-L6-v2
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", 1024))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", 128))


class VectorStoreClient:
    def __init__(self):
        self.client = QdrantClient(
            host=os.getenv("QDRANT_HOST", "qdrant"),
            port=int(os.getenv("QDRANT_PORT", 6333)),
        )
        self._ensure_collection()

    def _create_payload_indexes(self):
        try:
            self.client.create_payload_index(
                collection_name=COLLECTION,
                field_name="data_category",
                field_schema=PayloadSchemaType.KEYWORD,
            )

            # tenant_id: keyword index with tenant optimization
            self.client.create_payload_index(
                collection_name=COLLECTION,
                field_name="tenant_id",
                field_schema=PayloadSchemaType.KEYWORD,
            )

            # access_level: keyword index for exact matching
            self.client.create_payload_index(
                collection_name=COLLECTION,
                field_name="access_level",
                field_schema=PayloadSchemaType.KEYWORD,
            )

            # expires_at: datetime index for expiration filtering
            self.client.create_payload_index(
                collection_name=COLLECTION,
                field_name="expires_at",
                field_schema=PayloadSchemaType.DATETIME,
            )

        except Exception as e:
            print(f"Warining : could not create payload indexes: {e}")

    def _ensure_collection(self):
        names = [c.name for c in self.client.get_collections().collections]
        if COLLECTION not in names:
            self.client.create_collection(
                collection_name=COLLECTION,
                vectors_config={
                    "dense": VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
                },
                sparse_vectors_config={
                    "sparse": SparseVectorParams(modifier=Modifier.IDF)
                },
            )
            self._create_payload_indexes()

    def _generate_sparse_vector(self, text: str) -> SparseVector:
        import re
        import hashlib

        tokens = re.findall(r"\b[a-zA-Z0-9]{2,}\b", text.lower())
        term_freq: dict[str, int] = {}
        for token in tokens:
            term_freq[token] = term_freq.get(token, 0) + 1

        # Convert to sparse vector format
        # Use hash of term as index (deterministic mapping)
        # Aggregate values for hash collisions
        index_to_value: dict[int, float] = {}

        for term, freq in term_freq.items():
            # Hash term to get consistent index (0-65535 range)
            term_hash = int(hashlib.md5(term.encode()).hexdigest()[:8], 16) % 65536
            # Aggregate values for duplicate indices (handle collisions)
            index_to_value[term_hash] = index_to_value.get(term_hash, 0.0) + float(freq)

        # Convert to sorted lists (required by Qdrant)
        sorted_items = sorted(index_to_value.items())
        indices = [item[0] for item in sorted_items]
        values = [item[1] for item in sorted_items]

        return SparseVector(indices=indices, values=values)

    def reset_collection(self):
        self.client.delete_collection(COLLECTION)
        self._ensure_collection()

    def ingest_text(self, text: str, metadata: dict, embedder) -> list[str]:
        payload_metadata = dict(metadata or {})
        payload_metadata.setdefault(
            "ingested_at", datetime.now(timezone.utc).isoformat()
        )

        splitter = RecursiveCharacterTextSplitter(
            chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP
        )
        chunks = splitter.split_text(text)
        if not chunks:
            return []
        dense_vectors = embedder.encode(chunks).tolist()

        points, ids = [], []
        for chunk, dense_vec in zip(chunks, dense_vectors):
            pid = str(uuid.uuid4())
            ids.append(pid)

            sparse_vec = self._generate_sparse_vector(chunk)
            points.append(
                PointStruct(
                    id=pid,
                    vector={"dense": dense_vec, "sparse": sparse_vec},
                    payload={"text": chunk, **payload_metadata},
                )
            )
        self.client.upsert(collection_name=COLLECTION, points=points)
        return ids

    def hybrid_search(
        self,
        query: str,
        embedder,
        top_k: int = 10,
        tenant_id: str = None,
        data_category: str = None,
        access_levels: list[str] = None,
        dense_weight: float = 0.7,
        min_score: float = 0.7,
    ) -> list[dict]:
        from datetime import datetime

        dense_query_vector = embedder.encode([query]).tolist()[0]
        sparse_query_vector = self._generate_sparse_vector(query)

        filter_conditions = []

        if tenant_id:
            filter_conditions.append(
                FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id))
            )

        if data_category:
            filter_conditions.append(
                FieldCondition(
                    key="data_category", match=MatchValue(value=data_category)
                )
            )

        if access_levels:
            filter_conditions.append(
                FieldCondition(key="access_level", match=MatchValue(any=access_levels))
            )

        # Combine filters
        search_filter = Filter(must=filter_conditions) if filter_conditions else None

        # Calculate weights for RRF fusion
        # dense_weight: 0.0-1.0 (higher = more weight on dense/semantic)
        # sparse_weight: 1.0 - dense_weight (inverse)
        sparse_weight = 1.0 - dense_weight

        # Perform hybrid search with prefetch and weighted RRF fusion
        results = self.client.query_points(
            collection_name=COLLECTION,
            prefetch=[
                Prefetch(
                    query=dense_query_vector,
                    using="dense",
                    limit=top_k * 2,
                    filter=search_filter,
                ),
                Prefetch(
                    query=sparse_query_vector,
                    using="sparse",
                    limit=top_k * 2,
                    filter=search_filter,
                ),
            ],
            query=RrfQuery(rrf=Rrf(weights=[sparse_weight, dense_weight])),
            limit=top_k,
            with_payload=True,
        )

        # Format results with normalization and threshold filtering
        normalized_results = []
        for r in results.points:
            norm_score = normalize_score(r.score, "hybrid", is_rrf=True)

            # Apply threshold filter
            if norm_score >= min_score:
                normalized_results.append(
                    {
                        "text": r.payload.get("text", ""),
                        "score": norm_score,
                        "raw_score": float(r.score),
                        "metadata": {
                            key: value
                            for key, value in r.payload.items()
                            if key != "text"
                        },
                    }
                )

        return normalized_results

    def search(self, query_vector: list[float], top_k: int = 5) -> list[dict]:
        results = self.client.query_points(
            collection_name=COLLECTION,
            query=query_vector,
            using="dense",
            limit=top_k,
            with_payload=True,
        )
        return [
            {"text": r.payload.get("text", ""), "score": r.score, "metadata": r.payload}
            for r in results.points
        ]

    def keyword_search(
        self,
        query: str,
        top_k: int = 10,
        tenant_id: str = None,
        data_category: str = None,
        access_levels: list[str] = None,
        min_score: float = 0.7,
    ) -> list[dict]:
        """Keyword-only search using sparse vectors"""
        from datetime import datetime

        sparse_query_vector = self._generate_sparse_vector(query)

        # Build filter
        filter_conditions = []

        if tenant_id:
            filter_conditions.append(
                FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id))
            )

        if data_category:
            filter_conditions.append(
                FieldCondition(
                    key="data_category", match=MatchValue(value=data_category)
                )
            )

        if access_levels:
            filter_conditions.append(
                FieldCondition(key="access_level", match=MatchValue(any=access_levels))
            )

        search_filter = Filter(must=filter_conditions) if filter_conditions else None

        # Search using only sparse vectors (keyword matching)
        results = self.client.query_points(
            collection_name=COLLECTION,
            query=sparse_query_vector,
            using="sparse",
            limit=top_k,
            query_filter=search_filter,
            with_payload=True,
        )

        # Normalize and filter results
        normalized_results = []
        for r in results.points:
            norm_score = normalize_score(r.score, "keyword")

            if norm_score >= min_score:
                normalized_results.append(
                    {
                        "text": r.payload.get("text", ""),
                        "score": norm_score,
                        "raw_score": float(r.score),
                        "metadata": {
                            key: value
                            for key, value in r.payload.items()
                            if key != "text"
                        },
                    }
                )

        return normalized_results

    def list_documents(self, limit: int = 500) -> list[dict]:
        documents: dict[str, dict] = {}
        offset = None

        while True:
            points, next_offset = self.client.scroll(
                collection_name=COLLECTION,
                limit=250,
                with_payload=True,
                with_vectors=False,
                offset=offset,
            )

            for point in points:
                payload = point.payload or {}
                source = str(payload.get("source") or "unknown")
                ingested_at = payload.get("ingested_at")

                if source not in documents:
                    documents[source] = {
                        "document_id": source,
                        "source": source,
                        "chunk_count": 0,
                        "last_updated": ingested_at,
                    }

                documents[source]["chunk_count"] += 1
                if ingested_at and (
                    documents[source]["last_updated"] is None
                    or ingested_at > documents[source]["last_updated"]
                ):
                    documents[source]["last_updated"] = ingested_at

            if next_offset is None or len(documents) >= limit:
                break
            offset = next_offset

        rows = list(documents.values())
        rows.sort(
            key=lambda item: (
                item.get("last_updated") or "",
                item.get("source") or "",
            ),
            reverse=True,
        )
        return rows[:limit]

    def delete_document(self, source: str) -> int:
        source_filter = Filter(
            must=[FieldCondition(key="source", match=MatchValue(value=source))]
        )
        count_result = self.client.count(
            collection_name=COLLECTION,
            count_filter=source_filter,
            exact=True,
        )
        deleted_chunks = count_result.count
        if deleted_chunks == 0:
            return 0

        self.client.delete(
            collection_name=COLLECTION,
            points_selector=FilterSelector(filter=source_filter),
            wait=True,
        )
        return deleted_chunks

    def total_chunks(self) -> int:
        count_result = self.client.count(
            collection_name=COLLECTION,
            exact=True,
        )
        return count_result.count

    @staticmethod
    def extract_pdf_text(content: bytes) -> str:
        reader = PdfReader(io.BytesIO(content))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
