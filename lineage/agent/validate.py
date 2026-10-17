"""Answer validator: after the model has written an answer, check that every concrete thing it names is in the evidence the tools returned.

The model is trusted to choose tools and to phrase; it is NOT trusted to know facts. So, from its answer we extract
  * feature-gate-like names (CamelCase identifiers),   * GitHub handles (@mentions, names after "approvers:/reviewers were ...", and any known person),
  * versions (v1.25, 1.37.0),   * KEP numbers (KEP-361),   * repository paths (pkg/controller/job),   * URLs
and look each one up in the evidence (and in the question, which the user wrote). One that is in neither is a claim with no source: the whole answer is rejected and replaced by a
template assembled from the evidence rows themselves (`template_answer`). Rejections are logged.
This is deliberately blunt: a false rejection costs a plainer answer; a false acceptance costs a fabricated fact.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from falkordb import Graph

log = logging.getLogger("lineage.validator")

_CITE = re.compile(r"\[\s*e\d+(?:\s*[,;]\s*e\d+)*\s*\]")
_URL = re.compile(r"https?://[^\s)\]>\"'`]+")
_VERSION = re.compile(r"(?<![\w.])v?(1\.\d{1,2}(?:\.\d+)?)(?![\w]|\.\d)")
_KEP = re.compile(r"\bKEP[-\s#:]{0,3}(\d{2,5})\b", re.I)
_PATH = re.compile(r"(?<![\w/.-])((?:pkg|staging|cmd|test|content|hack|vendor|plugin|build|cluster|api|docs|third_party)/[A-Za-z0-9_./-]*[A-Za-z0-9_])")
_CAMEL = re.compile(r"(?<![\w])([A-Z][A-Za-z0-9]{5,}|[A-Z]{2,}[a-z][A-Za-z0-9]*)(?![\w])")
_MENTION = re.compile(r"(?<![\w@])@([A-Za-z0-9][A-Za-z0-9-]{1,38})\b")
_ROLE_LIST = re.compile(r"\b(?:approvers?|reviewers?|owners?|experts?|chairs?|tech leads?|leads?|authors?|maintainers?)\b[^.\n:]{0,90}?(?::|\b(?:were|are|was|is)\b)[\s:]*"
                        r"((?:[`*]*[a-z0-9][a-z0-9-]{1,38}[`*]*(?:\s*(?:,|and|&)\s*)?)+)", re.I)
_HANDLE = re.compile(r"[a-z0-9][a-z0-9-]{1,38}")
# Words that are product vocabulary rather than facts (never treated as an invented gate), and words that can follow a role word.
PRODUCT_WORDS = {"Kubernetes", "GitHub", "FeatureGate", "FeatureGates", "DocSection", "DocPage", "CodePath", "OWNERS", "OWNERS_ALIASES", "Lineage", "FalkorDB", "ReplicaSet", "DaemonSet", "StatefulSet",
                 "Deployment", "CronJob", "PersistentVolume", "PersistentVolumeClaim", "ResourceQuota", "ConfigMap", "ServiceAccount", "NodePort", "LoadBalancer", "ClusterIP", "PodSpec", "StorageClass",
                 "Insufficient", "InitContainers", "Evidence", "Release", "Person", "Github", "Cypher", "PageRank", "JavaScript", "TypeScript"}
FILLER = {"the", "a", "an", "and", "or", "of", "for", "in", "on", "at", "to", "by", "also", "now", "then", "none", "nobody", "no", "not", "listed", "currently", "still", "both", "these", "those",
          "all", "any", "some", "many", "most", "other", "others", "following", "same", "only", "just", "still", "active", "inactive", "emeritus", "official", "recent", "approvers", "reviewers",
          "owners", "experts", "people", "person", "team", "teams", "folder", "path", "docs", "code", "which", "that", "this", "who", "as", "with", "from", "have", "has", "had", "be", "been"}


@dataclass
class Validation:
    accepted: bool
    rejected: list[dict] = field(default_factory=list)  # [{"kind": "handle", "term": "zorblax"}]
    checked: dict[str, int] = field(default_factory=dict)  # how many of each kind were extracted and found


def flatten(value, out: list[str]) -> None:
    if isinstance(value, dict):
        for k, v in value.items():
            out.append(str(k))
            flatten(v, out)
    elif isinstance(value, (list, tuple)):
        for v in value:
            flatten(v, out)
    elif value is not None:
        out.append(str(value))


def evidence_text(evidence: list[dict]) -> str:
    parts: list[str] = []
    for e in evidence:
        flatten(e, parts)
    return "\n".join(parts).lower()


class AnswerValidator:
    def __init__(self, known_people: set[str] | None = None, vocabulary: set[str] | None = None):
        self.known_people = known_people or set()
        self.vocabulary = vocabulary or set()  # ordinary lowercase words (from the docs corpus), so a word in a list is not mistaken for a handle

    @classmethod
    def from_graph(cls, g: Graph, chunks_path: Path = Path("snapshot/chunks.jsonl")) -> "AnswerValidator":
        people = {r[0] for r in g.ro_query("MATCH (p:Person) RETURN p.handle").result_set if r[0]}
        vocab: set[str] = set()
        if chunks_path.exists():
            for line in chunks_path.read_text().splitlines():
                try:
                    vocab.update(w for w in re.findall(r"[a-z]{3,}", json.loads(line)["text"].lower()))
                except (ValueError, KeyError):
                    continue
        return cls(people, vocab - people)  # a handle that is also a common word (e.g. "dims") must still be checked as a handle

    def extract(self, answer: str, question: str = "") -> dict[str, set[str]]:
        text = _CITE.sub(" ", answer)
        urls = {u.rstrip(".,;:") for u in _URL.findall(text)}
        bare = _URL.sub(" ", text)
        out: dict[str, set[str]] = {
            "url": urls,
            "version": {m for m in _VERSION.findall(bare)},
            "kep": {m for m in _KEP.findall(bare)},
            "path": {m.rstrip("./") for m in _PATH.findall(bare)},
            "gate": {m for m in _CAMEL.findall(bare) if m not in PRODUCT_WORDS and sum(c.isupper() for c in m[1:]) >= 1 and any(c.islower() for c in m)},
            "handle": {m.lower() for m in _MENTION.findall(bare)},
        }
        for m in _ROLE_LIST.finditer(bare):
            for tok in _HANDLE.findall(m.group(1).lower().replace("*", " ").replace("`", " ")):
                if tok not in FILLER and (tok in self.known_people or tok not in self.vocabulary):
                    out["handle"].add(tok)
        words = set(re.findall(r"[a-z0-9][a-z0-9-]{1,38}", bare.lower()))
        out["handle"] |= {w for w in words & self.known_people if w not in FILLER and w not in self.vocabulary}  # a known person named without evidence
        return out

    def validate(self, answer: str, evidence: list[dict], question: str = "") -> Validation:
        blob = evidence_text(evidence)
        asked = question.lower()
        rejected: list[dict] = []
        checked: dict[str, int] = {}
        for kind, terms in self.extract(answer, question).items():
            checked[kind] = len(terms)
            for term in sorted(terms):
                t = term.lower()
                if kind == "kep":
                    found = re.search(rf"(?<!\d){re.escape(t)}(?!\d)", blob) or re.search(rf"(?<!\d){re.escape(t)}(?!\d)", asked)
                elif kind == "url":
                    found = t.rstrip("/") in blob or t.rstrip("/") in asked
                else:
                    found = t in blob or t in asked
                if not found:
                    rejected.append({"kind": kind, "term": term})
        return Validation(not rejected, rejected, checked)


def describe(e: dict) -> str:
    props = {k: v for k, v in (e.get("props") or {}).items() if isinstance(v, (str, int, float, bool)) and v not in ("", None)}
    shown = ", ".join(f"{k}: {str(v)[:70]}" for k, v in list(props.items())[:4])
    ends = f"{e.get('from')}" + (f" → {e['to']}" if e.get("to") else "")
    return f"[{e['id']}] {e.get('type')}: {ends}" + (f" ({shown})" if shown else "") + (f" — {e['source_url']}" if e.get("source_url") else "")


def template_answer(evidence: list[dict], rejected: list[dict], limit: int = 10) -> tuple[str, list[dict]]:
    """The fallback: the evidence rows themselves, in plain text, each with its id and source. Returns (text, the evidence items shown, so they can be cited)."""
    shown = evidence[:limit]
    terms = ", ".join(sorted({r["term"] for r in rejected})[:6])
    head = (f"I could not verify the wording I drafted against the evidence (it named {terms}, which the evidence does not contain), so here are the rows the graph returned instead:")
    body = "\n".join(f"• {describe(e)}" for e in shown)
    more = f"\n(and {len(evidence) - limit} more evidence rows)" if len(evidence) > limit else ""
    return f"{head}\n{body}{more}", shown


_VALIDATORS: dict[str, tuple[float, AnswerValidator]] = {}


def validator_for(g: Graph, ttl: int = 600) -> AnswerValidator:
    hit = _VALIDATORS.get(g.name)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    v = AnswerValidator.from_graph(g)
    _VALIDATORS[g.name] = (time.time(), v)
    return v


def log_rejection(question: str, answer: str, v: Validation) -> None:
    rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "question": question[:300], "rejected": v.rejected, "draft": answer[:500]}
    log.warning("answer rejected by the validator: %s", json.dumps(rec, ensure_ascii=False))
    import os

    path = os.environ.get("LINEAGE_VALIDATOR_LOG")
    if path:
        with open(path, "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
