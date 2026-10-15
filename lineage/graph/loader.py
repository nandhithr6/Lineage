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
from lineage.ingest.keps import SNAPSHOT as KEPS_SNAPSHOT
from lineage.ingest.sigs import SNAPSHOT as SIGS_SNAPSHOT
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
    ("KEP", "id"),
    ("SIG", "id"),
    ("Person", "handle"),
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


# Edge queries are built from this fixed whitelist (Cypher cannot take a relationship type as a parameter).
_EDGE_PROV = """SET e.source_url = r.source_url, e.extracted_by = r.extracted_by, e.confidence = r.confidence,
    e.recorded_at = coalesce(e.recorded_at, $now)"""


def edge_query(rel: str, src: tuple[str, str], dst: tuple[str, str], extra: str = "") -> str:
    assert rel in {"OWNED_BY", "PARTICIPATES", "INTRODUCES", "REPLACES", "SEE_ALSO", "SUPERSEDED_BY", "AUTHORED",
                   "APPROVED_KEP", "CHAIRS", "TECH_LEAD_OF", "EMERITUS_LEAD_OF"}, rel
    return (f"UNWIND $rows AS r MATCH (a:{src[0]} {{{src[1]}: r.src}}), (b:{dst[0]} {{{dst[1]}: r.dst}}) "
            f"MERGE (a)-[e:{rel}]->(b) {extra} {_EDGE_PROV}")


Q_KEP = """
UNWIND $rows AS r
MERGE (k:KEP {id: r.id})
SET k.number = r.number, k.title = r.title, k.status = r.status, k.status_raw = r.status_raw, k.stage = r.stage,
    k.latest_milestone = r.latest_milestone, k.milestone_alpha = r.milestone_alpha, k.milestone_beta = r.milestone_beta,
    k.milestone_stable = r.milestone_stable, k.creation_date = r.creation_date, k.summary = r.summary,
    k.unresolved_refs = r.unresolved_refs, k.source_url = r.source_url
"""
Q_SIG = """
UNWIND $rows AS r
MERGE (s:SIG {id: r.id})
SET s.name = r.name, s.group_type = r.group_type, s.label = r.label, s.mission = r.mission,
    s.subprojects = r.subprojects, s.source_url = r.source_url
"""
Q_PERSON = "UNWIND $rows AS r MERGE (p:Person {handle: r.handle}) SET p.name = coalesce(r.name, p.name)"


def prov(rows: list[dict], url_key: str = "source_url", by: str = "structured", conf: float = 1.0) -> list[dict]:
    return [{**r, "source_url": r[url_key] if url_key in r else r["source_url"], "extracted_by": r.get("extracted_by", by),
             "confidence": r.get("confidence", conf)} for r in rows]


def load_sigs(g: Graph, groups: list[dict], now: str) -> None:
    run(g, Q_SIG, [{**s, "subprojects": json.dumps(s["subprojects"])} for s in groups], now)
    people = {l["handle"]: l["name"] for s in groups for l in s["leaders"]}
    run(g, Q_PERSON, [{"handle": h, "name": n} for h, n in people.items()], now)
    for role, rel in (("chair", "CHAIRS"), ("tech_lead", "TECH_LEAD_OF"), ("emeritus_lead", "EMERITUS_LEAD_OF")):
        rows = [{"src": l["handle"], "dst": s["id"], "source_url": s["source_url"]}
                for s in groups for l in s["leaders"] if l["role"] == role]
        run(g, edge_query(rel, ("Person", "handle"), ("SIG", "id")), prov(rows), now)
    log.info("groups: %d, people: %d", len(groups), len(people))


def load_keps(g: Graph, keps: list[dict], now: str) -> None:
    rows = [{**k, "milestone_alpha": k["milestone"].get("alpha"), "milestone_beta": k["milestone"].get("beta"),
             "milestone_stable": k["milestone"].get("stable")} for k in keps]
    run(g, Q_KEP, rows, now)
    people = sorted({h for k in keps for h in k["authors"] + k["approvers"]})
    run(g, Q_PERSON, [{"handle": h, "name": None} for h in people], now)

    def pairs(field: str, src_key: str = "id") -> list[dict]:
        return [{"src": k[src_key], "dst": v, "source_url": k["source_url"]} for k in keps for v in k[field]]

    run(g, edge_query("OWNED_BY", ("KEP", "id"), ("SIG", "id")),
        prov([{"src": k["id"], "dst": k["owning_sig"], "source_url": k["source_url"]} for k in keps]), now)
    run(g, edge_query("PARTICIPATES", ("KEP", "id"), ("SIG", "id")), prov(pairs("participating_sigs")), now)
    for rel, field in (("REPLACES", "replaces"), ("SEE_ALSO", "see_also"), ("SUPERSEDED_BY", "superseded_by")):
        run(g, edge_query(rel, ("KEP", "id"), ("KEP", "id")), prov(pairs(field)), now)
    run(g, edge_query("AUTHORED", ("Person", "handle"), ("KEP", "id")),
        prov([{"src": h, "dst": k["id"], "source_url": k["source_url"]} for k in keps for h in k["authors"]]), now)
    run(g, edge_query("APPROVED_KEP", ("Person", "handle"), ("KEP", "id")),
        prov([{"src": h, "dst": k["id"], "source_url": k["source_url"]} for k in keps for h in k["approvers"]]), now)
    intro = [{"src": k["id"], "dst": gate, "source_url": k["source_url"], "method": "kep_yaml", "extracted_by": "structured", "confidence": 1.0}
             for k in keps for gate in k["feature_gates"]]
    intro += [{"src": k["id"], "dst": m["gate"], "source_url": k["source_url"].replace("kep.yaml", "README.md"),
               "method": "readme_mention", "extracted_by": "inferred", "confidence": m["confidence"]}
              for k in keps for m in k["readme_gates"]]
    run(g, edge_query("INTRODUCES", ("KEP", "id"), ("FeatureGate", "name"), "SET e.method = r.method"), prov(intro), now)
    log.info("KEPs: %d, people: %d, INTRODUCES rows: %d", len(keps), len(people), len(intro))


VERIFIED_LINKS = Path("snapshot/verified_links.jsonl")

# Manual links overwrite the linker's edge for the same (claim, gate) pair but keep the linker's score for the audit trail.
Q_ABOUT_MANUAL = """
UNWIND $rows AS r
MATCH (c:Claim {id: r.claim_id}), (g:FeatureGate {name: r.gate})
MERGE (c)-[a:ABOUT]->(g)
SET a.linker_confidence = CASE WHEN a.method = 'linker' THEN a.link_confidence ELSE a.linker_confidence END
SET a.method = 'manual', a.link_confidence = 1.0, a.confidence = 1.0, a.extracted_by = 'manual',
    a.verified_by = r.verified_by, a.verified_on = r.verified_on, a.reason = r.reason, a.source_url = r.source_url,
    a.recorded_at = coalesce(a.recorded_at, $now)
"""


def load_verified(g: Graph, path: Path, claims_source: dict[str, str], now: str) -> int:
    """Human-verified claim -> gate links (disclosed in the README). Must be loaded after the linker's edges."""
    rows = []
    for v in read_jsonl(path):
        if v["claim_id"] not in claims_source:
            log.warning("verified link names an unknown claim: %s", v["claim_id"])
            continue
        rows.append({**{k: v[k] for k in ("claim_id", "gate", "verified_by", "verified_on", "reason")},
                     "source_url": claims_source[v["claim_id"]]})
    run(g, Q_ABOUT_MANUAL, rows, now)
    log.info("manually verified links: %d", len(rows))
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
    for label in ["KEP", "SIG", "Person", "Finding"]:
        out[label] = g.ro_query(f"MATCH (n:{label}) RETURN count(n)").result_set[0][0]
    for rel in ["STAGE_IN", "HAS_SECTION", "HAS_CLAIM", "ABOUT", "OWNED_BY", "PARTICIPATES", "INTRODUCES", "REPLACES",
                "SEE_ALSO", "SUPERSEDED_BY", "AUTHORED", "APPROVED_KEP", "CHAIRS", "TECH_LEAD_OF", "EMERITUS_LEAD_OF"]:
        out[rel] = g.ro_query(f"MATCH ()-[e:{rel}]->() RETURN count(e)").result_set[0][0]
    return out


def load_all(g: Graph, gates_path: Path = GATES_SNAPSHOT, labels_path: Path = LABELS_SNAPSHOT) -> dict[str, int]:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ensure_schema(g)
    load_gates(g, [gate_from_record(r) for r in read_jsonl(gates_path)], now)
    labels = read_jsonl(labels_path)
    load_labels(g, labels, now)
    if SIGS_SNAPSHOT.exists():
        load_sigs(g, read_jsonl(SIGS_SNAPSHOT), now)
    if KEPS_SNAPSHOT.exists():
        load_keps(g, read_jsonl(KEPS_SNAPSHOT), now)
    if LINKS_SNAPSHOT.exists():
        load_links(g, LINKS_SNAPSHOT, {x["id"]: x["source_url"] for x in labels}, now)
    if VERIFIED_LINKS.exists():
        load_verified(g, VERIFIED_LINKS, {x["id"]: x["source_url"] for x in labels}, now)
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
