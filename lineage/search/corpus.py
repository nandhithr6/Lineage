"""The retrieval corpus: the SAME facts as the graph rendered as plain-text passages (docs sections, feature gates, design documents, teams, OWNERS per release).

Used by the vector-RAG baseline (eval/) and by the product's text search (search_text), so both retrieve over identical text.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from lineage.link.linker import read_jsonl

SNAP = Path("snapshot")
SAMPLED = [130, 131, 132, 133, 134, 135, 136, 137, 138]
_WORD = re.compile(r"[a-z0-9][a-z0-9_.\-/]+|[a-z0-9]+")


@dataclass(frozen=True)
class Passage:
    id: str
    text: str
    source_url: str | None


def tokens(text: str) -> list[str]:
    out: list[str] = []
    for w in _WORD.findall(text.lower()):
        out.append(w)
        if re.search(r"[-/._]", w):  # also index the pieces, so "sig-storage" matches "storage"
            out += [p for p in re.split(r"[-/._]", w) if len(p) > 1]
    return out


def at(e: dict, n: int) -> bool:
    return e["valid_from"] <= n and (e["valid_to"] is None or e["valid_to"] > n)


def build_corpus() -> list[Passage]:
    ps: list[Passage] = []
    labels = read_jsonl(SNAP / "doc_labels.jsonl")
    seen: set[str] = set()
    for lab in labels:
        sec = f"{lab['page_path']}#{lab['anchor']}"
        if sec in seen:
            continue
        seen.add(sec)
        ps.append(Passage(f"doc:{sec}", f"Docs page \"{lab['page_title']}\", section \"{lab['heading']}\". {lab['section_text'][:1500]}", lab["source_url"]))
        # the label itself, as a sentence (what a reader would see)
    for lab in labels:
        if lab["kind"] == "hardcoded":
            ps.append(Passage(f"label:{lab['id']}", f"The docs page \"{lab['page_title']}\" section \"{lab['heading']}\" has a label saying {lab['state']}"
                              f"{' since v' + lab['for_k8s_version'] if lab['for_k8s_version'] else ''} (line {lab['line']}).", lab["source_url"]))
    for g in read_jsonl(SNAP / "gates.jsonl"):
        stages = "; ".join(f"{s['stage']} from {s['from_version']}" + (f" to {s['to_version']}" if s["to_version"] else "") for s in g["stages"])
        ps.append(Passage(f"gate:{g['name']}", f"Feature gate {g['name']}: {stages}. {'The gate was removed.' if g['removed'] else 'The gate is active.'} {g['description'][:500]}", g["source_url"]))
    for k in read_jsonl(SNAP / "keps.jsonl"):
        ms = ", ".join(f"{s} {v}" for s, v in k["milestone"].items())
        ps.append(Passage(f"kep:{k['id']}", f"Design document (KEP) {k['id']} \"{k['title']}\": owned by {k['owning_sig']}; status {k['status']}; stage {k['stage']}; milestones {ms}. "
                          f"Authors: {', '.join(k['authors'])}. Approvers: {', '.join(k['approvers'])}. Feature gates: {', '.join(k['feature_gates'] + [m['gate'] for m in k['readme_gates']])}. "
                          f"Replaces: {', '.join(k['replaces'])}. {k['summary'][:400]}", k["source_url"]))
    for s in read_jsonl(SNAP / "sigs.jsonl"):
        by: dict[str, list[str]] = defaultdict(list)
        for l in s["leaders"]:
            by[l["role"]].append(l["handle"])
        ps.append(Passage(f"sig:{s['id']}", f"Team {s['id']} ({s['name']}, {s['group_type']}). Chairs: {', '.join(by['chair'])}. Tech leads: {', '.join(by['tech_lead'])}. "
                          f"Emeritus leads: {', '.join(by['emeritus_lead'])}.", s["source_url"]))
    owners = read_jsonl(SNAP / "owners_edges.jsonl")
    members = read_jsonl(SNAP / "team_members.jsonl")
    for team in sorted({m["team"] for m in members}):
        ps.append(Passage(f"team:{team}", f"Team alias {team} has members: {', '.join(sorted(m['handle'] for m in members if m['team'] == team and at(m, 138)))}.",
                          next(m["source_url"] for m in members if m["team"] == team)))
    dirs = sorted({(e["repo"], e["dir"]) for e in owners})
    for repo, d in dirs:
        rows = [e for e in owners if e["repo"] == repo and e["dir"] == d and e["filter"] == ""]
        for n in SAMPLED if repo == "kubernetes" else [138]:
            cur = [e for e in rows if at(e, n)]
            if not cur:
                continue
            def who(role):  # noqa: E306
                return ", ".join(sorted(e["subject"] for e in cur if e["role"] == role))
            ver = "latest (1.38-dev)" if n == 138 else f"1.{n - 100}"
            ps.append(Passage(f"owners:{repo}:{d}:{n}", f"OWNERS of {repo}:{d or '/'} as of release {ver}. Approvers: {who('APPROVER_OF')}. Reviewers: {who('REVIEWER_OF')}. "
                              f"Emeritus approvers: {who('EMERITUS_APPROVER_OF')}.", cur[0]["source_url"]))
    return ps


