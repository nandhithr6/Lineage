import json

from fastapi.testclient import TestClient

from lineage.agent import answer_cache as ac
from lineage.agent.answer_cache import AnswerCache, cache_key, load, normalise
from lineage.api.main import app, get_graph, get_llm
import lineage.api.main as m
from tests.test_api import client  # noqa: F401
from tests.test_loader import graph  # noqa: F401


def test_normalise_ignores_case_punctuation_and_spacing():
    assert normalise("  Why is the Resource Quotas page STALE?? ") == normalise("why is the resource quotas page stale")
    assert normalise("pkg/controller/job") != normalise("pkg controller job")  # paths stay distinct


def test_key_depends_on_the_snapshot():
    assert cache_key("q", "snapA") != cache_key("q", "snapB")


def test_snapshot_id_changes_when_data_changes(tmp_path):
    (tmp_path / "a.jsonl").write_text("1")
    first = ac.snapshot_id(tmp_path)
    (tmp_path / "a.jsonl").write_text("2")
    assert ac.snapshot_id(tmp_path) != first
    (tmp_path / "demo_answers.jsonl").write_text("ignored: the cache file is not part of the fingerprint")
    assert ac.snapshot_id(tmp_path) != first  # still the "2" content...
    (tmp_path / "a.jsonl").write_text("1")
    assert ac.snapshot_id(tmp_path) == first


def test_put_get_and_stale_entries(tmp_path):
    snap = tmp_path / "snap"; snap.mkdir(); (snap / "a.jsonl").write_text("1")
    cache = AnswerCache(tmp_path / "answers.jsonl", snap)
    assert cache.get("Hello there?") is None
    cache.put("Hello there?", {"answer": "x [e1]", "status": "ok", "evidence": [{"id": "e1"}]})
    got = cache.get("hello there")
    assert got["cached"] is True and got["evidence"] == [{"id": "e1"}] and got["snapshot_id"] == cache.sid
    (snap / "a.jsonl").write_text("2")  # the data changed: the saved answer must not be served
    fresh = AnswerCache(tmp_path / "answers.jsonl", snap)
    assert fresh.get("hello there") is None and fresh.stale_entries() == ["Hello there?"]
    assert fresh.prune() == 1 and fresh.stale_entries() == [] and load(tmp_path / "answers.jsonl") == {}


def test_ask_serves_a_saved_answer_without_any_llm_and_without_using_the_quota(client, monkeypatch, tmp_path):  # noqa: F811
    cache = AnswerCache(tmp_path / "answers.jsonl", tmp_path)
    full = {"answer": "It is stale [e1].", "status": "ok", "citations": [{"id": "e1", "kind": "node", "type": "Finding", "from": "a", "to": None, "props": {}, "source_url": None}],
            "evidence": [{"id": "e1", "kind": "node", "type": "Finding", "from": "a", "to": None, "props": {}, "source_url": None}], "path": [], "tool_trace": []}
    cache.put("Why is the Resource Quotas page stale?", full)
    monkeypatch.setattr(m, "ANSWER_CACHE", cache)
    monkeypatch.setattr(m, "ASK_LIMITER", m.AskLimiter(1, 1))
    app.dependency_overrides.pop(get_llm, None)  # no LLM configured at all
    from lineage.agent.llm import LLMNotConfigured

    def not_configured():
        raise LLMNotConfigured("no key in this test")

    monkeypatch.setattr(m, "build_llm", not_configured)  # the developer's .env must never make a unit test call a real model
    for _ in range(3):  # more than the limiter would allow
        r = client.post("/ask", json={"question": "why is the resource quotas page stale?"})
        assert r.status_code == 200 and r.json()["cached"] is True and r.json()["evidence"][0]["id"] == "e1" and r.json()["status"] == "ok"
    miss = client.post("/ask", json={"question": "something nobody saved"})
    assert miss.status_code == 503  # not cached, and no key: the clear "not configured" error, as before


def test_every_saved_demo_answer_in_the_repo_has_resolvable_citations():
    """If snapshot/demo_answers.jsonl exists, each current entry must cite only evidence it carries (no dangling [eN])."""
    from lineage.agent.loop import cited_ids

    for key, row in ac.load().items():
        if row.get("mode") == "retrieval":  # the plain-retrieval answer is deliberately unchecked and may be uncited
            continue
        r = row["response"]
        have = {e["id"] for e in r["evidence"]}
        assert r["status"] == "ok" and set(cited_ids(r["answer"])) <= have, row["question"]
