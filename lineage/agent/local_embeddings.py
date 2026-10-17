"""Local text embeddings: BAAI/bge-base-en-v1.5 (768 dimensions, MIT licence) through fastembed's quantised ONNX build. No API key, no quota, no torch.

Why local: the hosted embedding API allows about 1,000 texts a day on the free tier, which blocked the retrieval baseline and the vector index. The same model embeds the
documents at build time and the question at query time, so the RAG baseline and the FalkorDB vector index share one embedding space.
The model is downloaded once (about 210 MB) into `cache_dir`; set FASTEMBED_CACHE to move it (the single-container image bakes it in).
"""
from __future__ import annotations

import base64
import os
from pathlib import Path

import numpy as np

MODEL = "BAAI/bge-base-en-v1.5"
DIM = 768
MAX_CHUNK_TOKENS = 400  # well below the model's 512-token window, which also holds the special tokens
DEFAULT_CACHE = Path(os.environ.get("FASTEMBED_CACHE", "data/cache/fastembed"))


def available() -> bool:
    """True when the local embedding model can be used here: fastembed is installed and the build is not the slim one (LINEAGE_SLIM=1)."""
    import importlib.util

    return os.environ.get("LINEAGE_SLIM") != "1" and importlib.util.find_spec("fastembed") is not None


SLIM_NOTE = "needs the local embedding model, which is not in this slim build; it is available in the local setup (docker compose up)"


class LocalEmbedder:
    """Same interface as GeminiEmbedder.embed(texts, task) so retrievers can use either."""

    def __init__(self, model: str = MODEL, cache_dir: Path | str = DEFAULT_CACHE, threads: int | None = None):
        self.model_name, self.cache_dir, self.threads = model, str(cache_dir), threads
        self._model = None

    @property
    def model(self):
        if self._model is None:
            from fastembed import TextEmbedding

            self._model = TextEmbedding(self.model_name, cache_dir=self.cache_dir, threads=self.threads)
        return self._model

    def count_tokens(self, text: str) -> int:
        return len(self.model.model.tokenizer.encode(text, add_special_tokens=False).ids)

    def embed_documents(self, texts: list[str], batch_size: int = 32) -> list[list[float]]:
        return [v.tolist() for v in self.model.embed(texts, batch_size=batch_size)]

    def embed_query(self, text: str) -> list[float]:
        """bge models are trained to see a short instruction before a SEARCH query (fastembed adds it); documents get none."""
        return next(iter(self.model.query_embed(text))).tolist()

    def embed(self, texts: list[str], task: str = "RETRIEVAL_DOCUMENT", **_) -> list[list[float]]:
        return [self.embed_query(t) for t in texts] if task == "RETRIEVAL_QUERY" else self.embed_documents(texts)


def pack(vec: list[float]) -> str:
    """float16 base64: 768 dims in 2 KB instead of about 14 KB of JSON text (cosine ranking is unchanged for normalised vectors)."""
    return base64.b64encode(np.asarray(vec, dtype=np.float16).tobytes()).decode("ascii")


def unpack(b64: str) -> list[float]:
    return np.frombuffer(base64.b64decode(b64), dtype=np.float16).astype(np.float32).tolist()
