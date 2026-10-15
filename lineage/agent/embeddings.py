"""Gemini embeddings with a persistent cache, built for a free tier.

Observed limit (2026-10-15, free key): about 100 embedding texts per rolling minute, regardless of text size. So requests are batched (<= 25 texts)
and paced by a rolling-window budget; a 429 waits a full minute before retrying. Everything is cached on disk (data/cache/, git-ignored) so an
interrupted run resumes where it stopped and the evaluation never pays twice for the same text.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from collections import deque
from pathlib import Path

import httpx

from lineage.config import Settings, load_settings

log = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)  # httpx logs full URLs at INFO; never let a URL (or anything key-like) reach a log
BASE = "https://generativelanguage.googleapis.com/v1beta"
CACHE_DIR = Path("data/cache")


class EmbeddingError(RuntimeError):
    pass


class GeminiEmbedder:
    def __init__(self, api_key: str, model: str, dim: int = 768, per_minute: int = 80, batch: int = 20, max_retries: int = 6,
                 cache_dir: Path = CACHE_DIR, sleep=time.sleep, clock=time.monotonic):
        self.key, self.model, self.dim, self.per_minute, self.batch, self.max_retries = api_key, model, dim, per_minute, batch, max_retries
        self._sleep, self._clock = sleep, clock
        self._sent: deque[tuple[float, int]] = deque()  # (time, texts) inside the last 60 s
        cache_dir.mkdir(parents=True, exist_ok=True)
        self.path = cache_dir / f"embeddings_{model}_{dim}.jsonl"
        self.cache: dict[str, list[float]] = {}
        if self.path.exists():
            for line in self.path.read_text().splitlines():
                if line.strip():
                    row = json.loads(line)
                    self.cache[row["k"]] = row["v"]
        self.stats = {"requests": 0, "texts_embedded": 0, "cache_hits": 0, "rate_limited": 0}

    @classmethod
    def from_settings(cls, s: Settings | None = None, **kw) -> "GeminiEmbedder":
        s = s or load_settings()
        if not (s.gemini_api_key and s.gemini_embed_model):
            raise EmbeddingError("Set GEMINI_API_KEY and GEMINI_EMBED_MODEL in .env (scripts/pick_models.py --write)")
        return cls(s.gemini_api_key, s.gemini_embed_model, **kw)

    def _key(self, text: str, task: str) -> str:
        return hashlib.sha1(f"{self.model}|{self.dim}|{task}|{text}".encode()).hexdigest()

    def _wait_for_budget(self, n: int) -> None:
        while True:
            now = self._clock()
            while self._sent and now - self._sent[0][0] >= 60:
                self._sent.popleft()
            used = sum(c for _, c in self._sent)
            if used + n <= self.per_minute:
                return
            self._sleep(max(1.0, 60 - (now - self._sent[0][0]) + 0.5))

    def _request(self, texts: list[str], task: str) -> list[list[float]]:
        body = {"requests": [{"model": f"models/{self.model}", "content": {"parts": [{"text": t}]}, "taskType": task, "outputDimensionality": self.dim} for t in texts]}
        for attempt in range(self.max_retries + 1):
            self._wait_for_budget(len(texts))
            self._sent.append((self._clock(), len(texts)))
            self.stats["requests"] += 1
            try:
                r = httpx.post(f"{BASE}/models/{self.model}:batchEmbedContents", headers={"x-goog-api-key": self.key}, json=body, timeout=90)
            except httpx.HTTPError as e:
                msg, status = f"network error {type(e).__name__}", 0
            else:
                status = r.status_code
                if status == 200:
                    return [e["values"] for e in r.json()["embeddings"]]
                msg = f"HTTP {status}: {r.text[:120].replace(self.key, '***')}"
                if status not in (429, 500, 502, 503, 504):
                    raise EmbeddingError(msg)
            self.stats["rate_limited"] += status == 429
            if attempt == self.max_retries:
                raise EmbeddingError(f"gave up after {attempt + 1} attempts: {msg}")
            wait = 65.0 if status == 429 else 5.0 * (attempt + 1)
            log.warning("embeddings: %s; waiting %.0fs (attempt %d/%d)", msg[:70], wait, attempt + 1, self.max_retries)
            self._sleep(wait)
        raise EmbeddingError("unreachable")

    def embed(self, texts: list[str], task: str = "RETRIEVAL_DOCUMENT", progress_every: int = 200) -> list[list[float]]:
        keys = [self._key(t, task) for t in texts]
        missing = [i for i, k in enumerate(keys) if k not in self.cache]
        self.stats["cache_hits"] += len(texts) - len(missing)
        done = 0
        for start in range(0, len(missing), self.batch):
            idx = missing[start : start + self.batch]
            vecs = self._request([texts[i][:6000] for i in idx], task)
            with self.path.open("a") as f:
                for i, v in zip(idx, vecs):
                    self.cache[keys[i]] = v
                    f.write(json.dumps({"k": keys[i], "v": v}) + "\n")
            done += len(idx)
            self.stats["texts_embedded"] += len(idx)
            if done % progress_every < self.batch:
                log.info("embedded %d / %d new texts (cache hits so far: %d)", done, len(missing), self.stats["cache_hits"])
        return [self.cache[k] for k in keys]
