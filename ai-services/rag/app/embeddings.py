import os
from sentence_transformers import SentenceTransformer
from dotenv import load_dotenv

load_dotenv()

_embedder = None


def get_embedder() -> SentenceTransformer:
    global _embedder
    if _embedder is None:
        model_name = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
        _embedder = SentenceTransformer(model_name)
    return _embedder
