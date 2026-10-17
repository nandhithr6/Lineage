"""Write docs/queries.md from the Cypher strings in the code, so the document cannot drift from what the product runs.

Run: uv run python -m lineage.graph.export_queries
"""
from __future__ import annotations

import textwrap
from pathlib import Path

from lineage.detect import drift
from lineage.graph import loader, queries, trace
from lineage.search import vector
from lineage.agent import tools  # noqa: F401  (tool queries are inline; listed by name below)

SECTIONS = [
    ("Ownership: who owns this path? (walk UP the folder tree)",
     "`CHILD_OF*0..` is a variable-length traversal; the `none(...)` condition stops the walk after a folder whose OWNERS says "
     "`no_parent_owners: true`. `as_of` is a sampled release number (130 = 1.30), which makes this the time-travel query too.",
     queries.Q_WALK_UP),
    ("Team members at a point in time", "Aliases such as `sig-docs-en-owners` are `Team` nodes; people are resolved by the graph, not copied onto folders.", queries.Q_MEMBERS),
    ("Drift detector input: hard-coded claims and their candidate gates", "Each claim can have 0..3 `ABOUT` candidates, each with its own confidence and method.", drift.Q_CLAIMS),
    ("Drift detector input: a gate's stage in every release", "One `STAGE_IN` edge per release in the gate's life.", drift.Q_HISTORY),
    ("Writing a Finding (idempotent)", "Findings are keyed by `kind|claim|gate|release|stage`, never by internal IDs. `FOR_CLAIM`, `FOR_GATE` and `AS_OF` point at nodes (edges cannot point at edges).", drift.Q_FINDING),
    ("Graph algorithm: PageRank over the review network", "`algo.pageRank(label, relationship-type)` runs inside FalkorDB on the derived `REVIEWED_BY` edges (author to reviewer). Ranks position in the review network, not skill.", queries.Q_PAGERANK),
    ("Vector index: create it and load a chunk", "HNSW cosine index over 768-dimension embeddings (BAAI/bge-base-en-v1.5, computed locally); `vecf32` stores the vector on the node. `CHUNK_OF` edges link a chunk to the node it is about.", loader.Q_CHUNK),
    ("Vector search: the nearest chunks to a question", "FalkorDB's score is a cosine distance (0 = identical); the product reports 1 - distance. Vector queries cannot be combined with property filters, so hits are expanded along edges afterwards (search_text).", vector.Q_VECTOR),
    ("Trace: gate stage history", "", trace.Q_HISTORY),
    ("Trace: the design decisions (KEPs) that introduced a gate", "", trace.Q_KEPS),
    ("Trace: how a claim was linked to its gate", "`method` is `gate_name`, `linker` or `manual`; the since-version rule's eliminated candidates are stored on the edge.", trace.Q_LINK),
    ("Loader: a stage edge (MERGE makes the load idempotent)", "`MERGE` finds the edge or creates it; running the loader twice gives identical counts.", loader.Q_STAGE_IN),
    ("Loader: ownership edge with validity interval", "`valid_from` and `filter` are part of the edge identity, so one person can hold a role over several separate intervals.", loader.owner_edge_query("APPROVER_OF", "Person")),
]


def main() -> None:
    out = ["# Cypher queries the product depends on", "",
           "Generated from the code by `python -m lineage.graph.export_queries`; do not edit by hand.", "",
           "**Graph algorithms:** traversals (pattern matching, variable-length paths, aggregation) plus FalkorDB's `algo.pageRank` over the review network. "
           "The HNSW vector index over `Chunk.embedding` (768 dims, cosine) serves free-text search and the vector-RAG baseline.", ""]
    for title, note, q in SECTIONS:
        out += [f"## {title}", ""] + ([note, ""] if note else []) + ["```cypher", textwrap.dedent(q).strip(), "```", ""]
    out += ["## Agent tools (each runs read-only `ro_query` calls)", "",
            "`drift_findings`, `explain_finding`, `gate_timeline`, `doc_claims`, `owners_of`, `sig_leaders`, `decision_lineage`: see `lineage/agent/tools.py`. "
            "Every tool returns an evidence bundle whose items carry a `source_url`."]
    Path("docs/queries.md").write_text("\n".join(out) + "\n")
    print("docs/queries.md written")


if __name__ == "__main__":
    main()
