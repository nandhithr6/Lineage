"""The slim build has no embedding model (LINEAGE_SLIM=1, or fastembed not installed). Everything that does not need it must keep working, and what does must say so."""
from pathlib import Path

import pytest

from lineage.agent.llm import FakeLLM, LLMResponse
from lineage.agent.local_embeddings import SLIM_NOTE, available
from lineage.agent.tools import ToolBox
from lineage.api.main import app, get_llm
from tests.test_api import client  # noqa: F401
from tests.test_loader import graph  # noqa: F401


@pytest.fixture
def slim(monkeypatch):
    monkeypatch.setenv("LINEAGE_SLIM", "1")


def test_available_follows_the_flag(monkeypatch):
    monkeypatch.delenv("LINEAGE_SLIM", raising=False)
    full = available()
    monkeypatch.setenv("LINEAGE_SLIM", "1")
    assert available() is False
    assert full in (True, False)  # True here (fastembed installed), False where the extra is not installed


def test_the_model_is_not_offered_a_tool_that_cannot_run(graph, slim, monkeypatch):  # noqa: F811
    tb = ToolBox(graph)
    assert "search_text" not in [s["name"] for s in tb.available_schemas()] and len(tb.available_schemas()) == len(ToolBox.SCHEMAS) - 1
    b = tb.search_text("which design document introduced the gate")
    assert b["status"] == "insufficient_evidence" and SLIM_NOTE in b["params"]["note"] and b["evidence"] == []
    monkeypatch.delenv("LINEAGE_SLIM")
    if available():
        assert "search_text" in [s["name"] for s in ToolBox(graph).available_schemas()]


def test_slim_loader_skips_chunks_and_the_vector_index(graph, slim, monkeypatch):  # noqa: F811
    from lineage.graph import loader

    real = Path("snapshot/chunks.jsonl")
    if not real.exists():
        pytest.skip("no chunks file")
    monkeypatch.setattr(loader, "CHUNKS_SNAPSHOT", real)
    counts = loader.load_all(graph)
    assert counts["Chunk"] == 0 and counts["FeatureGate"] > 400  # the rest of the graph is unaffected


def test_endpoints_in_the_slim_build(client, slim, monkeypatch):  # noqa: F811
    assert client.get("/health").json()["embeddings"] is False
    s = client.get("/search", params={"q": "which design document introduced the gate"}).json()
    assert s["status"] == "unavailable" and s["evidence"] == [] and "local setup" in s["note"]


def test_contrast_toggle_falls_back_to_keyword_retrieval(client, slim, monkeypatch):  # noqa: F811
    app.dependency_overrides.pop(__import__("lineage.api.main", fromlist=["x"]).get_retriever, None)
    app.dependency_overrides[get_llm] = lambda: FakeLLM([LLMResponse("Keyword retrieval says so [p1].")])
    r = client.post("/ask", json={"question": "A question nobody saved about local ephemeral storage quota?", "mode": "retrieval"}).json()
    assert r["mode"] == "retrieval" and r["retriever"] == "tfidf" and len(r["evidence"]) == 5 and r["answer"] == "Keyword retrieval says so [e1]."
    assert r["tool_trace"][0]["tool"] == "keyword_search" and r["outcome"] is None


def test_the_saved_hero_answer_still_works_in_both_modes_without_a_model(client, slim, monkeypatch):  # noqa: F811
    import lineage.api.main as m
    from lineage.agent.llm import LLMNotConfigured

    def no_key():
        raise LLMNotConfigured("no key")

    monkeypatch.setattr(m, "build_llm", no_key)
    app.dependency_overrides.pop(get_llm, None)
    for mode in ("graph", "retrieval"):
        r = client.post("/ask", json={"question": "Why is the Resource Quotas page stale?", "mode": mode}).json()
        assert r["cached"] is True and r["mode"] == mode
    assert r["retriever"] == "vector"  # the saved retrieval answer was made with the embedding model, and says so
