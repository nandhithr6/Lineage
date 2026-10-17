import pytest

from lineage.agent.answer_cache import AnswerCache, cache_key
from lineage.agent.llm import FakeLLM, LLMResponse
from lineage.api.main import app, get_llm, get_retriever
from lineage.search.corpus import Passage
from lineage.search.rag import answer_with_retrieval
from tests.test_api import client  # noqa: F401
from tests.test_loader import graph  # noqa: F401


class StubRetriever:
    def search(self, query, k=5):
        return [(Passage(f"gate:G{i}", f"text about gate G{i}", f"https://github.com/o/r/blob/abc/f{i}.md"), round(0.9 - i / 10, 3)) for i in range(1, k + 1)]


def test_retrieval_answer_maps_passage_citations_onto_evidence_chips():
    llm = FakeLLM([LLMResponse("G1 is first [p1] and G2 second [p2, p3]; nothing about p9 [p9].")])
    r = answer_with_retrieval(llm, StubRetriever(), "what about gates?")
    assert r["mode"] == "retrieval" and r["outcome"] is None and r["status"] == "ok"
    assert "[e1]" in r["answer"] and "[e2, e3]" in r["answer"] and "[p" not in r["answer"]
    assert [e["id"] for e in r["evidence"]] == ["e1", "e2", "e3", "e4", "e5"] and [c["id"] for c in r["citations"]] == ["e1", "e2", "e3"]  # e9 does not exist
    assert r["evidence"][0]["source_url"].endswith("f1.md") and r["evidence"][0]["props"]["similarity"] == 0.8
    t = r["tool_trace"][0]
    assert t["tool"] == "vector_search" and t["evidence"] == 5 and isinstance(t["duration_ms"], int) and "not validated" in r["validation"]["note"]
    assert answer_with_retrieval(FakeLLM([LLMResponse("No citations here.")]), StubRetriever(), "q")["status"] == "uncited"


def test_ask_retrieval_mode_over_http_and_graph_mode_is_unchanged(client):  # noqa: F811
    app.dependency_overrides[get_retriever] = lambda: StubRetriever()
    app.dependency_overrides[get_llm] = lambda: FakeLLM([LLMResponse("Plain retrieval says so [p1].")])
    r = client.post("/ask", json={"question": "A question nobody has saved an answer for?", "mode": "retrieval"}).json()
    assert r["mode"] == "retrieval" and r["outcome"] is None and r["citations"][0]["id"] == "e1" and r["answer"] == "Plain retrieval says so [e1]."
    from lineage.agent.llm import ToolCall

    app.dependency_overrides[get_llm] = lambda: FakeLLM([LLMResponse(tool_calls=[ToolCall("c", "gate_timeline", {"gate": "LocalStorageCapacityIsolation"})]), LLMResponse("Stable at 1.25 [e3].")])
    g = client.post("/ask", json={"question": "What happened to the gate?"}).json()
    assert g["mode"] == "graph" and g["outcome"] == "answered_with_path" and g["tool_trace"][0]["tool"] == "gate_timeline"
    assert client.post("/ask", json={"question": "xx?", "mode": "nonsense"}).status_code == 422


def test_hero_question_is_cached_both_ways_under_different_keys(client, monkeypatch, tmp_path):  # noqa: F811
    import lineage.api.main as m

    assert cache_key("q", "sid") != cache_key("q", "sid", "retrieval") and cache_key("q", "sid") == cache_key("q", "sid", "graph")
    cache = AnswerCache(tmp_path / "a.jsonl", tmp_path)
    base = {"answer": "x [e1]", "status": "ok", "citations": [], "evidence": [], "path": [], "tool_trace": []}
    cache.put("Why is the Resource Quotas page stale?", {**base, "answer": "GRAPH [e1]"})
    cache.put("Why is the Resource Quotas page stale?", {**base, "answer": "RAG [e1]", "mode": "retrieval", "outcome": None}, mode="retrieval")
    monkeypatch.setattr(m, "ANSWER_CACHE", cache)
    app.dependency_overrides.pop(get_llm, None)
    g = client.post("/ask", json={"question": "why is the resource quotas page stale?"}).json()
    r = client.post("/ask", json={"question": "why is the resource quotas page stale?", "mode": "retrieval"}).json()
    assert g["answer"] == "GRAPH [e1]" and g["cached"] and g["mode"] == "graph" and r["answer"] == "RAG [e1]" and r["cached"] and r["mode"] == "retrieval" and r["outcome"] is None
    assert cache.prune() == 0
