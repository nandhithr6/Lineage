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
from lineage.ingest.github_prs import SNAPSHOT as PRS_SNAPSHOT
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
    ("CodePath", "id"),
    ("Team", "id"),
    ("PR", "number"),
    ("Chunk", "id"),
    ("Issue", "id"),
    ("Note", "id"),
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


CHUNKS_SNAPSHOT = Path("snapshot/chunks.jsonl")
PR_KEPS_SNAPSHOT = Path("snapshot/pr_keps.jsonl")
VECTOR_DIM = 768
CHUNK_PARENT_KEY = {"DocSection": "id", "FeatureGate": "name", "KEP": "id", "SIG": "id", "Team": "id", "CodePath": "id", "Claim": "id"}  # label -> identifying property


def ensure_vector_index(g: Graph) -> None:
    """HNSW cosine index over Chunk.embedding (768 dims, the size of BAAI/bge-base-en-v1.5). Safe to call repeatedly."""
    try:
        g.query(f"CREATE VECTOR INDEX FOR (c:Chunk) ON (c.embedding) OPTIONS {{dimension: {VECTOR_DIM}, similarityFunction: 'cosine'}}")
        log.info("created vector index on Chunk.embedding")
    except Exception as e:  # already exists on reruns
        log.debug("vector index: %s", e)


Q_CHUNK = """
UNWIND $rows AS r
MERGE (c:Chunk {id: r.id})
SET c.text = r.text, c.kind = r.kind, c.source_url = r.source_url, c.line_start = r.line_start, c.line_end = r.line_end, c.parent = r.parent, c.model = r.model,
    c.embedding = vecf32(r.vec), c.recorded_at = coalesce(c.recorded_at, $now)
"""


def load_chunks(g: Graph, path: Path, now: str) -> int:
    """Text chunks with their embeddings (FalkorDB vector index) and a CHUNK_OF edge to the node each chunk is about, so a vector hit can be expanded along the graph."""
    from lineage.agent.local_embeddings import MODEL, unpack

    ensure_vector_index(g)
    rows = [{"id": r["id"], "text": r["text"], "kind": r["kind"], "source_url": r["source_url"], "line_start": r["line_start"], "line_end": r["line_end"],
             "parent": r["parent"], "model": MODEL, "vec": unpack(r["vec"])} for r in read_jsonl(path)]
    g.query("MATCH (c:Chunk) WHERE NOT c.id IN $ids DETACH DELETE c", {"ids": [r["id"] for r in rows]})  # a chunk that no longer exists must not linger
    run(g, Q_CHUNK, rows, now)
    by_label: dict[str, list[dict]] = {}
    for r in rows:
        if r["parent"]:
            label, _, key = r["parent"].partition(":")
            by_label.setdefault(label, []).append({"id": r["id"], "key": key, "source_url": r["source_url"]})
    for label, prop in CHUNK_PARENT_KEY.items():
        run(g, f"UNWIND $rows AS r MATCH (c:Chunk {{id: r.id}}), (p:{label} {{{prop}: r.key}}) MERGE (c)-[e:CHUNK_OF]->(p) "
               "SET e.source_url = r.source_url, e.extracted_by = 'structured', e.confidence = 1.0, e.recorded_at = coalesce(e.recorded_at, $now)", by_label.get(label, []), now)
    log.info("chunks: %d (vector index, 768 dims)", len(rows))
    return len(rows)


Q_ISSUE = """
UNWIND $rows AS r
MERGE (i:Issue {id: r.id})
SET i.number = r.number, i.repo = r.repo, i.title = r.title, i.state = r.state, i.url = r.url, i.created_at = r.created_at, i.closed_at = r.closed_at,
    i.source_url = r.url, i.recorded_at = coalesce(i.recorded_at, $now)
"""
Q_FIXES = """
UNWIND $rows AS r
MATCH (p:PR {number: r.pr}), (i:Issue {id: r.issue})
MERGE (p)-[e:FIXES]->(i)
SET e.source_url = r.pr_url, e.extracted_by = 'structured', e.confidence = 1.0, e.recorded_at = coalesce(e.recorded_at, $now)
"""
Q_IMPLEMENTS = """
UNWIND $rows AS r
MATCH (p:PR {number: r.pr}), (k:KEP {id: r.kep})
MERGE (p)-[e:IMPLEMENTS]->(k)
SET e.method = r.method, e.confidence = r.confidence, e.evidence = r.evidence, e.source_url = r.source_url, e.extracted_by = 'inferred',
    e.recorded_at = coalesce(e.recorded_at, $now)
"""


def load_issues(g: Graph, prs: list[dict], now: str) -> tuple[int, int]:
    """Tickets: GitHub's own `closingIssuesReferences` of each PR (a "Fixes #N" that GitHub recognised), as Issue nodes and PR-[:FIXES]->Issue edges."""
    issues: dict[str, dict] = {}
    fixes: list[dict] = []
    for pr in prs:
        for c in pr.get("closes") or []:
            iid = f"{c['repo']}#{c['number']}"
            issues[iid] = {"id": iid, "number": c["number"], "repo": c["repo"], "title": c["title"], "state": c["state"], "url": c["url"],
                           "created_at": c["created_at"], "closed_at": c["closed_at"]}
            fixes.append({"pr": pr["number"], "issue": iid, "pr_url": pr["url"]})
    run(g, Q_ISSUE, list(issues.values()), now)
    run(g, Q_FIXES, fixes, now)
    log.info("issues: %d, FIXES edges: %d", len(issues), len(fixes))
    return len(issues), len(fixes)


def load_pr_keps(g: Graph, rows: list[dict], now: str) -> int:
    """Inferred PR -> KEP links (lineage/link/pr_kep.py): derived hypotheses, so the previous run's edges are dropped first."""
    g.query("MATCH ()-[e:IMPLEMENTS]->() DELETE e")
    run(g, Q_IMPLEMENTS, rows, now)
    log.info("IMPLEMENTS edges: %d", len(rows))
    return len(rows)


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


def is_drift_eligible(kind: str, page_path: str, state: str | None = "stable") -> bool:
    """Only hard-coded labels with a real stage can go stale, and the style guide's shortcodes are examples, not claims."""
    from lineage.ingest.docs_labels import VALID_STATES

    return kind == "hardcoded" and state in VALID_STATES and not page_path.startswith(STYLE_GUIDE_PREFIX)


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
SET a.method = 'linker', a.link_confidence = r.confidence, a.signals = r.signals, a.eliminated = r.eliminated,
    a.source_url = r.source_url, a.extracted_by = 'inferred', a.confidence = r.confidence,
    a.recorded_at = coalesce(a.recorded_at, $now)
"""


def load_links(g: Graph, links_path: Path, claims_source: dict[str, str], now: str) -> int:
    """ABOUT edges from the linker: up to 3 candidate gates per hard-coded claim, each with its own confidence."""
    # Linker candidates are derived hypotheses, regenerated on every run: drop the previous run's edges so a candidate the
    # linker no longer produces cannot linger (MERGE alone never removes). Hand-verified (manual) and gate_name links stay.
    g.query("MATCH ()-[a:ABOUT]->() WHERE a.method IN ['linker', 'manual'] DELETE a")  # manual links are re-created from verified_links.jsonl right after
    rows = [
        {"claim_id": link["claim_id"], "gate": c["gate"], "confidence": c["confidence"],
         "signals": json.dumps(c["signals"], sort_keys=True), "source_url": claims_source[link["claim_id"]],
         "eliminated": json.dumps(link.get("eliminated", []))}
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
                   "APPROVED_KEP", "CHAIRS", "TECH_LEAD_OF", "EMERITUS_LEAD_OF", "OWNS", "CHILD_OF", "IN_PATH", "REVIEWED", "APPROVED", "TOUCHES", "REVIEWED_BY"}, rel
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


Q_CODEPATH = """
UNWIND $rows AS r
MERGE (c:CodePath {id: r.id})
SET c.repo = r.repo, c.path = r.dir, c.has_owners = r.has_owners, c.no_parent_owners = r.no_parent_owners,
    c.has_filters = r.has_filters, c.labels = r.labels, c.source_url = r.source_url
"""
Q_TEAM = "UNWIND $rows AS r MERGE (t:Team {id: r.id}) SET t.repo = r.repo, t.alias = r.alias"
OWNER_RELS = ("APPROVER_OF", "REVIEWER_OF", "EMERITUS_APPROVER_OF")


def owner_edge_query(rel: str, subject_label: str) -> str:
    """Ownership edge from a Person or Team to a CodePath. valid_from and filter are part of the edge identity so a
    person can hold the same role over several separate time intervals; valid_to null = still true at the latest sample."""
    assert rel in OWNER_RELS and subject_label in ("Person", "Team")
    key = "handle" if subject_label == "Person" else "id"
    return (f"UNWIND $rows AS r MATCH (a:{subject_label} {{{key}: r.subject}}), (b:CodePath {{id: r.dir_id}}) "
            f"MERGE (a)-[e:{rel} {{valid_from: r.valid_from, filter: r.filter}}]->(b) "
            "SET e.valid_to = r.valid_to, e.source_url = r.source_url, e.extracted_by = 'structured', e.confidence = 1.0, "
            "e.recorded_at = coalesce(e.recorded_at, $now)")


Q_MEMBER = """
UNWIND $rows AS r
MATCH (p:Person {handle: r.handle}), (t:Team {id: r.team})
MERGE (p)-[e:MEMBER_OF {valid_from: r.valid_from}]->(t)
SET e.valid_to = r.valid_to, e.source_url = r.source_url, e.extracted_by = 'structured', e.confidence = 1.0,
    e.recorded_at = coalesce(e.recorded_at, $now)
"""
Q_RELEASE_DATE = "UNWIND $rows AS r MATCH (x:Release {num: r.num}) SET x.date = r.date"


def load_owners(g: Graph, snap: Path, labels: list[dict], now: str) -> None:
    from lineage.ingest.owners import scope_dirs_k8s

    codepaths = read_jsonl(snap / "codepaths.jsonl")
    rows = [{**c, "id": f"{c['repo']}:{c['dir']}"} for c in codepaths]
    run(g, Q_CODEPATH, rows, now)
    run(g, edge_query("CHILD_OF", ("CodePath", "id"), ("CodePath", "id")),
        prov([{"src": r["id"], "dst": f"{r['repo']}:{r['parent']}", "source_url": r["source_url"] or ""}
              for r in rows if r["parent"] is not None]), now)
    edges, members = read_jsonl(snap / "owners_edges.jsonl"), read_jsonl(snap / "team_members.jsonl")
    teams = {e["subject"] for e in edges if e["subject_kind"] == "team"} | {m["team"] for m in members}
    run(g, Q_TEAM, [{"id": t, "repo": t.split(":")[0], "alias": t.split(":", 1)[1]} for t in sorted(teams)], now)
    people = sorted({e["subject"] for e in edges if e["subject_kind"] == "person"} | {m["handle"] for m in members})
    run(g, Q_PERSON, [{"handle": h, "name": None} for h in people], now)
    for rel in OWNER_RELS:
        for kind, label in (("person", "Person"), ("team", "Team")):
            sel = [{**e, "dir_id": f"{e['repo']}:{e['dir']}"} for e in edges if e["role"] == rel and e["subject_kind"] == kind]
            run(g, owner_edge_query(rel, label), sel, now)
    run(g, Q_MEMBER, members, now)
    pages = sorted({x["page_path"] for x in labels})
    run(g, edge_query("IN_PATH", ("DocPage", "path"), ("CodePath", "id")),
        prov([{"src": p_, "dst": f"website:{str(Path(p_).parent)}", "source_url": ""} for p_ in pages]), now)
    sigs_url = {s["id"]: s["source_url"] for s in read_jsonl(SIGS_SNAPSHOT)}
    owns = [{"src": sig, "dst": f"kubernetes:{d}", "source_url": sigs_url[sig]}
            for d, sigs in scope_dirs_k8s().items() for sig in sorted(set(sigs))]
    run(g, edge_query("OWNS", ("SIG", "id"), ("CodePath", "id")), prov(owns), now)
    dates = read_jsonl(snap / "release_dates.jsonl")
    run(g, Q_RELEASE_DATE, dates, now)
    log.info("code paths: %d, teams: %d, ownership rows: %d, team memberships: %d, SIG OWNS: %d",
             len(rows), len(teams), len(edges), len(members), len(owns))


BOT_MARKERS = ("[bot]", "-bot", "robot", "k8s-ci", "dependabot")  # automation accounts are not people; excluded from reviewer/expert data


def is_bot(login: str | None) -> bool:
    return not login or any(m in login.lower() for m in BOT_MARKERS)


def nearest_codepath(directory: str, known: set[str]) -> str | None:
    """Deepest known kubernetes folder that contains `directory`, never the repository root (everything is under the root, so it says nothing)."""
    parts = [p for p in directory.split("/") if p]
    for i in range(len(parts), 0, -1):
        cand = "/".join(parts[:i])
        if cand in known:
            return cand
    return None


CROSS_CUTTING = 10  # a PR touching this many distinct code paths (3% of them) is a repo-wide change, not evidence of expertise in any one folder


def is_cherry_pick(title: str, base_ref: str | None = None) -> bool:
    """A backport to a release branch (base branch is not master/main; or, if the branch is unknown, the 'Automated cherry pick of #123' title).
    Their approvals are release-process work, not domain expertise."""
    if base_ref:
        return base_ref not in ("master", "main")
    return title.lower().startswith(("automated cherry pick of", "cherry pick of", "[release-"))


Q_PR = """
UNWIND $rows AS r
MERGE (p:PR {number: r.number})
SET p.title = r.title, p.url = r.url, p.merged_at = r.merged_at, p.created_at = r.created_at, p.sigs = r.sigs,
    p.files_total = r.files_total, p.files_truncated = r.files_truncated, p.reviews_truncated = r.reviews_truncated,
    p.cherry_pick = r.cherry_pick, p.paths_touched = r.paths_touched, p.cross_cutting = r.cross_cutting
"""


def load_prs(g: Graph, prs: list[dict], known_dirs: set[str], now: str) -> dict[str, int]:
    """PR nodes; Person-AUTHORED/REVIEWED/APPROVED->PR (per person and PR: latest date and review count); PR-TOUCHES->nearest CodePath."""
    rows = []
    for p in prs:
        n = len({nearest_codepath(x, known_dirs) for x in p["dirs"]} - {None})
        rows.append({**p, "cherry_pick": is_cherry_pick(p["title"], p.get("base_ref")), "paths_touched": n, "cross_cutting": n >= CROSS_CUTTING or bool(p["files_truncated"])})
    run(g, Q_PR, rows, now)
    people = {p["author"] for p in prs if not is_bot(p["author"])} | {r["login"] for p in prs for r in p["reviews"] if not is_bot(r["login"])}
    run(g, Q_PERSON, [{"handle": h, "name": None} for h in sorted(people)], now)
    authored, reviewed, approved, touches = [], [], [], []
    reviewed_by_pr: dict[int, list[dict]] = {}
    for p in prs:
        if not is_bot(p["author"]):
            authored.append({"src": p["author"], "dst": p["number"], "source_url": p["url"], "at": p["created_at"], "count": 1})
        per: dict[str, dict] = {}
        for r in p["reviews"]:
            if is_bot(r["login"]) or r["login"] == p["author"]:  # reviewing your own PR is not review activity
                continue
            e = per.setdefault(r["login"], {"last": r["at"], "n": 0, "approved": None})
            e["n"] += 1
            e["last"] = max(e["last"] or "", r["at"] or "")
            if r["state"] == "APPROVED":
                e["approved"] = max(e["approved"] or "", r["at"] or "")
        for login, e in per.items():
            row = {"src": login, "dst": p["number"], "source_url": p["url"], "at": e["last"], "count": e["n"]}
            reviewed.append(row)
            reviewed_by_pr.setdefault(p["number"], []).append(row)
            if e["approved"]:
                approved.append({**row, "at": e["approved"]})
        for d in {nearest_codepath(x, known_dirs) for x in p["dirs"]} - {None}:
            touches.append({"src": p["number"], "dst": f"kubernetes:{d}", "source_url": p["url"]})
    # Derived review network for graph algorithms: (author)-[:REVIEWED_BY]->(reviewer), one edge per pair, counting the PRs. Only mainline, folder-level work counts
    # (backports and repo-wide PRs say nothing about who relies on whom). Direction matters for PageRank: a reviewer many people rely on collects the incoming edges.
    pair: dict[tuple[str, str], dict] = {}
    for p, row in zip(prs, rows):
        if row["cherry_pick"] or row["cross_cutting"] or is_bot(p["author"]):
            continue
        for rv in reviewed_by_pr.get(p["number"], []):
            e = pair.setdefault((p["author"], rv["src"]), {"src": p["author"], "dst": rv["src"], "count": 0, "at": rv["at"], "source_url": p["url"]})
            e["count"] += 1
            e["at"] = max(e["at"] or "", rv["at"] or "")
    extra = "SET e.at = r.at, e.count = r.count"
    for e in pair.values():
        e["extracted_by"], e["confidence"] = "inferred", 1.0  # derived from PR reviews, not stated by any source
    run(g, edge_query("REVIEWED_BY", ("Person", "handle"), ("Person", "handle"), extra), list(pair.values()), now)
    run(g, edge_query("AUTHORED", ("Person", "handle"), ("PR", "number"), extra), prov(authored), now)
    run(g, edge_query("REVIEWED", ("Person", "handle"), ("PR", "number"), extra), prov(reviewed), now)
    run(g, edge_query("APPROVED", ("Person", "handle"), ("PR", "number"), extra), prov(approved), now)
    run(g, edge_query("TOUCHES", ("PR", "number"), ("CodePath", "id")), prov(touches), now)
    log.info("PRs: %d, people: %d, authored %d, reviewed %d, approved %d, touches %d, review pairs %d", len(prs), len(people), len(authored), len(reviewed), len(approved), len(touches), len(pair))
    return {"prs": len(prs), "authored": len(authored), "reviewed": len(reviewed), "approved": len(approved), "touches": len(touches), "review_pairs": len(pair)}


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
        row["drift_eligible"] = is_drift_eligible(lab["kind"], lab["page_path"], lab["state"])
        rows.append(row)
    run(g, Q_DOC, rows, now)
    lookups = [r for r in rows if r["kind"] == "gate_lookup"]
    run(g, Q_ABOUT_LOOKUP, lookups, now)
    log.info("labels: %d (gate lookups: %d)", len(rows), len(lookups))


def counts(g: Graph) -> dict[str, int]:
    out: dict[str, int] = {}
    for label in ["Release", "FeatureGate", "DocPage", "DocSection", "Claim"]:
        out[label] = g.ro_query(f"MATCH (n:{label}) RETURN count(n)").result_set[0][0]
    for label in ["KEP", "SIG", "Person", "CodePath", "Team", "PR", "Chunk", "Issue"]:
        out[label] = g.ro_query(f"MATCH (n:{label}) RETURN count(n)").result_set[0][0]
    out["Finding"] = g.ro_query("MATCH (n:Finding {active: true}) RETURN count(n)").result_set[0][0]  # inactive ones are history
    for rel in ["STAGE_IN", "HAS_SECTION", "HAS_CLAIM", "ABOUT", "OWNED_BY", "PARTICIPATES", "INTRODUCES", "REPLACES",
                "SEE_ALSO", "SUPERSEDED_BY", "AUTHORED", "APPROVED_KEP", "CHAIRS", "TECH_LEAD_OF", "EMERITUS_LEAD_OF",
                "CHILD_OF", "IN_PATH", "OWNS", "APPROVER_OF", "REVIEWER_OF", "EMERITUS_APPROVER_OF", "MEMBER_OF", "REVIEWED", "APPROVED", "TOUCHES", "CHUNK_OF", "FIXES", "IMPLEMENTS"]:
        out[rel] = g.ro_query(f"MATCH ()-[e:{rel}]->() RETURN count(e)").result_set[0][0]
    return out


def load_all(g: Graph, gates_path: Path = GATES_SNAPSHOT, labels_path: Path = LABELS_SNAPSHOT, include_prs: bool = True, include_chunks: bool = True) -> dict[str, int]:
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
    if (Path("snapshot") / "owners_edges.jsonl").exists():
        load_owners(g, Path("snapshot"), labels, now)
    if include_prs and PRS_SNAPSHOT.exists():
        known = {c["dir"] for c in read_jsonl(Path("snapshot") / "codepaths.jsonl") if c["repo"] == "kubernetes"}
        prs = read_jsonl(PRS_SNAPSHOT)
        load_prs(g, prs, known, now)
        load_issues(g, prs, now)
        if PR_KEPS_SNAPSHOT.exists():
            load_pr_keps(g, read_jsonl(PR_KEPS_SNAPSHOT), now)
    if VERIFIED_LINKS.exists():
        load_verified(g, VERIFIED_LINKS, {x["id"]: x["source_url"] for x in labels}, now)
    from lineage.agent.local_embeddings import available as embeddings_available

    if include_chunks and CHUNKS_SNAPSHOT.exists() and embeddings_available():  # the slim build has no embedding model, so the vector index would never be queried
        load_chunks(g, CHUNKS_SNAPSHOT, now)
    elif include_chunks and CHUNKS_SNAPSHOT.exists():
        log.info("slim build: text chunks and the vector index are not loaded")
    from lineage.graph.paths import set_path_weights

    set_path_weights(g)  # algo.SPpaths needs a numeric weight on every edge it may use (path_between, reviewer connections)
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
