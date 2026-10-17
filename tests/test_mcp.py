import asyncio

import pytest
from mcp import Client

import lineage.mcp_server as ms
from lineage.agent.tools import ToolBox
from lineage.detect.drift import run_detector, write_findings
from lineage.graph.loader import load_all
from tests.test_loader import graph  # noqa: F401


def run(coro):
    return asyncio.run(coro)


def test_server_exposes_exactly_the_curated_read_only_tools():
    async def go():
        async with Client(ms.build_server()) as c:
            return (await c.list_tools()).tools

    tools = run(go())
    assert [t.name for t in tools] == ms.TOOL_NAMES
    assert all(t.annotations and t.annotations.read_only_hint and t.annotations.destructive_hint is False for t in tools)
    forbidden = ("cypher", "write", "delete", "create", "update", "set_", "query")
    assert not [t.name for t in tools if any(f in t.name for f in forbidden)]  # no free-form Cypher, no write tools
    schemas = {s["name"]: s for s in ToolBox.SCHEMAS}
    for t in tools:  # the MCP input schema offers every argument of the agent's tool schema (a few optional extras such as `limit` are allowed) and requires the same ones
        agent = schemas[t.name]["parameters"]
        assert set(agent["properties"]) <= set(t.input_schema["properties"]), t.name
        assert set(t.input_schema.get("required", [])) == set(agent.get("required", [])), t.name


@pytest.fixture
def served(graph, monkeypatch):  # noqa: F811
    load_all(graph)
    write_findings(graph, run_detector(graph)[0])
    monkeypatch.setattr(ms, "toolbox", lambda: ToolBox(graph))
    return graph


def call(name, args):
    async def go():
        async with Client(ms.build_server()) as c:
            r = await c.call_tool(name, args)
            return r.is_error, r.structured_content

    return run(go())


def test_calls_return_the_same_evidence_bundles_with_citation_ids_and_pinned_sources(served):
    err, b = call("gate_timeline", {"gate": "LocalStorageCapacityIsolation"})
    assert not err and b["status"] == "ok" and b["tool"] == "gate_timeline"
    assert [e["id"] for e in b["evidence"]][:2] == ["e1", "e2"] and all(e["source_url"].startswith("https://github.com/kubernetes/") for e in b["evidence"])
    got = ToolBox(served).call("gate_timeline", {"gate": "LocalStorageCapacityIsolation"})  # what the agent receives
    assert {k: v for k, v in b.items() if k != "duration_ms"} == {k: v for k, v in got.items() if k != "duration_ms"}


def test_refusals_travel_over_mcp_unchanged(served):
    assert call("gate_timeline", {"gate": "NoSuchGate"})[1]["status"] == "insufficient_evidence"
    status = call("owners_of", {"path": "pkg/controller/job", "as_of": "1.45"})[1]
    assert status["status"] == "insufficient_evidence" and "future cannot be known" in status["params"]["note"]


def test_explain_and_reviewers_tools(served):
    assert call("explain_finding", {"query": "resource-quotas"})[1]["status"] == "ok"
    b = call("suggest_reviewers", {"query": "resource-quotas"})[1]
    assert b["status"] == "ok" and all(e["type"] == "SUGGESTED_REVIEWER" and e["props"]["reason"] for e in b["evidence"])
    assert call("suggest_reviewers", {"query": "no-such-page"})[1]["status"] == "insufficient_evidence"


def test_streamable_http_is_mounted_in_the_single_container_app(tmp_path):
    from fastapi.testclient import TestClient

    from lineage.api.server import build

    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}}}
    with TestClient(build(tmp_path)) as c:  # the context manager runs the lifespan that starts the MCP session manager
        r = c.post("/mcp/", json=init, headers={"accept": "application/json, text/event-stream", "host": "lineage.example.org"})
        assert r.status_code == 200 and "lineage" in r.text
        assert c.get("/healthz").json() == {"status": "ok"}


def test_outcomes_travel_over_mcp(served):
    assert call("gate_timeline", {"gate": "LocalStorageCapacityIsolation"})[1]["outcome"] == "answered_with_path"
    bad = call("gate_timeline", {"gate": "NoSuchGate"})[1]
    assert bad["outcome"] == "unknown_entity" and bad["unresolved"][0]["given"] == "NoSuchGate" and bad["evidence"] == []
    assert call("owners_of", {"path": "pkg/volume", "as_of": "1.20"})[1]["outcome"] == "not_in_graph"
    assert call("owners_of", {"path": "pkg/volume", "as_of": "1.45"})[1]["outcome"] == "unknown_entity"
