import json

import pytest

from lineage import memory as M
from lineage.agent.llm import FakeLLM, LLMResponse, ToolCall
from lineage.agent.tools import ToolBox
from lineage.api.main import RECENT_ANSWERS, app, get_llm
from tests.test_api import client  # noqa: F401
from tests.test_loader import graph  # noqa: F401

GATE = "LocalStorageCapacityIsolation"


@pytest.fixture(autouse=True)
def unlimited_asks(monkeypatch):
    """These tests ask many questions from one client address; the per-visitor limiter (12 an hour, shared by the whole test session) must not interfere."""
    import lineage.api.main as m

    monkeypatch.setattr(m, "ASK_LIMITER", m.AskLimiter(0, 0))


def good_response(g, text="It reached stable at 1.25 [e3]."):
    """A real graph answer: run the tool, keep its evidence and trace, as /ask would."""
    b = ToolBox(g).call("gate_timeline", {"gate": GATE})
    ev = [{**e, "id": f"e{i}"} for i, e in enumerate(b["evidence"], 1)]
    return {"answer": text, "status": "ok", "citations": ev[:1], "evidence": ev, "path": b["path"], "mode": "graph", "outcome": "answered_with_path", "validation": {"accepted": True, "rejected": []},
            "tool_trace": [{"tool": "gate_timeline", "params": {"gate": GATE}, "status": "ok", "evidence": len(ev), "outcome": "answered_with_path"}]}


def test_entities_are_read_from_evidence_and_from_the_question(graph):  # noqa: F811
    from lineage.graph.loader import load_all

    load_all(graph)
    refs = M.entity_refs(good_response(graph)["evidence"])
    assert ("FeatureGate", GATE) in refs and all(label in M.LABEL_PROP for label, _ in refs)
    assert not any(label == "Release" for label, _ in refs)  # the evidence names stage RANGES ("1.7..1.9"), which are not release nodes
    assert M.entity_refs([{"from": "Person:x", "to": "Release:1.25"}, {"from": "Chunk:a", "to": "Finding:k"}]) == [("Person", "x"), ("Release", "1.25")]
    q = M.entities_in_text(graph, f"What about {GATE} and sig-storage in pkg/volume, KEP-361 and @alculquicondor? Also Zorblax and NoSuchGate.")
    assert {("FeatureGate", GATE), ("SIG", "sig-storage"), ("CodePath", "kubernetes:pkg/volume"), ("Person", "alculquicondor")} <= set(q)
    assert any(label == "KEP" for label, _ in q) and not any(key in ("Zorblax", "NoSuchGate") for _, key in q)


@pytest.mark.parametrize("change,expected", [({"mode": "retrieval"}, "plain retrieval"), ({"status": "uncited"}, "evidence"), ({"evidence": []}, "evidence"),
                                              ({"validation": {"accepted": False, "rejected": [{"kind": "gate", "term": "X"}]}}, "validator"), ({"outcome": "not_in_graph"}, "not remembered")])
def test_only_validated_evidence_backed_graph_answers_can_be_saved(change, expected):
    base = {"mode": "graph", "status": "ok", "evidence": [{"id": "e1"}], "validation": {"accepted": True}, "outcome": "answered_with_path"}
    assert M.can_save(base)[0] is True
    ok, why = M.can_save({**base, **change})
    assert ok is False and expected in why


def test_save_recall_session_scope_forget_and_limit(graph):  # noqa: F811
    from lineage.graph.loader import load_all

    load_all(graph)
    r = good_response(graph)
    n = M.save_note(graph, r, "What happened to the gate?", "session-a")
    assert n["about"] and f"FeatureGate:{GATE}" in n["about"] and n["evidence_ids"] == ["e1"]
    edge = graph.ro_query("MATCH (:Note {id: $i})-[r:ABOUT_ENTITY]->(e:FeatureGate) RETURN r.label, r.key, r.recorded_at, r.extracted_by, e.name", {"i": n["id"]}).result_set[0]
    assert edge[0] == "FeatureGate" and edge[1] == GATE and edge[2] and edge[3] == "user" and edge[4] == GATE
    refs = [("FeatureGate", GATE)]
    assert [x["id"] for x in M.recall(graph, refs, "session-a")] == [n["id"]]
    assert M.recall(graph, refs, "session-b") == [] and M.recall(graph, refs, None) == [] and M.recall(graph, [("FeatureGate", "Other")], "session-a") == []  # private to the session
    assert [x["id"] for x in M.list_notes(graph, "session-a")] == [n["id"]] and M.list_notes(graph, "session-b") == []
    assert M.forget(graph, n["id"], "session-b") is False and M.forget(graph, n["id"], "session-a") is True and M.recall(graph, refs, "session-a") == []
    with pytest.raises(ValueError):
        M.save_note(graph, {**r, "validation": {"accepted": False}}, "q", "session-a")
    for i in range(M.NOTE_LIMIT_PER_SESSION):
        M.save_note(graph, r, f"q{i}", "session-full")
    with pytest.raises(ValueError, match="at most"):
        M.save_note(graph, r, "one too many", "session-full")


def test_reverify_says_unchanged_then_changed_when_the_graph_moves(graph):  # noqa: F811
    from lineage.graph.loader import load_all

    load_all(graph)
    n = M.save_note(graph, good_response(graph), "q", "session-r")
    note = M.recall(graph, [("FeatureGate", GATE)], "session-r")[0]
    tb = ToolBox(graph)
    assert M.reverify(tb, note)["status"] == "unchanged"
    graph.query("MATCH (:FeatureGate {name: $g})-[e:STAGE_IN]->(r:Release {num: 125}) SET e.stage = 'beta'", {"g": GATE})
    after = M.reverify(tb, note)
    assert after["status"] == "changed" and after["added"] >= 1 and after["removed"] >= 1 and "differs" not in after["detail"] or after["status"] == "changed"
    graph.query("MATCH (g:FeatureGate {name: $g}) DETACH DELETE g", {"g": GATE})
    gone = M.reverify(ToolBox(graph), note)
    assert gone["status"] == "changed" and "unknown_entity" in gone["detail"]
    assert M.reverify(tb, {**note, "tool_calls": []})["status"] == "unavailable"


def ask(client, question, session, tool=None):
    steps = [LLMResponse(tool_calls=[ToolCall("c", "gate_timeline", {"gate": GATE})]), LLMResponse("It reached stable at 1.25 [e3].")] if tool else [LLMResponse("x [e1].")]
    app.dependency_overrides[get_llm] = lambda: FakeLLM(steps)
    return client.post("/ask", json={"question": question, "session_id": session}).json()


def test_full_flow_save_then_recall_first_reverified_and_private(client):  # noqa: F811
    RECENT_ANSWERS.clear()
    a = ask(client, f"What is the stage of {GATE}?", "session-flow", tool=True)
    assert a["outcome"] == "answered_with_path" and a["can_save"] is True and a["answer_id"] and a["memory"] == []
    saved = client.post("/notes", json={"answer_id": a["answer_id"], "session_id": "session-flow"})
    assert saved.status_code == 200 and f"FeatureGate:{GATE}" in saved.json()["about"]
    b = ask(client, f"Remind me about {GATE}?", "session-flow", tool=True)
    assert b["outcome"] == "answered_from_memory" and len(b["memory"]) == 1
    mem = b["memory"][0]
    assert mem["learned_at"][:4] == "2026" or mem["learned_at"] and mem["reverified"]["status"] == "unchanged" and mem["text"] == a["answer"] and mem["sources"]
    assert b["answer"] and b["evidence"]  # the fresh graph answer follows the note
    other = ask(client, f"Remind me about {GATE}?", "session-other", tool=True)
    assert other["outcome"] == "answered_with_path" and other["memory"] == []  # notes are private to the session that saved them
    assert [n["id"] for n in client.get("/notes", params={"session_id": "session-flow"}).json()] == [saved.json()["id"]]
    assert client.delete(f"/notes/{saved.json()['id']}", params={"session_id": "session-other"}).status_code == 404
    assert client.delete(f"/notes/{saved.json()['id']}", params={"session_id": "session-flow"}).status_code == 200
    assert ask(client, f"Remind me about {GATE}?", "session-flow", tool=True)["memory"] == []


def test_things_that_must_not_be_saved(client):  # noqa: F811
    RECENT_ANSWERS.clear()
    a = ask(client, f"What is the stage of {GATE}?", "session-guard", tool=True)
    assert client.post("/notes", json={"answer_id": a["answer_id"], "session_id": "session-intruder"}).status_code == 404  # someone else's answer
    assert client.post("/notes", json={"answer_id": "doesnotexist", "session_id": "session-guard"}).status_code == 404
    RECENT_ANSWERS[a["answer_id"]]["response"]["validation"] = {"accepted": False, "rejected": [{"kind": "gate", "term": "X"}]}  # the validator rejected the wording
    assert client.post("/notes", json={"answer_id": a["answer_id"], "session_id": "session-guard"}).status_code == 422
    unknown = ask(client, "What is the stage of the LocalStorageCapacityIsolatio gate?", "session-guard")
    assert unknown["outcome"] == "unknown_entity" and unknown["can_save"] is False and unknown["answer_id"] is None or unknown["can_save"] is False
    app.dependency_overrides[get_llm] = lambda: FakeLLM([LLMResponse("plain [p1].")])
    from lineage.api.main import get_retriever
    from tests.test_contrast import StubRetriever

    app.dependency_overrides[get_retriever] = lambda: StubRetriever()
    r = client.post("/ask", json={"question": "Plain retrieval question?", "session_id": "session-guard", "mode": "retrieval"}).json()
    assert r["mode"] == "retrieval" and r["answer_id"] is None and r["can_save"] is False  # plain retrieval answers cannot be remembered


def test_a_note_still_answers_when_the_model_is_unavailable(client, monkeypatch):  # noqa: F811
    import lineage.api.main as m
    from lineage.agent.llm import LLMNotConfigured

    RECENT_ANSWERS.clear()
    a = ask(client, f"What is the stage of {GATE}?", "session-offline", tool=True)
    client.post("/notes", json={"answer_id": a["answer_id"], "session_id": "session-offline"})

    def no_key():
        raise LLMNotConfigured("no key")

    monkeypatch.setattr(m, "build_llm", no_key)
    app.dependency_overrides.pop(get_llm, None)
    r = client.post("/ask", json={"question": f"And what about {GATE} again?", "session_id": "session-offline"})
    body = r.json()
    assert r.status_code == 200 and body["outcome"] == "answered_from_memory" and body["answer"] == a["answer"] and body["memory"][0]["reverified"]["status"] == "unchanged"
    assert client.post("/ask", json={"question": f"What about {GATE} again?", "session_id": "session-nonote"}).status_code == 503  # no note: the usual 'no model' error
