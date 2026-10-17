"""Vector search inside FalkorDB: the HNSW index over Chunk.embedding, queried with `db.idx.vector.queryNodes`.

FalkorDB vector queries do not combine with property filters, so the pattern is: search the vectors first, then expand the hits along graph edges (search_text tool).
`score` from FalkorDB is a cosine DISTANCE (0 = identical); we return similarity = 1 - distance so bigger is better, like the in-memory retrievers.
"""
from __future__ import annotations

from falkordb import Graph

from lineage.search.corpus import Passage

Q_VECTOR = """
CALL db.idx.vector.queryNodes('Chunk', 'embedding', $k, vecf32($v)) YIELD node, score
RETURN node.id, node.text, node.source_url, node.kind, node.line_start, node.line_end, node.parent, score
ORDER BY score
"""


def vector_search(g: Graph, vec: list[float], k: int = 5) -> list[dict]:
    rows = g.ro_query(Q_VECTOR, {"k": k, "v": vec}).result_set
    return [{"id": r[0], "text": r[1], "source_url": r[2], "kind": r[3], "line_start": r[4], "line_end": r[5], "parent": r[6], "similarity": round(1.0 - r[7], 4)} for r in rows]


class FalkorRetriever:
    """The vector-RAG baseline, served by FalkorDB's own vector index: question embedded locally, top-k chunks from the database, NO graph expansion.

    Same `.search(query, k) -> [(Passage, score)]` shape as the TF-IDF and in-memory retrievers, so run_qa treats it like any other baseline.
    """

    name = "vector"

    def __init__(self, g: Graph, embedder) -> None:
        self.g, self.embedder = g, embedder

    def search(self, query: str, k: int = 5) -> list[tuple[Passage, float]]:
        hits = vector_search(self.g, self.embedder.embed_query(query), k)
        return [(Passage(h["id"], h["text"], h["source_url"]), h["similarity"]) for h in hits]
