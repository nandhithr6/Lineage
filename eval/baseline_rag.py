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
from collections import Counter, defaultdict
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


class Retriever:
    def __init__(self, passages: list[Passage]):
        self.passages = passages
        toks = [tokens(p.text) for p in passages]
        df: Counter[str] = Counter()
        for t in toks:
            df.update(set(t))
        n = len(passages)
        self.idf = {w: math.log((n + 1) / (c + 1)) + 1 for w, c in df.items()}
        self.vecs = [self._vec(t) for t in toks]

    def _vec(self, toks: list[str]) -> dict[str, float]:
        tf = Counter(toks)
        v = {w: (1 + math.log(c)) * self.idf.get(w, 1.0) for w, c in tf.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {w: x / norm for w, x in v.items()}

    def search(self, query: str, k: int = 5) -> list[tuple[Passage, float]]:
        q = self._vec(tokens(query))
        scored = sorted(((sum(w * v.get(t, 0.0) for t, w in q.items()), i) for i, v in enumerate(self.vecs)), reverse=True)[:k]
        return [(self.passages[i], round(s, 3)) for s, i in scored]


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


RAG_SYSTEM = ("You answer questions about the Kubernetes project using ONLY the numbered passages provided. Cite passages like [p2] after each claim. "
              "If the passages do not contain the answer, say \"insufficient evidence\" and do not guess.")


def rag_prompt(question: str, hits: list[tuple[Passage, float]]) -> list[dict]:
    ctx = "\n\n".join(f"[p{i}] ({p.source_url or 'no source'})\n{p.text}" for i, (p, _) in enumerate(hits, 1))
    return [{"role": "system", "content": RAG_SYSTEM}, {"role": "user", "content": f"Passages:\n{ctx}\n\nQuestion: {question}"}]


if __name__ == "__main__":
    r = Retriever(build_corpus())
    print(f"{len(r.passages)} passages")
    for p, s in r.search(" ".join(sys.argv[1:]) or "Who chairs sig-storage?"):
        print(f"{s:.3f} {p.id}: {p.text[:110]}")
