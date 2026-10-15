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
    assert d["status"] == "ok" and d["sections"]["link"]["method"] == "manual" and d["path"][0].startswith("DocPage:")
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
    assert a["manual_links"] == 1 and a["counts"]["FeatureGate"] > 400 and a["detector"]["claims checked"] > 100
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
