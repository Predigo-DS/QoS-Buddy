import os
import sys
from sentence_transformers import SentenceTransformer
from dotenv import load_dotenv

# --- Monkeypatch huggingface_hub for download progress tracking ---
from tqdm.auto import tqdm
import huggingface_hub.utils

download_progress = {
    "total_bytes": 0,
    "downloaded_bytes": 0,
    "percentage": 0.0
}

class TrackingTqdm(tqdm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if hasattr(self, 'total') and self.total is not None:
            download_progress["total_bytes"] += self.total

    def update(self, n=1):
        super().update(n)
        download_progress["downloaded_bytes"] += n
        if download_progress["total_bytes"] > 0:
            download_progress["percentage"] = min(
                100.0, 
                round((download_progress["downloaded_bytes"] / download_progress["total_bytes"]) * 100, 2)
            )

# Apply the monkeypatch to huggingface_hub internals safely
if hasattr(huggingface_hub.utils, '_tqdm'):
    huggingface_hub.utils._tqdm.tqdm = TrackingTqdm
if hasattr(huggingface_hub.utils, '_progress'):
    huggingface_hub.utils._progress.tqdm = TrackingTqdm
if hasattr(huggingface_hub.utils, 'tqdm'):
    huggingface_hub.utils.tqdm = TrackingTqdm
# -------------------------------------------------------------------

load_dotenv()

_embedder = None


def get_embedder() -> SentenceTransformer:
    global _embedder
    if _embedder is None:
        model_name = os.getenv("EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-0.6B")
        print(f"Loading embedding model on CPU...", flush=True)
        sys.stdout.flush()
        _embedder = SentenceTransformer(model_name, trust_remote_code=True, device="cpu")
        print(f"Embedding model loaded on CPU", flush=True)
        sys.stdout.flush()
    return _embedder
