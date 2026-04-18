import os
from FlagEmbedding import BGEM3FlagModel
from dotenv import load_dotenv

load_dotenv()

_embedder = None


def get_embedder() -> BGEM3FlagModel:
    global _embedder
    if _embedder is None:
        model_name = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")
        _embedder = BGEM3FlagModel(model_name, use_fp16=True)
    return _embedder
