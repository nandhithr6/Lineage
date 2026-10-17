"""Keyword (TF-IDF) retrieval over the same passages as the vector index: no model, no embeddings, pure Python. It is the contrast toggle's retriever in the slim build."""
from __future__ import annotations

import math
from collections import Counter

from lineage.search.corpus import Passage, tokens


class Retriever:
    name = "tfidf"

    def __init__(self, passages: list[Passage]):
        self.passages = passages
        toks = [tokens(p.text) for p in passages]
        df: Counter[str] = Counter()
        for t in toks:
            df.update(set(t))
        n = len(passages)
        self.idf = {w: math.log((n + 1) / (c + 1)) + 1 for w, c in df.items()}
        self.vecs = [self._vec(t) for t in toks]

    def _vec(self, toks: list[str]) -> dict[str, float]:
        tf = Counter(toks)
        v = {w: (1 + math.log(c)) * self.idf.get(w, 1.0) for w, c in tf.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {w: x / norm for w, x in v.items()}

    def search(self, query: str, k: int = 5) -> list[tuple[Passage, float]]:
        q = self._vec(tokens(query))
        scored = sorted(((sum(w * v.get(t, 0.0) for t, w in q.items()), i) for i, v in enumerate(self.vecs)), reverse=True)[:k]
        return [(self.passages[i], round(s, 3)) for s, i in scored]
