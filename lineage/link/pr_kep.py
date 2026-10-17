"""Link pull requests to the design documents (KEPs) they mention or implement, with the method and a confidence on every link.

A PR rarely says "implements KEP-x" in a structured field, so the link is inferred from the text the author wrote (title and the head of the description):
  * kep_url             a link into kubernetes/enhancements/keps/<sig>/<number>-<slug>      -> that exact KEP                        0.95
  * enhancement_issue   kubernetes/enhancements/issues/N or kubernetes/enhancements#N       -> KEP number N (the KEP number IS its tracking issue)
  * kep_number          "KEP-1234", "KEP 1234", "KEP #1234"                                  -> KEP number N
      for the two number forms: 0.90 when the KEP is owned by (or shared with) one of the PR's own SIG labels; 0.70 when the number is unique across all KEPs
      but the SIG does not match; NO link when the number is ambiguous (KEP numbers are not unique across SIG directories) and no SIG matches
  * feature_gate_name   the title or body names a feature gate that a KEP introduces (case-sensitive whole word, >= 8 characters)       0.50
PRs whose title is about nomination, reviewers/approvers or OWNERS files are skipped (they mention KEPs, they do not implement them). A mention is not proof of implementation: consumers show the method, and the default "implementing PRs" view only uses links of 0.70 or more.
    uv run python -m lineage.link.pr_kep        # -> snapshot/pr_keps.jsonl
"""
from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from pathlib import Path

from lineage.link.linker import read_jsonl

log = logging.getLogger(__name__)
SNAP = Path("snapshot")
OUT = SNAP / "pr_keps.jsonl"
STRONG = 0.70  # links at or above this are "implementing PRs"; below it they are only "mentions"

KEP_TREE = re.compile(r"github\.com/kubernetes/enhancements/(?:tree|blob)/[^/\s)\]]+/keps/(sig-[a-z0-9-]+)/(\d+)-([a-z0-9-]+)", re.I)
ENHANCEMENT_ISSUE = re.compile(r"(?:github\.com/kubernetes/enhancements/issues/|kubernetes/enhancements#|\benhancements#)(\d{1,5})\b", re.I)
KEP_NUMBER = re.compile(r"\bKEP[-\s#:]{0,3}(\d{2,5})\b", re.I)


# Membership/ownership PRs ("self-nominate X to be a sig-node reviewer") list a person's KEP work in the description; that is a mention of the KEP, not an implementation of it.
NOT_IMPLEMENTATION = re.compile(r"\bnominat|\b(?:to be|as) (?:an? )?(?:[\w-]+ )?(?:reviewer|approver)\b|\bemeritus\b|\bOWNERS(?:_ALIASES)?\b", re.I)


def pr_sigs(pr: dict) -> set[str]:
    return {s.replace("sig/", "sig-") for s in pr.get("sigs", [])}


def snippet(text: str, start: int, end: int, pad: int = 50) -> str:
    return re.sub(r"\s+", " ", text[max(0, start - pad): end + pad]).strip()


def build_indexes(keps: list[dict], gates_to_keps: dict[str, list[str]] | None = None):
    by_id = {k["id"]: k for k in keps}
    by_number: dict[int, list[dict]] = defaultdict(list)
    for k in keps:
        by_number[k["number"]].append(k)
    return by_id, by_number, gates_to_keps or {}


def resolve_number(number: int, sigs: set[str], by_number: dict[int, list[dict]]) -> tuple[str, float] | None:
    cands = by_number.get(number, [])
    if not cands:
        return None
    matching = [k for k in cands if k["owning_sig"] in sigs or sigs & set(k.get("participating_sigs") or [])]
    if len(matching) == 1:
        return matching[0]["id"], 0.90
    if len(matching) > 1:
        return None  # two KEPs with this number both fit the PR's SIGs: do not guess
    if len(cands) == 1:
        return cands[0]["id"], 0.70
    return None


def links_for_pr(pr: dict, by_id: dict, by_number: dict, gate_names: dict[str, list[str]]) -> list[dict]:
    if NOT_IMPLEMENTATION.search(pr["title"]):
        return []
    text = f"{pr['title']}\n{pr.get('body') or ''}"
    sigs = pr_sigs(pr)
    found: dict[str, dict] = {}

    def add(kep_id: str, method: str, conf: float, ev: str) -> None:
        if kep_id not in found or conf > found[kep_id]["confidence"]:
            found[kep_id] = {"pr": pr["number"], "kep": kep_id, "method": method, "confidence": conf, "evidence": ev[:200], "source_url": pr.get("url")}

    for m in KEP_TREE.finditer(text):
        kid = f"{m.group(1).lower()}/{m.group(2)}-{m.group(3).lower()}"
        if kid in by_id:
            add(kid, "kep_url", 0.95, snippet(text, m.start(), m.end()))
        else:  # slug differs (renamed KEP): fall back to the number within that SIG
            hit = next((k for k in by_number.get(int(m.group(2)), []) if k["owning_sig"] == m.group(1).lower()), None)
            if hit:
                add(hit["id"], "kep_url", 0.90, snippet(text, m.start(), m.end()))
    for pattern, method in ((ENHANCEMENT_ISSUE, "enhancement_issue"), (KEP_NUMBER, "kep_number")):
        for m in pattern.finditer(text):
            r = resolve_number(int(m.group(1)), sigs, by_number)
            if r:
                add(r[0], method, r[1], snippet(text, m.start(), m.end()))
    head = f"{pr['title']}\n{(pr.get('body') or '')[:1500]}"
    for gate, kep_ids in gate_names.items():
        m = re.search(rf"(?<![A-Za-z0-9]){re.escape(gate)}(?![A-Za-z0-9])", head)
        if m:
            for kid in kep_ids:
                add(kid, "feature_gate_name", 0.50, f"names feature gate {gate}: " + snippet(head, m.start(), m.end(), 40))
    return sorted(found.values(), key=lambda x: (-x["confidence"], x["kep"]))


def gate_to_keps(keps: list[dict], min_conf: float = 0.7) -> dict[str, list[str]]:
    """Feature gate -> the KEPs that introduce it, using the KEPs' own `feature-gates` field (structured, so safe) for gates of 8+ characters."""
    out: dict[str, list[str]] = defaultdict(list)
    for k in keps:
        for g in k.get("feature_gates") or []:
            if isinstance(g, str) and len(g) >= 8:
                out[g].append(k["id"])
    return {g: v for g, v in out.items() if len(v) == 1}  # a gate shared by several KEPs is ambiguous


def build(snap: Path = SNAP) -> list[dict]:
    keps = read_jsonl(snap / "keps.jsonl")
    by_id, by_number, _ = build_indexes(keps)
    gates = gate_to_keps(keps)
    rows: list[dict] = []
    for pr in read_jsonl(snap / "prs.jsonl"):
        rows += links_for_pr(pr, by_id, by_number, gates)
    return rows


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    rows = build()
    OUT.write_text("".join(json.dumps(r) + "\n" for r in rows))
    by_method: dict[str, int] = defaultdict(int)
    for r in rows:
        by_method[r["method"]] += 1
    log.info("PR -> KEP links: %d (%s); PRs linked: %d; strong (>= %.2f): %d -> %s", len(rows), dict(by_method), len({r["pr"] for r in rows}), STRONG,
             sum(r["confidence"] >= STRONG for r in rows), OUT)


if __name__ == "__main__":
    main()
