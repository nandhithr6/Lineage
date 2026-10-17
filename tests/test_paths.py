import pytest

from lineage.agent.tools import ToolBox
from lineage.graph import paths as P
from lineage.graph.loader import load_all
from lineage.graph.trace import build_trace
from tests.test_loader import graph  # noqa: F401


@pytest.fixture
def g(graph):  # noqa: F811
    load_all(graph)
    return graph


def test_weights_are_set_only_on_structural_edges(g):
    q = lambda c: g.ro_query(c).result_set[0][0]  # noqa: E731
    assert q("MATCH ()-[e:OWNED_BY]->() WHERE e.weight = 1 RETURN count(e)") == q("MATCH ()-[e:OWNED_BY]->() RETURN count(e)")
    assert q("MATCH ()-[e:APPROVED_KEP]->() WHERE e.weight = 2 RETURN count(e)") == q("MATCH ()-[e:APPROVED_KEP]->() RETURN count(e)")
    assert q("MATCH ()-[e:STAGE_IN]->() WHERE e.weight IS NOT NULL RETURN count(e)") == 0  # activity/volume edges are never path material
    P.set_path_weights(g)  # idempotent
    assert q("MATCH ()-[e:OWNED_BY]->() WHERE e.weight = 1 RETURN count(e)") > 0


def test_shortest_path_between_a_person_and_a_gate_goes_through_the_team_and_the_design_decision(g):
    p = P.shortest_path(g, ("Person", "jsafrane"), ("FeatureGate", "LocalStorageCapacityIsolation"))
    assert p and p["nodes"][0] == "Person:jsafrane" and p["nodes"][-1] == "FeatureGate:LocalStorageCapacityIsolation"
    assert [h["type"] for h in p["hops"]][-1] == "INTRODUCES" and any(h["type"] == "OWNED_BY" for h in p["hops"])
    assert all(h["direction"] in ("forward", "backward") for h in p["hops"]) and any(h["source_url"] for h in p["hops"])
    assert "-[INTRODUCES]->" in P.describe_path(p) and p["weight"] == sum(P.PATH_WEIGHTS[h["type"]] for h in p["hops"])


def test_no_path_within_the_hop_limit_and_unsupported_labels(g):
    assert P.shortest_path(g, ("Person", "jsafrane"), ("FeatureGate", "LocalStorageCapacityIsolation"), max_len=1) is None
    with pytest.raises(ValueError):
        P.shortest_path(g, ("Bogus", "x"), ("FeatureGate", "y"))


def test_a_timeout_becomes_a_readable_error(g, monkeypatch):
    class Boom:
        name = "x"

        def ro_query(self, *a, **k):
            raise RuntimeError("Query timed out")

    with pytest.raises(P.PathTimeout):
        P.shortest_path(Boom(), ("Person", "a"), ("Person", "b"))


def test_path_between_tool_outcomes(g):
    tb = ToolBox(g)
    ok = tb.call("path_between", {"a": "jsafrane", "b": "LocalStorageCapacityIsolation"})
    assert ok["outcome"] == "answered_with_path" and ok["status"] == "ok" and ok["path"][0] == "Person:jsafrane" and "summary" in ok["params"]
    assert [e["type"] for e in ok["evidence"]][-1] == "INTRODUCES" and all(e["props"]["direction"] for e in ok["evidence"])
    none = tb.call("path_between", {"a": "jsafrane", "b": "LocalStorageCapacityIsolation", "max_hops": 1})
    assert none["outcome"] == "not_in_graph" and none["evidence"] == [] and "no path" in none["params"]["note"]
    unknown = tb.call("path_between", {"a": "jsafrane", "b": "NoSuchEntityAnywhere"})
    assert unknown["outcome"] == "unknown_entity" and unknown["unresolved"][0]["argument"] == "b"
    assert tb.call("path_between", {"a": "NodeSwap", "b": "NodeSwap"})["outcome"] == "not_in_graph"  # the same entity: nothing to connect


def test_reviewer_reasons_include_how_each_expert_connects_to_the_gate(g):
    from lineage.detect.drift import run_detector, write_findings

    write_findings(g, run_detector(g)[0])
    reviewers = build_trace(g, "resource-quotas")["sections"]["reviewers"]
    connected = [r for r in reviewers if any(x["category"] == "connection" for x in r["reasons"])]
    assert connected, "domain experts must carry a connection reason"
    x = next(x for x in connected[0]["reasons"] if x["category"] == "connection")
    assert "connects to LocalStorageCapacityIsolation" in x["reason"] and x["hops"] and x["hops"][-1]["type"] == "INTRODUCES"
    docs_only = [r for r in reviewers if all(x["category"] == "docs" for x in r["reasons"])]
    assert docs_only and not any(x["category"] == "connection" for r in docs_only for x in r["reasons"])  # docs approvers are explained by the docs OWNERS path already
    assert all(r["score"] == round(sum({"docs": 1.0, "code": 1.0, "kep": 0.8, "active": 0.7, "lead": 0.5, "network": 0.3}[x["category"]] for x in r["reasons"] if x["category"] != "connection"), 2) for r in reviewers)  # no score change
