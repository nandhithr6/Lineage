"""Link a docs label (Claim) to the feature gate(s) it is probably about, with a confidence.

Hard-coded labels don't name their gate, so we infer it from the section around the label:
  1. name_mention - the exact gate name appears in the section text
  2. shared_links - section and gate description link to the same docs page (rarer link = stronger)
  3. text_sim     - IDF-weighted cosine between the section and the gate's name + description
Signals are combined with a noisy-OR: 1 - prod(1 - p_i). The claimed stage/version is never used,
otherwise "link by agreement, then flag disagreement" would be circular (CLAUDE.md §5.5).

Run: `uv run python -m lineage.link.linker` -> writes snapshot/links.jsonl
"""
from __future__ import annotations

import json
import logging
import math
import re
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

from lineage.ingest.docs_labels import SNAPSHOT as LABELS_SNAPSHOT
from lineage.ingest.gates import SNAPSHOT as GATES_SNAPSHOT
from lineage.ingest.keps import SNAPSHOT as KEPS_SNAPSHOT

log = logging.getLogger(__name__)

LINKS_SNAPSHOT = Path("snapshot/links.jsonl")
CONFIDENT = 0.8  # >= this: usable for findings. Below: "possible" only (CLAUDE.md §5.4)
KEEP = 0.3  # candidates below this are dropped
MAX_CANDIDATES = 3

# Tunable knobs, kept in one place so they are easy to explain and to report.
PARAMS = {
    "name_mention": 0.9,  # exact gate name near the words "feature gate"
    "name_bare": 0.5,  # exact gate name anywhere else (many gate names are also ordinary API object names)
    "page_link_factor": 0.6,  # a link shared at page level counts for this fraction of a section-level one
    "heading_weight": 3,  # heading words count this many times in the section's word list
    # KEP signal: section resembles a KEP's title + summary, and that KEP introduces the gate (DocSection -> KEP -> Gate)
    # (chosen by grid search on page-half A, precision >= 0.95 with the name signal both on and off; B held out)
    "kep_floor": 0.05,
    "kep_scale": 0.25,
    "kep_max": 0.8,
    "ambiguity_gap": 0.1,  # top two candidates closer than this: neither is confident
    "link_by_gate_count": [(1, 0.5), (2, 0.4), (4, 0.25), (10**9, 0.1)],  # (max gates sharing the link, weight); a shared link alone must never look confident
    # Text-signal calibration, chosen by grid search on page-half A of the known-gate claims with the name
    # signal switched off (most hard-coded sections never name a gate); precision-first setting; half B
    # is the held-out check. See eval/score_links.py.
    "text_floor": 0.10,  # similarity below this contributes nothing
    "text_scale": 0.35,  # similarity range mapped onto 0..text_max
    "text_max": 0.9,
}

_STOP = set(
    "the a an and or of to in on for with is are be this that it as by at from can will not if when "
    "you your use used using which what how also more than then into any all one such may must should "
    "has have had been these those their there where while about over only other some each per via".split()
)
# Independent kinds of evidence. "Confident" needs at least two different families.
FAMILY = {"name_mention": "name", "shared_links": "links", "page_links": "links", "text_sim": "text", "kep": "kep"}
_WORD = re.compile(r"[a-z][a-z0-9]+")
_CAMEL = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")
_SHORTCODE = re.compile(r"\{\{<.*?>\}\}")
_GATE_WORDS = re.compile(r"feature[\s-]*gates?", re.I)
_MD_TARGET = re.compile(r"\]\([^)]*\)")


@dataclass(frozen=True)
class Candidate:
    gate: str
    confidence: float
    signals: dict  # which signals fired and their strength; shown to the user as the "why"
    via: str | None = None  # the KEP the "kep" signal came through, for the evidence path


def camel_words(name: str) -> list[str]:
    return [w.lower() for w in _CAMEL.findall(name)]


def tokens(text: str) -> list[str]:
    text = _MD_TARGET.sub("]", _SHORTCODE.sub(" ", text))
    return [w for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 2]


def norm_link(link: str) -> str:
    return link.rstrip("/").lower()


class Linker:
    def __init__(self, gates: list[dict], params: dict | None = None, keps: list[dict] | None = None):
        self.p = {**PARAMS, **(params or {})}
        self.gates = gates
        self.gate_tokens = {g["name"]: camel_words(g["name"]) * 2 + tokens(g["description"]) for g in gates}
        self.link_gates: dict[str, set[str]] = {}
        for g in gates:
            for link in g["doc_links"]:
                self.link_gates.setdefault(norm_link(link), set()).add(g["name"])
        df: Counter[str] = Counter()
        for toks in self.gate_tokens.values():
            df.update(set(toks))
        n = len(gates)
        self.idf = {t: math.log((n + 1) / (c + 1)) + 1 for t, c in df.items()}
        self.gate_vec = {name: self._vec(toks) for name, toks in self.gate_tokens.items()}
        self.keps = keps or []
        self.kep_vec: list[dict[str, float]] = []
        self.gate_keps: dict[str, list[tuple[int, float]]] = {}  # gate -> [(kep index, INTRODUCES confidence)]
        if self.keps:
            kep_tokens = [tokens(k["title"]) * 3 + tokens(k["summary"]) for k in self.keps]
            kdf: Counter[str] = Counter()
            for toks in kep_tokens:
                kdf.update(set(toks))
            self.kep_idf = {w: math.log((len(self.keps) + 1) / (c + 1)) + 1 for w, c in kdf.items()}
            self.kep_vec = [self._kvec(toks) for toks in kep_tokens]
            for i, k in enumerate(self.keps):
                for gate in k["feature_gates"]:
                    self.gate_keps.setdefault(gate, []).append((i, 1.0))
                for m in k["readme_gates"]:
                    self.gate_keps.setdefault(m["gate"], []).append((i, m["confidence"]))
        self.name_re = {g["name"]: re.compile(rf"(?<![A-Za-z0-9]){re.escape(g['name'])}(?![A-Za-z0-9])") for g in gates}

    def _kvec(self, toks: list[str]) -> dict[str, float]:
        tf = Counter(toks)
        v = {w: (1 + math.log(c)) * self.kep_idf.get(w, math.log(len(self.keps) + 1) + 1) for w, c in tf.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {w: x / norm for w, x in v.items()}

    def _vec(self, toks: list[str]) -> dict[str, float]:
        tf = Counter(toks)
        v = {t: (1 + math.log(c)) * self.idf.get(t, math.log(len(self.gates) + 1) + 1) for t, c in tf.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {t: x / norm for t, x in v.items()}

    def _name_signal(self, name: str, text: str) -> float:
        """0.9 if the name sits within 60 characters of "feature gate", 0.5 for any other exact mention."""
        best = 0.0
        for m in self.name_re[name].finditer(text):
            window = text[max(0, m.start() - 60) : m.end() + 60]
            best = max(best, self.p["name_mention"] if _GATE_WORDS.search(window) else self.p["name_bare"])
        return best

    def link(self, section_text: str, section_links: list[str], heading: str | None = None,
             page_links: list[str] | None = None) -> list[Candidate]:
        text_vec = self._vec(tokens(section_text) + tokens(heading or "") * (self.p["heading_weight"] - 1))
        links = {norm_link(link) for link in section_links}
        page_set = {norm_link(link) for link in (page_links or [])} - links
        out: list[Candidate] = []
        kep_sims: list[float] = []
        if self.keps:
            kv = self._kvec(tokens(section_text) + tokens(heading or "") * (self.p["heading_weight"] - 1))
            kep_sims = [sum(w * v.get(t, 0.0) for t, w in kv.items()) for v in self.kep_vec]
        for g in self.gates:
            name = g["name"]
            signals: dict[str, float] = {}
            strength = self._name_signal(name, section_text)
            if strength:
                signals["name_mention"] = strength
            for key, group, factor in (("shared_links", links, 1.0), ("page_links", page_set, self.p["page_link_factor"])):
                shared = {lk for lk in group if name in self.link_gates.get(lk, ())}
                best = 0.0
                for lk in shared:
                    n_gates = len(self.link_gates[lk])
                    best = max(best, factor * next(w for cap, w in self.p["link_by_gate_count"] if n_gates <= cap))
                if best:
                    signals[key] = best
            via = None
            if kep_sims:
                best_k = 0.0
                for i, edge_conf in self.gate_keps.get(name, ()):
                    s = min(1.0, max(0.0, (kep_sims[i] - self.p["kep_floor"]) / self.p["kep_scale"])) * self.p["kep_max"] * edge_conf
                    if s > best_k:
                        best_k, via = s, self.keps[i]["id"]
                if best_k > 0:
                    signals["kep"] = best_k
            gv = self.gate_vec[name]
            sim = sum(w * gv.get(t, 0.0) for t, w in text_vec.items())
            if sim > self.p["text_floor"]:
                signals["text_sim"] = min(self.p["text_max"], (sim - self.p["text_floor"]) / self.p["text_scale"] * self.p["text_max"])
            if signals:
                conf = 1 - math.prod(1 - s for s in signals.values())
                if len({FAMILY[k] for k in signals}) < 2:
                    conf = min(conf, CONFIDENT - 0.01)  # one kind of evidence alone is never "confident"
                if conf >= KEEP:
                    out.append(Candidate(name, round(conf, 3), {k: round(v, 3) for k, v in signals.items()}, via))
        out.sort(key=lambda c: -c.confidence)
        out = out[:MAX_CANDIDATES]
        if out and out[0].confidence >= CONFIDENT:
            close = [c for c in out if out[0].confidence - c.confidence < self.p["ambiguity_gap"]]
            if len(close) > 1:  # a near-tie is not a confident link for either candidate
                capped = {c.gate: Candidate(c.gate, round(CONFIDENT - 0.01, 3), {**c.signals, "ambiguous_with": [x.gate for x in close if x is not c]}, c.via) for c in close}
                out = [capped.get(c.gate, c) for c in out]
        return out


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def link_all(labels: list[dict], gates: list[dict], params: dict | None = None, keps: list[dict] | None = None) -> list[dict]:
    linker = Linker(gates, params, keps)
    rows = []
    for lab in labels:
        if lab["kind"] != "hardcoded":
            continue
        cands = linker.link(lab["section_text"], lab["doc_links"], lab["heading"], lab["page_doc_links"])
        rows.append({"claim_id": lab["id"], "candidates": [asdict(c) for c in cands]})
    return rows


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    gates, labels = read_jsonl(GATES_SNAPSHOT), read_jsonl(LABELS_SNAPSHOT)
    keps = read_jsonl(KEPS_SNAPSHOT) if KEPS_SNAPSHOT.exists() else None
    rows = link_all(labels, gates, keps=keps)
    LINKS_SNAPSHOT.write_text("".join(json.dumps(r) + "\n" for r in rows))
    confident = sum(1 for r in rows if r["candidates"] and r["candidates"][0]["confidence"] >= CONFIDENT)
    possible = sum(1 for r in rows if r["candidates"] and r["candidates"][0]["confidence"] < CONFIDENT)
    log.info("hard-coded claims: %d | confident (>=%.1f): %d | possible only: %d | unresolved: %d",
             len(rows), CONFIDENT, confident, possible, len(rows) - confident - possible)


if __name__ == "__main__":
    main()
