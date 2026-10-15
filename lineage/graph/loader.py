"""Load the snapshot (gates + docs labels) into FalkorDB. Idempotent: MERGE, never CREATE.

Run: `uv run --env-file .env python -m lineage.graph.loader [--graph NAME]`

Graph written here (see docs/schema_proposal.md and DECISIONS.md):
  (DocPage)-[:HAS_SECTION]->(DocSection)-[:HAS_CLAIM]->(Claim)-[:ABOUT]->(FeatureGate)-[:STAGE_IN]->(Release)
Only gate_lookup claims get an ABOUT edge now (the gate is named in the shortcode). Hard-coded claims get
theirs from the linker, which does not exist yet; until then they are the "unresolved" claims.

Every edge carries source_url, extracted_by, confidence and recorded_at. recorded_at is set when the edge
is first created and kept on reruns.
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from falkordb import Graph

from lineage.config import load_settings
from lineage.graph.client import connect
from lineage.ingest.docs_labels import SNAPSHOT as LABELS_SNAPSHOT
from lineage.ingest.gates import SNAPSHOT as GATES_SNAPSHOT
from lineage.link.linker import LINKS_SNAPSHOT
from lineage.ingest.gates import Gate, StageRange, expand_to_releases

log = logging.getLogger(__name__)

BATCH = 200
STYLE_GUIDE_PREFIX = "content/en/docs/contribute/style/"

# (label, property) pairs that identify a node. Each gets an index + unique constraint.
KEYS = [
    ("Release", "num"),
    ("FeatureGate", "name"),
    ("DocPage", "path"),
    ("DocSection", "id"),
    ("Claim", "id"),
]


def ensure_schema(g: Graph) -> None:
    """Create the key indexes and unique constraints. Safe to call repeatedly."""
    for label, prop in KEYS:
        try:
            g.create_node_range_index(label, prop)
            g.create_node_unique_constraint(label, prop)
            log.info("created index + unique constraint on %s.%s", label, prop)
        except Exception as e:  # already exists on reruns
            log.debug("schema %s.%s: %s", label, prop, e)


def batches(rows: list[dict], size: int = BATCH) -> Iterable[list[dict]]:
    for i in range(0, len(rows), size):
        yield rows[i : i + size]


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def gate_from_record(rec: dict) -> Gate:
    stages = [
        StageRange(s["stage"], s["from_version"], s["to_version"], s["default_value"], s["locked"])
        for s in rec["stages"]
    ]
    return Gate(**{**rec, "stages": stages})


def is_drift_eligible(kind: str, page_path: str) -> bool:
    """Only hard-coded labels can go stale, and the style guide's shortcodes are examples, not claims."""
    return kind == "hardcoded" and not page_path.startswith(STYLE_GUIDE_PREFIX)


def run(g: Graph, query: str, rows: list[dict], now: str) -> None:
    for chunk in batches(rows):
        g.query(query, {"rows": chunk, "now": now})


# --- queries -------------------------------------------------------------------------------------------

Q_RELEASE = "UNWIND $rows AS r MERGE (x:Release {num: r.num}) SET x.version = r.version"

Q_GATE = """
UNWIND $rows AS r
MERGE (g:FeatureGate {name: r.name})
SET g.removed = r.removed, g.final_stage = r.final_stage, g.description = r.description,
    g.doc_links = r.doc_links, g.issues = r.issues, g.source_url = r.source_url
"""

Q_STAGE_IN = """
UNWIND $rows AS r
MATCH (g:FeatureGate {name: r.gate}), (x:Release {num: r.release_num})
MERGE (g)-[e:STAGE_IN {stage: r.stage}]->(x)
SET e.default_value = r.default_value, e.locked = r.locked, e.overlap = r.overlap,
    e.inferred_end = r.inferred_end, e.source_url = r.source_url,
    e.extracted_by = 'structured', e.confidence = 1.0,
    e.recorded_at = coalesce(e.recorded_at, $now)
"""

Q_DOC = """
UNWIND $rows AS r
MERGE (p:DocPage {path: r.page_path})
SET p.url = r.page_url, p.title = r.page_title
MERGE (s:DocSection {id: r.section_id})
SET s.heading = r.heading, s.anchor = r.anchor, s.text = r.section_text, s.doc_links = r.doc_links
MERGE (p)-[hs:HAS_SECTION]->(s)
SET hs.source_url = r.page_source_url, hs.extracted_by = 'structured', hs.confidence = 1.0,
    hs.recorded_at = coalesce(hs.recorded_at, $now)
MERGE (c:Claim {id: r.id})
SET c.kind = r.kind, c.state = r.state, c.since = r.for_k8s_version, c.gate_name = r.gate_name,
    c.shortcode = r.shortcode, c.line = r.line, c.source_url = r.source_url,
    c.drift_eligible = r.drift_eligible, c.issues = r.issues
MERGE (s)-[hc:HAS_CLAIM]->(c)
SET hc.source_url = r.source_url, hc.extracted_by = 'structured', hc.confidence = 1.0,
    hc.recorded_at = coalesce(hc.recorded_at, $now)
"""

Q_ABOUT_LOOKUP = """
UNWIND $rows AS r
MATCH (c:Claim {id: r.id}), (g:FeatureGate {name: r.gate_name})
MERGE (c)-[a:ABOUT]->(g)
SET a.method = 'gate_name', a.link_confidence = 1.0, a.source_url = r.source_url,
    a.extracted_by = 'structured', a.confidence = 1.0, a.recorded_at = coalesce(a.recorded_at, $now)
"""


Q_ABOUT_LINKER = """
UNWIND $rows AS r
MATCH (c:Claim {id: r.claim_id}), (g:FeatureGate {name: r.gate})
MERGE (c)-[a:ABOUT]->(g)
SET a.method = 'linker', a.link_confidence = r.confidence, a.signals = r.signals,
    a.source_url = r.source_url, a.extracted_by = 'inferred', a.confidence = r.confidence,
    a.recorded_at = coalesce(a.recorded_at, $now)
"""


def load_links(g: Graph, links_path: Path, claims_source: dict[str, str], now: str) -> int:
    """ABOUT edges from the linker: up to 3 candidate gates per hard-coded claim, each with its own confidence."""
    rows = [
        {"claim_id": link["claim_id"], "gate": c["gate"], "confidence": c["confidence"],
         "signals": json.dumps(c["signals"], sort_keys=True), "source_url": claims_source[link["claim_id"]]}
        for link in read_jsonl(links_path) for c in link["candidates"]
    ]
    run(g, Q_ABOUT_LINKER, rows, now)
    log.info("linker ABOUT edges: %d", len(rows))
    return len(rows)


def load_gates(g: Graph, gates: list[Gate], now: str) -> None:
    rows = [row for gate in gates for row in expand_to_releases(gate)]
    releases = sorted({r["release_num"]: r["release"] for r in rows}.items())
    run(g, Q_RELEASE, [{"num": n, "version": v} for n, v in releases], now)
    run(
        g,
        Q_GATE,
        [
            {
                "name": x.name,
                "removed": x.removed,
                "final_stage": x.stages[-1].stage if x.stages else None,
                "description": x.description,
                "doc_links": x.doc_links,
                "issues": x.issues,
                "source_url": x.source_url,
            }
            for x in gates
        ],
        now,
    )
    run(g, Q_STAGE_IN, rows, now)
    log.info("gates: %d, releases: %d, stage rows: %d", len(gates), len(releases), len(rows))


def load_labels(g: Graph, labels: list[dict], now: str) -> None:
    rows = []
    for lab in labels:
        row = dict(lab)
        row["section_id"] = f"{lab['page_path']}#{lab['anchor'] or ''}"
        row["page_source_url"] = lab["source_url"].split("#")[0]
        row["drift_eligible"] = is_drift_eligible(lab["kind"], lab["page_path"])
        rows.append(row)
    run(g, Q_DOC, rows, now)
    lookups = [r for r in rows if r["kind"] == "gate_lookup"]
    run(g, Q_ABOUT_LOOKUP, lookups, now)
    log.info("labels: %d (gate lookups: %d)", len(rows), len(lookups))


def counts(g: Graph) -> dict[str, int]:
    out: dict[str, int] = {}
    for label in ["Release", "FeatureGate", "DocPage", "DocSection", "Claim"]:
        out[label] = g.ro_query(f"MATCH (n:{label}) RETURN count(n)").result_set[0][0]
    for rel in ["STAGE_IN", "HAS_SECTION", "HAS_CLAIM", "ABOUT"]:
        out[rel] = g.ro_query(f"MATCH ()-[e:{rel}]->() RETURN count(e)").result_set[0][0]
    return out


def load_all(g: Graph, gates_path: Path = GATES_SNAPSHOT, labels_path: Path = LABELS_SNAPSHOT) -> dict[str, int]:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ensure_schema(g)
    load_gates(g, [gate_from_record(r) for r in read_jsonl(gates_path)], now)
    labels = read_jsonl(labels_path)
    load_labels(g, labels, now)
    if LINKS_SNAPSHOT.exists():
        load_links(g, LINKS_SNAPSHOT, {x["id"]: x["source_url"] for x in labels}, now)
    return counts(g)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--graph", default=None, help="graph name (default: FALKORDB_GRAPH)")
    args = ap.parse_args()
    settings = load_settings()
    g = connect(settings).select_graph(args.graph or settings.falkordb_graph)
    result: dict[str, Any] = load_all(g)
    log.info("graph %r now has: %s", g.name, result)


if __name__ == "__main__":
    main()
