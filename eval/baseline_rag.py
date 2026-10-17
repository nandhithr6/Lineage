"""Vector-style RAG baseline: the SAME facts as the graph, rendered as text passages, retrieved by similarity, no graph.

It is deliberately generous: structured facts (OWNERS per release, gate stage ranges, KEP metadata, team members) are written out as
plain sentences so retrieval has every chance. What it cannot do is traverse: each question needs the right passages to land in the top k.
Retrieval here is TF-IDF cosine (no embedding key is available yet); an embedding retriever can replace `Retriever.search` unchanged.

Run a quick look: uv run python -m eval.baseline_rag "Who chairs sig-storage?"
"""
from __future__ import annotations

import json
import math
import re
import sys
from collections import Counter

from lineage.search.corpus import SAMPLED, SNAP, Passage, at, build_corpus, tokens  # noqa: F401  (moved to the product package; re-exported for the eval code)


from lineage.search.tfidf import Retriever  # noqa: F401,E402  (moved to the product package: the slim build uses it for the contrast toggle)


class EmbeddingRetriever:
    """The real vector-RAG baseline: passages embedded once (cached on disk), the question embedded per query, cosine similarity."""

    def __init__(self, passages: list[Passage], embedder) -> None:
        self.passages, self.embedder = passages, embedder
        self.vecs = [self._unit(v) for v in embedder.embed([p.text for p in passages], "RETRIEVAL_DOCUMENT")]

    @staticmethod
    def _unit(v: list[float]) -> list[float]:
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]

    def search(self, query: str, k: int = 5) -> list[tuple[Passage, float]]:
        q = self._unit(self.embedder.embed([query], "RETRIEVAL_QUERY")[0])
        scored = sorted(((sum(a * b for a, b in zip(q, v)), i) for i, v in enumerate(self.vecs)), reverse=True)[:k]
        return [(self.passages[i], round(s, 3)) for s, i in scored]


from lineage.search.rag import RAG_SYSTEM, rag_prompt  # noqa: F401,E402  (moved to the product package; the demo uses the same baseline)


if __name__ == "__main__":
    r = Retriever(build_corpus())
    print(f"{len(r.passages)} passages")
    for p, s in r.search(" ".join(sys.argv[1:]) or "Who chairs sig-storage?"):
        print(f"{s:.3f} {p.id}: {p.text[:110]}")
