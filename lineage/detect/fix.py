"""Draft a fix for a drift finding: a unified diff that changes ONLY the feature-state shortcode on one line.

The corrected label is a pure function of the evidence (stage history of the linked gate), not LLM output, so every
character of the draft can be explained. Lineage only drafts: nothing is posted anywhere (CLAUDE.md §1).
"""
from __future__ import annotations

import difflib
import re
from pathlib import Path

from falkordb import Graph

from lineage.graph.trace import build_trace
from lineage.ingest.gates import WEBSITE_REPO

STAGE_RANK = {"alpha": 0, "beta": 1, "stable": 2, "deprecated": 3}
FIXABLE = {"stale_stage", "wrong_since_version"}


def pick_actual(actual_stages: list[str]) -> str:
    """With an overlap (two stages in one release) the further-along stage is the safer thing to state."""
    return max(actual_stages, key=lambda s: STAGE_RANK.get(s, -1))


def entered_version(history: list[dict], stage: str) -> str | None:
    """First release at which the gate was in `stage` (from the collapsed stage ranges)."""
    return next((h["from"] for h in history if h["stage"] == stage), None)


def rewrite_shortcode(line: str, new_stage: str, new_version: str | None) -> str:
    """Change state= and for_k8s_version= values inside the feature-state shortcode; touch nothing else on the line."""
    out = re.sub(r'(state\s*=\s*")[^"]*(")', rf"\g<1>{new_stage}\g<2>", line, count=1)
    if new_version and re.search(r'for_k8s_version\s*=\s*"', out):
        out = re.sub(r'(for_k8s_version\s*=\s*")[^"]*(")', rf"\g<1>v{new_version}\g<2>", out, count=1)
    return out


def unified_diff(path: str, old_lines: list[str], line_no: int, new_line: str) -> str:
    new_lines = list(old_lines)
    new_lines[line_no - 1] = new_line
    return "".join(difflib.unified_diff([x + "\n" for x in old_lines], [x + "\n" for x in new_lines],
                                        fromfile=f"a/{path}", tofile=f"b/{path}", n=3))


def offset_hunks(diff: str, offset: int) -> str:
    """Shift the line numbers in '@@ -a,b +c,d @@' headers, for a diff made on an excerpt that starts at line offset+1."""
    return re.sub(r"^@@ -(\d+)(,\d+)? \+(\d+)(,\d+)? @@",
                  lambda m: f"@@ -{int(m.group(1)) + offset}{m.group(2) or ''} +{int(m.group(3)) + offset}{m.group(4) or ''} @@",
                  diff, flags=re.M)


def excerpt_from_graph(g: Graph, claim_id: str, shortcode: str, line_no: int) -> tuple[list[str], int] | None:
    """Fallback when the website clone is not on this machine (e.g. a server): the graph stores the claim's section text.

    Returns (section lines, offset) where section line i is page line offset+i+1, or None if the shortcode line cannot be located.
    """
    rows = g.ro_query("MATCH (c:Claim {id: $c})<-[:HAS_CLAIM]-(s:DocSection) RETURN s.text", {"c": claim_id}).result_set
    if not rows or not rows[0][0]:
        return None
    lines = rows[0][0].splitlines()
    hits = [i for i, x in enumerate(lines) if x.strip() == shortcode.strip()]
    if len(hits) != 1:
        return None
    return lines, line_no - 1 - hits[0]


def draft_fix(g: Graph, finding_key: str, repo_root: Path = WEBSITE_REPO) -> dict:
    b = build_trace(g, None, finding_key=finding_key)
    base = {"finding_key": finding_key, "diff": None, "explanation": None, "evidence": b["evidence"], "reviewers": [], "drafted_by": "rules"}
    if b["status"] != "ok":
        return {**base, "status": "insufficient_evidence", "note": f"No active finding with key {finding_key!r}."}
    s = b["sections"]
    f, claim, gate = s["finding"], s["claim"], s["gate"]
    base["reviewers"] = s["reviewers"]
    if f["kind"] not in FIXABLE:
        return {**base, "status": "not_applicable",
                "note": f"This is a '{f['kind']}' review item, not a confirmed drift finding. A person should confirm the link between the label "
                        "and the gate first; Lineage drafts no change until then."}
    fin_ev = next(e for e in b["evidence"] if e["type"] == "Finding")
    actual = pick_actual(fin_ev["props"]["actual_stages"])
    version = entered_version(gate["history"], actual if f["kind"] == "stale_stage" else claim["stage"])
    if not version:
        return {**base, "status": "insufficient_evidence", "note": f"The gate history does not say when {actual} began."}
    new_stage = actual if f["kind"] == "stale_stage" else claim["stage"]
    path = s["page"]["path"]
    file = repo_root / path
    offset = 0
    if file.exists():
        lines = file.read_text(encoding="utf-8").splitlines()
        idx = claim["line"] - 1
    else:  # no website clone here (a deployed server): use the section text stored in the graph
        ex = excerpt_from_graph(g, claim["id"], claim["shortcode"], claim["line"])
        if ex is None:
            return {**base, "status": "insufficient_evidence", "note": f"{path} is not available here and its section text is not in the graph."}
        lines, offset = ex
        idx = claim["line"] - 1 - offset
    old = lines[idx] if 0 <= idx < len(lines) else ""
    if old.strip() != claim["shortcode"].strip():
        return {**base, "status": "insufficient_evidence",
                "note": f"Line {claim['line']} no longer contains the shortcode the finding was based on; re-run ingestion."}
    new = rewrite_shortcode(old, new_stage, version)
    diff = offset_hunks(unified_diff(path, lines, idx + 1, new), offset)
    removed_note = (" The gate has since been removed, so the feature is now always on; a maintainer may prefer to drop the badge entirely."
                    if gate["removed"] and new_stage == "stable" else "")
    why = (f"The label says {claim['stage']}{' since v' + claim['since'] if claim['since'] else ''}. "
           f"The feature gate {gate['name']} was {new_stage} from Kubernetes v{version}.{removed_note}")
    return {**base, "status": "ok", "diff": diff, "explanation": why,
            "note": "Draft only. Review the diff and the sources; Lineage never posts to Kubernetes."}
