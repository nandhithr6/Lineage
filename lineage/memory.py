"""Lightweight memory: an answer that passed the validator can be saved as a Note linked to the entities it is about, and shown first next time.

    (:Note {id, text, question, at, session, evidence_ids, evidence_sig, tool_calls})-[:ABOUT_ENTITY {label, key, recorded_at, extracted_by: 'user'}]->(entity)

What makes this safe to trust:
  * only an answer the server itself produced AND the validator accepted can be saved (the client sends an answer id, never text), so a note cannot be forged or hold an unverified claim;
  * a note is private to the session that saved it (the blog's per-tenant isolation, done with a property filter because a Space has one graph) and can be forgotten;
  * a note is never an answer on its own: when it is used, the tools it was based on are RE-RUN against the graph now and the evidence compared with the evidence it was saved with
    (`reverify`): unchanged, changed, or unavailable. The date it was learned is always shown, so a reader can weigh its age.
The graph in a single-container deploy is rebuilt on every start, so notes there last until the container restarts; with the compose volume they persist.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone

from falkordb import Graph

from lineage.graph.paths import LABEL_KEY

NOTE_LIMIT_PER_SESSION = 20
TEXT_MAX = 4000
LABEL_PROP = {**LABEL_KEY, "Release": "version", "Finding": "key", "Chunk": "id"}  # which property identifies a node of each label
_REF = re.compile(r"^([A-Za-z]+):(.+)$")


def entity_refs(evidence: list[dict]) -> list[tuple[str, str]]:
    """The graph entities an answer's evidence names ("FeatureGate:X", "CodePath:kubernetes:pkg/x", ...), deduplicated, skipping labels we cannot look up."""
    seen: dict[tuple[str, str], None] = {}
    for e in evidence:
        for end in (e.get("from"), e.get("to")):
            m = _REF.match(end or "")
            if m and m.group(1) in LABEL_PROP and m.group(1) not in ("Chunk", "Finding") and not (m.group(1) == "Release" and not re.fullmatch(r"\d+\.\d+", m.group(2))):
                seen[(m.group(1), m.group(2))] = None  # stage ranges such as "Release:1.7..1.9" are not a single release node
    return list(seen)


def evidence_signature(evidence: list[dict]) -> list[list]:
    """Stable fingerprint of what an answer rested on: (type, from, to, source_url) per row. Evidence ids are excluded (they depend on call order)."""
    return sorted([[e.get("type"), e.get("from"), e.get("to"), e.get("source_url")] for e in evidence], key=lambda r: json.dumps(r, default=str))


def entities_in_text(g: Graph, text: str) -> list[tuple[str, str]]:
    """Entities a question mentions: feature gates, teams, folders and KEPs by exact name, people only as @handle. Read-only; unknown words are ignored."""
    from lineage.agent.resolve import names, resolve_kep

    out: dict[tuple[str, str], None] = {}
    gates = {n.lower(): n for n in names(g, "gate")}
    sigs = set(names(g, "sig"))
    paths = {i.split(":", 1)[1]: i for i in names(g, "path") if i.startswith("kubernetes:") and i.split(":", 1)[1]}
    people = {p.lower(): p for p in names(g, "person")}
    for tok in re.findall(r"[@A-Za-z0-9_./:-]+", text):
        t = tok.strip(".,;:!?)('\"`")
        if t.lower() in gates:
            out[("FeatureGate", gates[t.lower()])] = None
        elif t.lower() in sigs:
            out[("SIG", t.lower())] = None
        elif "/" in t and t.strip("/") in paths:
            out[("CodePath", paths[t.strip("/")])] = None
        elif t.startswith("@") and t[1:].lower() in people:
            out[("Person", people[t[1:].lower()])] = None
    for m in re.finditer(r"\bKEP[-\s#:]{0,3}(\d{2,5})\b", text, re.I):
        r = resolve_kep(g, m.group(1))
        if r.ok:
            out[("KEP", r.key)] = None
    return list(out)


def can_save(response: dict) -> tuple[bool, str]:
    """Only a validated, evidence-backed graph answer can be remembered."""
    if response.get("mode", "graph") != "graph":
        return False, "only graph answers can be remembered, not plain retrieval answers"
    if response.get("status") != "ok" or not response.get("evidence"):
        return False, "only answers backed by evidence can be remembered"
    if (response.get("validation") or {}).get("accepted") is False:
        return False, "the validator rejected the model's wording, so this answer cannot be remembered"
    if response.get("outcome") not in ("answered_with_path",):
        return False, f"an answer with outcome {response.get('outcome')} is not remembered"
    return True, ""


def save_note(g: Graph, response: dict, question: str, session_id: str, now: datetime | None = None) -> dict:
    ok, why = can_save(response)
    if not ok:
        raise ValueError(why)
    if g.ro_query("MATCH (n:Note {session: $s}) RETURN count(n)", {"s": session_id}).result_set[0][0] >= NOTE_LIMIT_PER_SESSION:
        raise ValueError(f"at most {NOTE_LIMIT_PER_SESSION} notes per session; forget one first")
    now = now or datetime.now(timezone.utc)
    refs = entity_refs(response["evidence"])
    note = {"id": uuid.uuid4().hex[:16], "text": response["answer"][:TEXT_MAX], "question": question[:300], "at": now.isoformat(timespec="seconds"), "session": session_id,
            "evidence_ids": [c["id"] for c in response["citations"]], "evidence_sig": json.dumps(evidence_signature(response["evidence"])),
            "tool_calls": json.dumps([{"tool": t["tool"], "args": t["params"]} for t in response.get("tool_trace", []) if t.get("outcome") == "answered_with_path"])}
    g.query("CREATE (n:Note {id: $id, text: $text, question: $question, at: $at, session: $session, evidence_ids: $evidence_ids, evidence_sig: $evidence_sig, tool_calls: $tool_calls, "
            "source: 'user_saved', validated: true})", note)
    for label, key in refs:
        g.query(f"MATCH (n:Note {{id: $id}}), (e:{label} {{{LABEL_PROP[label]}: $key}}) CREATE (n)-[:ABOUT_ENTITY {{label: $label, key: $key, recorded_at: $at, extracted_by: 'user'}}]->(e)",
                {"id": note["id"], "key": key, "label": label, "at": note["at"]})
    return {**{k: note[k] for k in ("id", "text", "question", "at", "evidence_ids")}, "about": [f"{label}:{key}" for label, key in refs]}


def _note_row(r: list) -> dict:
    return {"id": r[0], "text": r[1], "question": r[2], "at": r[3], "evidence_ids": r[4], "evidence_sig": json.loads(r[5] or "[]"), "tool_calls": json.loads(r[6] or "[]")}


NOTE_COLS = "n.id, n.text, n.question, n.at, n.evidence_ids, n.evidence_sig, n.tool_calls"


def recall(g: Graph, refs: list[tuple[str, str]], session_id: str | None, limit: int = 3) -> list[dict]:
    """This session's notes about any of `refs`, newest first. No session, no memory."""
    if not session_id or not refs:
        return []
    rows = g.ro_query(f"MATCH (n:Note {{session: $s}})-[r:ABOUT_ENTITY]->(e) WHERE (r.label + ':' + r.key) IN $refs RETURN DISTINCT {NOTE_COLS} ORDER BY n.at DESC LIMIT $limit",
                      {"s": session_id, "refs": [f"{a}:{b}" for a, b in refs], "limit": limit}).result_set
    return [_note_row(r) for r in rows]


def list_notes(g: Graph, session_id: str) -> list[dict]:
    rows = g.ro_query(f"MATCH (n:Note {{session: $s}}) RETURN {NOTE_COLS} ORDER BY n.at DESC", {"s": session_id}).result_set
    out = []
    for r in rows:
        note = _note_row(r)
        note["about"] = [f"{a}:{b}" for a, b in g.ro_query("MATCH (n:Note {id: $id})-[r:ABOUT_ENTITY]->(e) RETURN r.label, r.key", {"id": note["id"]}).result_set]
        out.append({k: note[k] for k in ("id", "text", "question", "at", "evidence_ids", "about")})
    return out


def forget(g: Graph, note_id: str, session_id: str) -> bool:
    n = g.ro_query("MATCH (n:Note {id: $id, session: $s}) RETURN count(n)", {"id": note_id, "s": session_id}).result_set[0][0]
    if n:
        g.query("MATCH (n:Note {id: $id, session: $s}) DETACH DELETE n", {"id": note_id, "s": session_id})
    return bool(n)


def reverify(tools, note: dict) -> dict:
    """Re-run the tools the note was based on (no language model) and compare the evidence with what the note rested on."""
    if not note["tool_calls"]:
        return {"status": "unavailable", "detail": "the note records no tool calls to repeat"}
    fresh: list[dict] = []
    for call in note["tool_calls"]:
        b = tools.call(call["tool"], call["args"])
        if b.get("outcome") != "answered_with_path":
            return {"status": "changed", "detail": f"{call['tool']} now returns {b.get('outcome')}: the entities it was about are no longer in the graph or no longer connected", "added": 0, "removed": len(note["evidence_sig"])}
        fresh += b["evidence"]
    old = {json.dumps(r, default=str) for r in note["evidence_sig"]}
    new = {json.dumps(r, default=str) for r in evidence_signature(fresh)}
    if old == new:
        return {"status": "unchanged", "detail": "the same tools return the same evidence now", "added": 0, "removed": 0}
    return {"status": "changed", "detail": f"the graph now returns different evidence ({len(new - old)} rows new, {len(old - new)} gone)", "added": len(new - old), "removed": len(old - new)}
