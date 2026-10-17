"""Name resolution: every entity a tool is asked about is looked up in the graph, with a read-only parameterised query, BEFORE any traversal runs.

A model can invent a feature gate, a folder or a person, or misspell a real one. If the name does not resolve, the tool does not run and the outcome is `unknown_entity`:
the answer is "I do not know X", never an empty traversal that sounds like "nothing there". Close matches are offered as TEXT ("did you mean ...") and are never substituted:
the only normalisation applied is the one that keeps the same name (letter case, a missing `sig-` prefix, a leading `v` on a release), and the trace shows the canonical name that was used.
Model output is never concatenated into a query: every lookup passes the name as a parameter.
"""
from __future__ import annotations

import difflib
import time
from dataclasses import dataclass, field

from falkordb import Graph

LATEST_NUM = 138  # "latest" = the HEAD sample (1.38-dev); see lineage.graph.queries.LATEST
KINDS = ("gate", "kep", "sig", "team", "person", "path", "release", "page", "finding", "kep_or_gate", "gate_or_path", "entity")


@dataclass
class Resolution:
    kind: str
    given: str
    ok: bool
    canonical: str | None = None
    label: str | None = None  # graph label of what was found (for "entity" lookups)
    suggestions: list[str] = field(default_factory=list)
    note: str | None = None
    key: str | None = None  # the value of the node's identifying property (gate name, handle, folder id, ...), for lookups by label

    def as_dict(self, argument: str) -> dict:
        return {"argument": argument, "kind": self.kind, "given": self.given, "did_you_mean": self.suggestions, "note": self.note}


# ---- names for "did you mean" (one read-only query per kind, cached briefly per graph) -----------------------------------------------------
_NAMES: dict[tuple[str, str], tuple[float, list[str]]] = {}
_NAME_QUERIES = {
    "gate": "MATCH (n:FeatureGate) RETURN n.name",
    "kep": "MATCH (n:KEP) RETURN n.id",
    "sig": "MATCH (n:SIG) RETURN n.id",
    "team": "MATCH (n:Team) RETURN n.id",
    "person": "MATCH (n:Person) RETURN n.handle",
    "path": "MATCH (n:CodePath) RETURN n.id",
    "page": "MATCH (n:DocPage) RETURN n.path",
}


def names(g: Graph, kind: str, ttl: int = 300) -> list[str]:
    hit = _NAMES.get((g.name, kind))
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    out = [r[0] for r in g.ro_query(_NAME_QUERIES[kind]).result_set if r[0]]
    _NAMES[(g.name, kind)] = (time.time(), out)
    return out


def clear_cache() -> None:
    _NAMES.clear()


def close_matches(given: str, pool: list[str], n: int = 3) -> list[str]:
    """Names that look like `given`, best first: those that contain it, and those difflib finds similar (ratio >= 0.6). Text only."""
    g = given.lower()
    cands = [p for p in pool if g and len(g) >= 3 and g in p.lower()] + difflib.get_close_matches(given, pool, n=n * 3, cutoff=0.6)
    scored = {c: difflib.SequenceMatcher(None, g, c.lower()).ratio() for c in dict.fromkeys(cands)}
    return sorted(scored, key=lambda c: (-scored[c], c))[:n]


def _unknown(kind: str, given: str, pool: list[str], note: str | None = None) -> Resolution:
    return Resolution(kind, given, False, suggestions=close_matches(given, pool), note=note or f"no {kind} named {given!r} exists in the graph")


# ---- one resolver per kind -----------------------------------------------------------------------------------------------------------
def resolve_gate(g: Graph, name: str) -> Resolution:
    row = g.ro_query("MATCH (n:FeatureGate) WHERE toLower(n.name) = toLower($n) RETURN n.name", {"n": name}).result_set
    return Resolution("gate", name, True, row[0][0], "FeatureGate", key=row[0][0]) if row else _unknown("gate", name, names(g, "gate"))


def resolve_sig(g: Graph, name: str) -> Resolution:
    low = name.strip().lower()
    for cand in dict.fromkeys([low, low if low.startswith("sig-") else f"sig-{low}"]):
        row = g.ro_query("MATCH (n:SIG {id: $n}) RETURN n.id", {"n": cand}).result_set
        if row:
            return Resolution("sig", name, True, row[0][0], "SIG", key=row[0][0])
    return _unknown("sig", name, names(g, "sig"))


def resolve_team(g: Graph, name: str) -> Resolution:
    row = g.ro_query("MATCH (n:Team) WHERE toLower(n.id) = toLower($n) RETURN n.id", {"n": name}).result_set
    return Resolution("team", name, True, row[0][0], "Team", key=row[0][0]) if row else _unknown("team", name, names(g, "team"))


def resolve_person(g: Graph, handle: str) -> Resolution:
    row = g.ro_query("MATCH (n:Person {handle: $h}) RETURN n.handle", {"h": handle.strip().lstrip("@").lower()}).result_set
    return Resolution("person", handle, True, row[0][0], "Person", key=row[0][0]) if row else _unknown("person", handle, names(g, "person"))


def resolve_kep(g: Graph, value: str) -> Resolution:
    """A KEP by id (`sig-storage/361-...`), by number (`361`, `KEP-361`) or by a title fragment; several KEPs can share a number, the canonical value is the first id."""
    v = value.strip()
    row = g.ro_query("MATCH (n:KEP {id: $v}) RETURN n.id", {"v": v}).result_set
    if not row:
        digits = v.lower().replace("kep", "").strip(" -#:")
        if digits.isdigit():
            row = g.ro_query("MATCH (n:KEP {number: $n}) RETURN n.id ORDER BY n.id", {"n": int(digits)}).result_set
    if not row and len(v) >= 4:
        row = g.ro_query("MATCH (n:KEP) WHERE toLower(n.title) CONTAINS toLower($v) OR toLower(n.id) CONTAINS toLower($v) RETURN n.id ORDER BY n.id LIMIT 5", {"v": v}).result_set
    return Resolution("kep", value, True, row[0][0], "KEP", key=row[0][0]) if row else _unknown("kep", value, names(g, "kep"))


def resolve_release(g: Graph, value: str | None) -> Resolution:
    """A release must exist in the graph (or be "latest"). A release in the future does not exist: unknown_entity, with the reason stated."""
    from lineage.agent.tools import release_num  # local import: tools imports this module

    if value in (None, "", "latest", "now", "current"):
        return Resolution("release", value or "latest", True, "latest", "Release")
    try:
        num = release_num(value)
    except (ValueError, TypeError):
        return Resolution("release", str(value), False, note=f"{value!r} is not a release number like 1.30")
    if num == LATEST_NUM or g.ro_query("MATCH (r:Release {num: $n}) RETURN r.version", {"n": num}).result_set:
        return Resolution("release", str(value), True, f"1.{num % 100}" if num != LATEST_NUM else "latest", "Release")
    newest = g.ro_query("MATCH (r:Release) RETURN max(r.num)").result_set[0][0]
    why = (f"release {value} is newer than the data (newest release recorded: 1.{newest % 100}; the latest commit is 1.38-dev): the future cannot be known" if num > newest
           else f"release {value} is older than the releases in the graph")
    return Resolution("release", str(value), False, note=why)


def resolve_path(g: Graph, path: str, repo: str = "kubernetes") -> Resolution:
    """A folder (or a docs file) that the graph holds. Folders outside the in-scope SIG paths are not loaded, so they are unknown, not 'empty'."""
    from lineage.agent.tools import clean_path

    p = clean_path(path)
    if repo not in ("kubernetes", "website"):
        return Resolution("path", path, False, note="repo must be 'kubernetes' or 'website'")
    for cand in (f"{repo}:{p}", f"{repo}:{p.rsplit('/', 1)[0]}" if p.endswith((".md", ".go", ".yaml", ".py")) and "/" in p else None):
        if cand and g.ro_query("MATCH (n:CodePath {id: $i}) RETURN n.id", {"i": cand}).result_set:
            return Resolution("path", path, True, p, "CodePath", key=cand)
    if repo == "website" and g.ro_query("MATCH (n:DocPage {path: $p}) RETURN n.path", {"p": p}).result_set:
        return Resolution("path", path, True, p, "DocPage", key=p)
    pool = [i.split(":", 1)[1] for i in names(g, "path") if i.startswith(f"{repo}:")]
    ancestors = [a for a in (p.rsplit("/", k)[0] for k in range(1, p.count("/") + 1)) if a in set(pool)]
    r = _unknown("path", path, pool, f"no folder {p!r} in {repo} is loaded (only the in-scope SIG folders and the docs tree are)")
    if ancestors:
        r.suggestions = list(dict.fromkeys(r.suggestions + [ancestors[0]]))[:3]
    return r


def resolve_page(g: Graph, text: str) -> Resolution:
    """A docs page (path or title fragment) or a section heading."""
    label = "DocPage"
    row = g.ro_query("MATCH (p:DocPage) WHERE toLower(p.path) CONTAINS toLower($t) OR toLower(p.title) CONTAINS toLower($t) RETURN p.path ORDER BY size(p.path) LIMIT 1", {"t": text.strip()}).result_set
    if not row:
        label = "DocSection"
        row = g.ro_query("MATCH (s:DocSection) WHERE toLower(s.heading) CONTAINS toLower($t) RETURN s.id LIMIT 1", {"t": text.strip()}).result_set
    return Resolution("page", text, True, text, label, key=row[0][0]) if row and text.strip() else _unknown("page", text, names(g, "page"))


def resolve_finding(g: Graph, text: str) -> Resolution:
    """A finding is addressed by part of its page path or by its key; the page (or the key) must exist."""
    if text.strip() and g.ro_query("MATCH (f:Finding) WHERE f.key CONTAINS $t RETURN f.key LIMIT 1", {"t": text.strip()}).result_set:
        return Resolution("finding", text, True, text, "Finding")
    r = resolve_page(g, text)
    return Resolution("finding", text, r.ok, text if r.ok else None, "DocPage", r.suggestions, r.note)


def resolve_kep_or_gate(g: Graph, value: str) -> Resolution:
    r = resolve_gate(g, value)
    if r.ok:
        return Resolution("kep_or_gate", value, True, r.canonical, "FeatureGate")
    k = resolve_kep(g, value)
    if k.ok:
        return Resolution("kep_or_gate", value, True, value, "KEP")
    return Resolution("kep_or_gate", value, False, suggestions=list(dict.fromkeys(r.suggestions + k.suggestions))[:3], note=f"no feature gate or design decision (KEP) matches {value!r}")


def resolve_gate_or_path(g: Graph, value: str, repo: str = "kubernetes") -> Resolution:
    r = resolve_gate(g, value)
    if r.ok:
        return Resolution("gate_or_path", value, True, r.canonical, "FeatureGate")
    p = resolve_path(g, value, repo)
    if p.ok:
        return Resolution("gate_or_path", value, True, p.canonical, p.label)
    return Resolution("gate_or_path", value, False, suggestions=list(dict.fromkeys(r.suggestions + p.suggestions))[:3], note=f"no feature gate and no folder named {value!r}")


def resolve_entity(g: Graph, value: str, repo: str = "kubernetes") -> Resolution:
    """Any node of the knowledge graph, for path_between: gate, person, team, SIG, KEP, folder, docs page."""
    for fn in (resolve_gate, resolve_person, resolve_sig, resolve_team, resolve_kep, lambda gg, v: resolve_path(gg, v, repo), resolve_page):
        r = fn(g, value)
        if r.ok:
            return Resolution("entity", value, True, r.canonical, r.label, key=r.key)
    pool = names(g, "gate") + names(g, "sig") + names(g, "person")
    return Resolution("entity", value, False, suggestions=close_matches(value, pool), note=f"nothing in the graph is named {value!r}")


RESOLVERS = {"gate": resolve_gate, "sig": resolve_sig, "team": resolve_team, "person": resolve_person, "kep": resolve_kep, "release": resolve_release, "page": resolve_page,
             "finding": resolve_finding, "kep_or_gate": resolve_kep_or_gate}


# ---- the question itself ---------------------------------------------------------------------------------------------------------------
# A language model tends to FIX a typo before it calls a tool (it types LocalStorageCapacityIsolation when the user wrote LocalStorageCapacityIsolatio), which is a silent substitution,
# and to DROP a time the user asked about (it answers "who approves X" with today's owners when asked about release 1.45). So the names and releases in the question are resolved
# up front, in code, before the model is called: a release that does not exist, or a gate name that is one slip away from a real one, ends the answer as unknown_entity.
import re  # noqa: E402

_Q_RELEASE = re.compile(r"(?<![\w.])v?(1\.\d{1,2})(?!\d)(?!\.\d)")
_Q_CAMEL = re.compile(r"(?<![\w])([A-Z][a-z0-9]+(?:[A-Z][A-Za-z0-9]*)+)(?![\w])")
TYPO_MIN_LEN, TYPO_MIN_RATIO = 12, 0.90


def preflight(g: Graph, question: str) -> list[dict]:
    """Unresolved names and releases in the question, as dicts like Resolution.as_dict (empty when everything resolves). Read-only; never substitutes."""
    out: list[dict] = []
    for ver in dict.fromkeys(_Q_RELEASE.findall(question)):
        r = resolve_release(g, ver)
        if not r.ok:
            out.append(r.as_dict("question"))
    long_tokens = [t for t in dict.fromkeys(_Q_CAMEL.findall(question)) if len(t) >= TYPO_MIN_LEN]
    if not long_tokens:
        return out  # nothing that could be a mistyped gate name: do not even touch the graph
    gates = names(g, "gate")
    known = {n.lower() for n in gates}
    for tok in long_tokens:
        if tok.lower() in known:
            continue
        best = difflib.get_close_matches(tok, gates, n=3, cutoff=TYPO_MIN_RATIO)
        if best:
            out.append(Resolution("gate", tok, False, suggestions=best, note=f"no feature gate named {tok!r} exists in the graph").as_dict("question"))
    return out


def unknown_entity_answer(unresolved: list[dict], searched: str = "nothing was looked up") -> str:
    said = "; ".join(f"I do not know a {u['kind']} called {u['given']!r}" + (f" (did you mean: {', '.join(u['did_you_mean'])}?)" if u["did_you_mean"] else "")
                     + (f": {u['note']}" if u["kind"] == "release" and u.get("note") else "") for u in unresolved)
    return f"Unknown entity: {said}. No lookup was run for it, so I am not guessing. What I searched: {searched}."
