import pytest
from fastapi.testclient import TestClient

from lineage.agent.llm import FakeLLM, LLMResponse, ToolCall
from lineage.api.main import app, finding_id, get_graph, get_llm
from lineage.detect.drift import run_detector, write_findings
from lineage.graph.loader import load_all
from tests.test_loader import graph  # noqa: F401


@pytest.fixture
def client(graph):  # noqa: F811
    load_all(graph)
    results, _ = run_detector(graph)
    write_findings(graph, results)
    app.dependency_overrides[get_graph] = lambda: graph
    yield TestClient(app)
    app.dependency_overrides.clear()


def seed(client):
    return next(f for f in client.get("/findings").json()["findings"] if f["gate"] == "LocalStorageCapacityIsolation")


def test_health(client):
    h = client.get("/health").json()
    assert h["status"] == "ok" and h["database"] is True and h["graph"] == "lineage_pytest"


def test_findings_list_and_filters(client):
    r = client.get("/findings").json()
    assert r["total"] >= 2 and r["counts"]["stale_stage"] >= 2
    assert r["findings"][0]["severity"] == "finding"  # most severe first
    only = client.get("/findings", params={"severity": "review"}).json()
    assert only["total"] > 0 and all(f["severity"] == "review" for f in only["findings"])
    assert client.get("/findings", params={"sig": "sig-storage", "severity": "finding"}).json()["total"] >= 1
    assert all(f["id"] == finding_id(f["key"]) and len(f["id"]) == 12 for f in r["findings"])
    assert client.get("/findings", params={"severity": "bogus"}).status_code == 422
    assert all((f["link_confidence"] or 0) >= 0.9 for f in client.get("/findings", params={"min_conf": 0.9}).json()["findings"])


def test_finding_detail_by_short_id_and_by_key(client):
    f = seed(client)
    d = client.get(f"/findings/{finding_id(f['key'])}").json()
    assert d["status"] == "ok" and d["sections"]["link"]["method"] == "linker" and d["path"][0].startswith("DocPage:")
    from urllib.parse import quote
    assert client.get(f"/findings/{quote(f['key'], safe='')}").json()["key"] == f["key"]  # a percent-encoded key works too (a raw '#' would start the URL fragment)
    assert client.get("/findings/doesnotexist").status_code == 404


def test_fix_is_post_only_and_returns_a_one_line_diff(client):
    f = seed(client)
    r = client.post(f"/findings/{finding_id(f['key'])}/fix").json()
    assert r["status"] == "ok" and 'state="stable"' in r["diff"] and r["reviewers"]
    assert client.get(f"/findings/{finding_id(f['key'])}/fix").status_code == 405


def test_entity_neighbors_and_unknown(client):
    e = client.get("/entity/FeatureGate/LocalStorageCapacityIsolation").json()
    assert e["node"]["label"] == "FeatureGate" and e["edges"] and {x["type"] for x in e["edges"]} >= {"STAGE_IN", "ABOUT"}
    inc = [x for x in e["edges"] if x["type"] == "STAGE_IN"]
    assert all(x["source"] == "FeatureGate:LocalStorageCapacityIsolation" for x in inc)  # direction preserved
    assert client.get("/entity/Release/125").json()["node"]["props"]["version"] == "1.25"
    assert client.get("/entity/Nope/x").status_code == 404 and client.get("/entity/KEP/missing").status_code == 404


def test_about_data(client):
    a = client.get("/about-data").json()
    assert a["manual_links"] == 0 and a["counts"]["FeatureGate"] > 400 and a["detector"]["claims checked"] > 100
    assert any(s["repo"] == "kubernetes/website" and len(s["commit"]) == 40 for s in a["sources"]) and a["limitations"]


def test_ask_with_fake_llm_and_session(client):
    llm = FakeLLM([LLMResponse(tool_calls=[ToolCall("c", "gate_timeline", {"gate": "LocalStorageCapacityIsolation"})]),
                   LLMResponse("It reached stable at 1.25 [e3]."),
                   LLMResponse("Same as before [e3]?")])
    app.dependency_overrides[get_llm] = lambda: llm
    r = client.post("/ask", json={"question": "What is LocalStorageCapacityIsolation?", "session_id": "s1"}).json()
    assert r["status"] == "ok" and r["citations"][0]["id"] == "e3" and r["tool_trace"][0]["tool"] == "gate_timeline"
    client.post("/ask", json={"question": "and then?", "session_id": "s1"})
    assert any(m["role"] == "assistant" and "stable at 1.25" in m["content"] for m in llm.seen[2])  # history carried over
    assert client.post("/ask", json={"question": "x"}).status_code == 422  # too short


def test_ask_without_llm_configured_is_a_clear_503(client, monkeypatch):
    for k in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "GEMINI_API_KEY", "GEMINI_MODEL", "GROQ_API_KEY", "GROQ_MODEL"):
        monkeypatch.delenv(k, raising=False)
    app.dependency_overrides.pop(get_llm, None)
    r = client.post("/ask", json={"question": "What is stale?"})
    assert r.status_code == 503 and "GEMINI_API_KEY" in r.json()["detail"]
    assert client.get("/health").json()["llm_configured"] is False


def test_ask_when_every_provider_fails_is_a_503_not_a_crash(client):
    from lineage.agent.llm import ProviderError

    class Down:
        def chat(self, *a, **k):
            raise ProviderError("gemini HTTP 429: quota")
    app.dependency_overrides[get_llm] = lambda: Down()
    r = client.post("/ask", json={"question": "Who chairs sig-storage?"})
    assert r.status_code == 503 and "unavailable" in r.json()["detail"] and "quota" in r.json()["detail"]


def test_owners_endpoint_time_travel_and_errors(client):
    now = client.get("/owners/kubernetes/pkg/controller/job").json()
    then = client.get("/owners/kubernetes/pkg/controller/job", params={"as_of": "1.30"}).json()
    person = lambda r, role: {o["handle"] for o in r["owners"] if o["role"] == role and not o["via_team"] and o["declared_at"] == "pkg/controller/job"}
    assert person(then, "APPROVER_OF") == {"alculquicondor"} and person(now, "APPROVER_OF") == {"mimowo"}
    assert then["as_of"] == "1.30" and now["as_of"] == "latest (1.38-dev)" and "1.37" in now["available_releases"]
    docs = client.get("/owners/website/content/en/docs/concepts/policy/resource-quotas.md").json()
    assert set(docs["declared_at"]) == {"content/en", "content/en/docs"} and any(o["handle"] == "tengqm" for o in docs["owners"])
    assert client.get("/owners/kubernetes/pkg/controller/job", params={"as_of": "1.20"}).status_code == 422  # not sampled
    assert client.get("/owners/website/content/en/docs/x.md", params={"as_of": "1.30"}).status_code == 422  # docs history is HEAD only
    assert client.get("/owners/kubernetes/no/such/folder").status_code == 404
    assert client.get("/owners/nowhere/x").status_code == 404


def test_changes_endpoint(client):
    c = client.get("/changes", params={"entity": "pkg/controller/job", "from": "1.30", "to": "1.37"}).json()
    assert c["kind"] == "path" and any(r["handle"] == "alculquicondor" for r in c["removed"]) and any(r["handle"] == "mimowo" for r in c["added"]) and c["unchanged"] > 0
    g = client.get("/changes", params={"entity": "LocalStorageCapacityIsolation", "from": "1.9", "to": "1.25"}).json()
    assert g["kind"] == "gate" and g["gate_stage_then"] == ["alpha"] and g["gate_stage_now"] == ["stable"]
    removed = client.get("/changes", params={"entity": "LocalStorageCapacityIsolation", "from": "1.25", "to": "1.37"}).json()
    assert removed["gate_stage_now"] == []  # removed from Kubernetes: no stage any more
    assert client.get("/changes", params={"entity": "pkg/controller/job", "from": "1.10", "to": "1.37"}).status_code == 422


def test_server_logging_is_configured_and_never_logs_urls(client):
    class P:
        name = "gemini"
    from lineage.agent.llm import LLMResponse
    llm = FakeLLM([LLMResponse("Insufficient evidence.")])
    app.dependency_overrides[get_llm] = lambda: llm
    import logging
    assert logging.getLogger("lineage").level == logging.INFO and logging.getLogger("httpx").level >= logging.WARNING
    assert client.post("/ask", json={"question": "anything at all"}).status_code == 200


def test_ask_limiter_per_visitor_per_day_and_window():
    from fastapi import HTTPException

    from lineage.api.main import AskLimiter

    t = [0.0]
    lim = AskLimiter(per_ip_hour=2, per_day=3, clock=lambda: t[0])
    lim.check("a"); lim.check("a")
    with pytest.raises(HTTPException) as e:
        lim.check("a")
    assert e.value.status_code == 429 and "per hour" in e.value.detail and "Retry-After" in e.value.headers
    lim.check("b")  # a different visitor is not affected
    with pytest.raises(HTTPException) as e:
        lim.check("c")  # the global daily budget (3) is used up
    assert "daily budget" in e.value.detail
    t[0] = 86401
    lim.check("a")  # a day later everything is available again


def test_ask_is_rate_limited_over_http(client, monkeypatch):
    from lineage.agent.llm import LLMResponse
    from lineage.api import main as M

    monkeypatch.setattr(M, "ASK_LIMITER", M.AskLimiter(per_ip_hour=1, per_day=0))
    app.dependency_overrides[get_llm] = lambda: FakeLLM([LLMResponse("Insufficient evidence."), LLMResponse("Insufficient evidence.")])
    assert client.post("/ask", json={"question": "first question"}, headers={"x-forwarded-for": "9.9.9.9, 10.0.0.1"}).status_code == 200
    r = client.post("/ask", json={"question": "second question"}, headers={"x-forwarded-for": "9.9.9.9"})
    assert r.status_code == 429 and "per hour" in r.json()["detail"]
    assert client.post("/ask", json={"question": "other visitor"}, headers={"x-forwarded-for": "8.8.8.8"}).status_code == 200


def test_single_container_server_serves_api_and_ui(tmp_path):
    from fastapi.testclient import TestClient

    from lineage.api.server import build

    (tmp_path / "index.html").write_text("<html>UI</html>")
    c = TestClient(build(tmp_path))
    assert c.get("/healthz").json() == {"status": "ok"} and "UI" in c.get("/").text
    assert c.get("/api/health").status_code in (200, 500)  # the API is mounted under /api (needs a database to say more)
    assert "UI" not in TestClient(build(tmp_path / "nope")).get("/api/openapi.json").text  # without a built UI only the API exists


def test_insights_endpoint_has_the_three_sections(client):
    r = client.get("/insights?months=12")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"months", "ownership_gaps", "orphaned", "dormant", "key_reviewers", "notes"}
    assert all(not row["path"].startswith("test/") for row in body["ownership_gaps"])


def test_warm_caches_fills_the_slow_ownership_caches(client, graph, monkeypatch):  # noqa: F811
    import lineage.api.main as m

    monkeypatch.setattr(m, "_graph", lambda: graph)
    m._CACHE.clear()
    m.warm_caches()
    assert {k[0] for k in m._CACHE} >= {"views"}
    # a request after the warm-up must hit the cache, not recompute
    monkeypatch.setattr(m, "scan_ownership", lambda *a, **k: (_ for _ in ()).throw(AssertionError("recomputed")))
    assert client.get("/orphans?months=12").status_code == 200


def _ask_with(client, tool, args, final="It is what it is [e1]."):
    app.dependency_overrides[get_llm] = lambda: FakeLLM([LLMResponse(tool_calls=[ToolCall("c", tool, args)]), LLMResponse(final), LLMResponse(final)])
    return client.post("/ask", json={"question": f"question about {tool}?", "session_id": f"s-{tool}-{abs(hash(str(args)))}"}).json()


def test_outcome_answered_with_path_and_the_trace_fields(client):
    r = _ask_with(client, "gate_timeline", {"gate": "LocalStorageCapacityIsolation"}, "It reached stable at 1.25 [e3].")
    assert r["outcome"] == "answered_with_path" and r["status"] == "ok"
    t = r["tool_trace"][0]
    assert t["tool"] == "gate_timeline" and t["params"] == {"gate": "LocalStorageCapacityIsolation"} and t["evidence"] == 4 and t["outcome"] == "answered_with_path"
    assert isinstance(t["duration_ms"], int) and t["duration_ms"] >= 0 and t["unresolved"] == []


def test_outcome_unknown_entity_runs_no_traversal_and_offers_names_as_text(client):
    r = _ask_with(client, "gate_timeline", {"gate": "LocalStorageCapacityIsolatio"}, "It is stable [e1].")
    assert r["outcome"] == "unknown_entity" and r["status"] == "insufficient_evidence" and r["evidence"] == [] and r["citations"] == []
    assert r["answer"].startswith("Unknown entity") and "did you mean: LocalStorageCapacityIsolation" in r["answer"] and "stable" not in r["answer"].lower().replace("insufficient", "")
    t = r["tool_trace"][0]
    assert t["outcome"] == "unknown_entity" and t["evidence"] == 0 and t["unresolved"][0]["did_you_mean"][0] == "LocalStorageCapacityIsolation"


def test_outcome_not_in_graph_means_the_name_exists_but_nothing_connects(client):
    r = _ask_with(client, "owners_of", {"path": "pkg/volume", "as_of": "1.20"}, "They were X [e1].")
    assert r["outcome"] == "not_in_graph" and r["status"] == "insufficient_evidence" and r["evidence"] == []
    assert r["tool_trace"][0]["outcome"] == "not_in_graph" and r["tool_trace"][0]["unresolved"] == []  # both names resolved; there is simply no record
    assert "unknown" not in r["answer"].lower().split("what i searched")[0]


def test_saved_demo_answers_keep_their_outcome():
    from lineage.api.models import AskResponse

    legacy = {"answer": "x [e1]", "status": "ok", "citations": [], "evidence": [], "path": [], "tool_trace": []}  # saved before outcomes existed
    assert AskResponse(**legacy).outcome == "answered_with_path"


def test_unknown_names_in_the_question_are_answered_without_a_model_or_a_key(client, monkeypatch):
    import lineage.api.main as m
    from lineage.agent.llm import LLMNotConfigured

    def no_key():
        raise LLMNotConfigured("no key")

    monkeypatch.setattr(m, "build_llm", no_key)
    app.dependency_overrides.pop(get_llm, None)
    monkeypatch.setattr(m, "ASK_LIMITER", m.AskLimiter(1, 1))
    for q, needle in (("What is the stage of the LocalStorageCapacityIsolatio gate?", "did you mean: LocalStorageCapacityIsolation"), ("Who will approve pkg/controller/job in release 1.45?", "release 1.45")):
        for _ in range(3):  # beyond the 1-question limiter budget: these never touch it
            r = client.post("/ask", json={"question": q})
            assert r.status_code == 200 and r.json()["outcome"] == "unknown_entity" and needle in r.json()["answer"] and r.json()["tool_trace"][0]["tool"] == "resolve_question"
    assert client.post("/ask", json={"question": "something a model must answer"}).status_code == 503  # an ordinary question still needs the model
