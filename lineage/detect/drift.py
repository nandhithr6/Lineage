"""Drift detector: compare hard-coded docs labels with the verified stage history of the gate they are about.

Run: `uv run --env-file .env python -m lineage.detect.drift [--write]`

Rules (CLAUDE.md §5, DECISIONS.md):
  * Only drift-eligible claims with a CONFIDENT link (>= 0.8) are judged. Possible links never produce findings.
  * "Actual" stage = stage(s) at the current release (1.37), or at the gate's last known release if it was removed.
    Where the source lists two stages for one release (overlap), either one counts as correct.
  * stale_stage (finding): the claimed stage is not an actual stage.
  * documents_removed_feature (review item): the gate was removed after alpha/deprecated, so the feature itself was dropped.
    Pages mention removed features on purpose, so this is "needs a look", not a finding.
  * wrong_since_version (minor): the stage matches but the history says the feature entered that stage at another version.
  * link_ambiguous (review item): the top link disagrees with the label but a runner-up link (>= 0.5) agrees exactly, so the
    disagreement may be a linking error rather than drift. A linking error must never masquerade as a finding (CLAUDE.md 5.5).
  * possible_drift (review item): the claim looks stale but the link is not ANCHORED. Anchored = the gate's name appears next to
    "feature gate" in the section, or a human verified the link. Many labels describe features that never had a gate, and the linker
    always returns its best guess, so an unanchored link can only ever support a "possible" report, never a finding.
  * Findings are nodes keyed by stable keys (kind|claim|gate|release|stage), never internal IDs: edges cannot point at
    edges, and internal IDs can be reused after a rebuild.
"""
from __future__ import annotations

import argparse
import json
import logging
from collections import Counter
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone

from falkordb import Graph

from lineage.config import load_settings
from lineage.graph.client import connect
from lineage.ingest.gates import CURRENT_RELEASE
from lineage.link.linker import CONFIDENT

log = logging.getLogger(__name__)

SEVERITY = {"stale_stage": "finding", "wrong_since_version": "minor", "documents_removed_feature": "review", "link_ambiguous": "review",
            "possible_drift": "review"}
ANCHOR_NAME = 0.9  # name_mention strength that counts as anchoring (name next to the words "feature gate")
RUNNER_UP = 0.5  # a runner-up link at least this strong can veto a finding
DROPPED_STAGES = {"alpha", "beta", "deprecated"}  # a removed gate whose last stage is one of these was dropped, not graduated


@dataclass(frozen=True)
class HistoryRow:
    release_num: int
    version: str
    stage: str
    overlap: bool = False


@dataclass(frozen=True)
class Evaluation:
    kind: str
    severity: str
    claim_id: str
    gate: str
    release: str  # release the comparison was made at
    claimed_stage: str
    claimed_since: str | None
    actual_stages: list[str]
    entered_version: str | None  # when the history says the claimed stage was entered
    gate_removed: bool
    evidence: list[dict]  # stable keys {gate, release, stage}; never internal edge IDs
    message: str

    @property
    def key(self) -> str:
        return f"{self.kind}|{self.claim_id}|{self.gate}|{self.release}|{self.claimed_stage}"


def evaluate_claim(claim_id: str, claimed_stage: str, claimed_since: str | None, gate: str,
                   removed: bool, history: list[HistoryRow]) -> Evaluation | None:
    """Pure judgement for one confidently linked claim. None means "label is consistent": stay silent."""
    if not history:
        return None
    last = max(r.release_num for r in history)
    at_last = [r for r in history if r.release_num == last]
    actual = sorted({r.stage for r in at_last})
    evidence = [{"gate": gate, "release": r.version, "stage": r.stage} for r in at_last]
    release = at_last[0].version
    entered = next((r.version for r in sorted(history, key=lambda r: r.release_num) if r.stage == claimed_stage), None)

    def make(kind: str, message: str) -> Evaluation:
        return Evaluation(kind, SEVERITY[kind], claim_id, gate, release, claimed_stage, claimed_since,
                          actual, entered, removed, evidence, message)

    final = max(at_last, key=lambda r: {"alpha": 0, "beta": 1, "stable": 2, "deprecated": 3}.get(r.stage, 0)).stage
    if removed and final in DROPPED_STAGES:
        return make("documents_removed_feature",
                    f"gate {gate} was removed after its last stage ({'/'.join(actual)}), so the feature appears to have been dropped; "
                    f"the page labels it {claimed_stage}")
    if claimed_stage not in actual:
        state = f"removed after reaching {'/'.join(actual)} (now locked on)" if removed else f"{'/'.join(actual)} as of {release}"
        return make("stale_stage", f"label says {claimed_stage}, but gate {gate} is {state}")
    if claimed_since and entered and claimed_since != entered:
        return make("wrong_since_version", f"label says {claimed_stage} since {claimed_since}, history says {claimed_stage} since {entered}")
    return None


def is_anchored(method: str | None, signals_json: str | None) -> bool:
    if method in ("manual", "gate_name"):
        return True
    try:
        return json.loads(signals_json or "{}").get("name_mention", 0.0) >= ANCHOR_NAME
    except ValueError:
        return False


def judge_claim(claim_id: str, state: str, since: str | None, ranked: list[tuple],
                history: dict[str, list[HistoryRow]]) -> Evaluation | None:
    """Judge a claim whose top link is confident. `ranked` = [(confidence, gate, removed, method, anchored)], best first."""
    _, gate, removed, _, anchored = ranked[0]
    ev = evaluate_claim(claim_id, state, since, gate, removed, history.get(gate, []))
    if ev and ev.severity != "review":
        agreeing = [g2 for conf2, g2, rem2, _, _ in ranked[1:]
                    if conf2 >= RUNNER_UP and history.get(g2)
                    and evaluate_claim(claim_id, state, since, g2, rem2, history[g2]) is None]
        if agreeing:
            ev = replace(ev, kind="link_ambiguous", severity="review",
                         message=f"{ev.message}; but {agreeing[0]} also fits the label exactly, so the link may be wrong")
        elif not anchored:
            ev = replace(ev, kind="possible_drift", severity="review",
                         message=f"{ev.message} (link not anchored: confirm that the label is really about this gate)")
    return ev


# --- graph access --------------------------------------------------------------------------------------

Q_CLAIMS = """
MATCH (c:Claim {kind: 'hardcoded'})
OPTIONAL MATCH (c)-[a:ABOUT]->(g:FeatureGate)
RETURN c.id, c.state, c.since, c.drift_eligible, g.name, g.removed, a.link_confidence, a.method, a.signals
"""

Q_HISTORY = """
MATCH (g:FeatureGate)-[e:STAGE_IN]->(r:Release) WHERE g.name IN $names
RETURN g.name, r.num, r.version, e.stage, e.overlap
"""


def fetch(g: Graph) -> tuple[list[list], dict[str, list[HistoryRow]]]:
    rows = g.ro_query(Q_CLAIMS).result_set
    names = sorted({r[4] for r in rows if r[4]})
    history: dict[str, list[HistoryRow]] = {}
    for name, num, version, stage, overlap in g.ro_query(Q_HISTORY, {"names": names}).result_set:
        history.setdefault(name, []).append(HistoryRow(num, version, stage, bool(overlap)))
    return rows, history


def run_detector(g: Graph) -> tuple[list[Evaluation], dict[str, int]]:
    rows, history = fetch(g)
    by_claim: dict[str, dict] = {}
    for cid, state, since, eligible, gate, removed, conf, method, signals in rows:
        entry = by_claim.setdefault(cid, {"state": state, "since": since, "eligible": bool(eligible), "cands": []})
        if gate:
            entry["cands"].append((conf, gate, bool(removed), method, is_anchored(method, signals)))
    stats: Counter[str] = Counter()
    results: list[Evaluation] = []
    for cid, c in sorted(by_claim.items()):
        if not c["eligible"]:
            stats["excluded (style guide)"] += 1
            continue
        stats["claims checked"] += 1
        if not c["cands"]:
            stats["unresolved"] += 1
            continue
        ranked = sorted(c["cands"], reverse=True)
        if ranked[0][0] < CONFIDENT:
            stats["possible only"] += 1
            continue
        stats["confident"] += 1
        ev = judge_claim(cid, c["state"], c["since"], ranked, history)
        if ev:
            results.append(ev)
    for ev in results:
        stats[f"{ev.severity}: {ev.kind}"] += 1
    return results, dict(stats)


Q_FINDING = """
UNWIND $rows AS r
MATCH (c:Claim {id: r.claim_id}), (g:FeatureGate {name: r.gate}), (x:Release {version: r.release})
MERGE (f:Finding {key: r.key})
SET f.kind = r.kind, f.severity = r.severity, f.claimed_stage = r.claimed_stage, f.claimed_since = r.claimed_since,
    f.actual_stages = r.actual_stages, f.entered_version = r.entered_version, f.gate_removed = r.gate_removed,
    f.evidence = r.evidence, f.message = r.message, f.active = true,
    f.detected_at = coalesce(f.detected_at, $now)
MERGE (f)-[:FOR_CLAIM]->(c)
MERGE (f)-[:FOR_GATE]->(g)
MERGE (f)-[:AS_OF]->(x)
"""


def write_findings(g: Graph, results: list[Evaluation]) -> None:
    """MERGE findings by stable key; findings no longer produced are marked inactive, never deleted."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = [{**asdict(ev), "key": ev.key, "evidence": json.dumps(ev.evidence)} for ev in results]
    g.query("MATCH (f:Finding) SET f.active = false")
    if rows:
        g.query(Q_FINDING, {"rows": rows, "now": now})


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description="Detect stale documentation labels")
    ap.add_argument("--write", action="store_true", help="also write Finding nodes to the graph")
    ap.add_argument("--graph", default=None)
    args = ap.parse_args()
    s = load_settings()
    g = connect(s).select_graph(args.graph or s.falkordb_graph)
    results, stats = run_detector(g)
    if args.write:
        write_findings(g, results)
    print(f"Release compared against: {CURRENT_RELEASE} (or the gate's last known release if removed)\n")
    for severity, title in (("finding", "FINDINGS"), ("minor", "MINOR FINDINGS"), ("review", "REVIEW ITEMS (needs a look)")):
        group = [e for e in results if e.severity == severity]
        print(f"== {title}: {len(group)}")
        for e in group:
            page = e.claim_id.split("#")[0].removeprefix("content/en/docs/")
            print(f"  [{e.kind}] {page}  ({e.claim_id.split('#')[1]})\n      {e.message}")
    print("\n== Counts")
    for k, v in sorted(stats.items()):
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
