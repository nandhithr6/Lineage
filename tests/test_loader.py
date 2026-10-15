import pytest

from lineage.graph.client import connect
from lineage.graph.loader import batches, counts, is_drift_eligible, load_all

TEST_GRAPH = "lineage_pytest"


def test_drift_eligibility():
    assert is_drift_eligible("hardcoded", "content/en/docs/concepts/policy/resource-quotas.md")
    assert not is_drift_eligible("gate_lookup", "content/en/docs/concepts/policy/resource-quotas.md")
    assert not is_drift_eligible("hardcoded", "content/en/docs/contribute/style/hugo-shortcodes/index.md")


def test_batches():
    assert [len(b) for b in batches([{}] * 450, 200)] == [200, 200, 50]


@pytest.fixture
def graph():
    try:
        db = connect()
        db.connection.ping()
    except Exception:
        pytest.skip("FalkorDB not reachable")
    if TEST_GRAPH in db.list_graphs():
        db.select_graph(TEST_GRAPH).delete()
    g = db.select_graph(TEST_GRAPH)
    yield g
    if TEST_GRAPH in db.list_graphs():
        g.delete()


def test_load_is_idempotent_and_seed_gate_is_correct(graph):
    first = load_all(graph)
    second = load_all(graph)
    assert first == second and first["FeatureGate"] > 400
    rows = graph.ro_query(
        "MATCH (:FeatureGate {name:'LocalStorageCapacityIsolation'})-[e:STAGE_IN]->(r:Release {num:125}) RETURN e.stage"
    ).result_set
    assert rows == [["stable"]]


def test_seed_finding_end_to_end(graph):
    """snapshot -> graph -> manual link -> detector -> Finding node with a stable key and no internal IDs."""
    from lineage.detect.drift import run_detector, write_findings

    load_all(graph)
    results, stats = run_detector(graph)
    seed = [r for r in results if r.gate == "LocalStorageCapacityIsolation"]
    assert len(seed) == 1 and seed[0].kind == "stale_stage" and seed[0].claimed_stage == "alpha"
    assert seed[0].actual_stages == ["stable"] and seed[0].gate_removed
    write_findings(graph, results)
    write_findings(graph, results)  # idempotent
    rows = graph.ro_query("MATCH (f:Finding {key:$k})-[:FOR_CLAIM]->(c:Claim), (f)-[:FOR_GATE]->(g), (f)-[:AS_OF]->(r) "
                          "RETURN count(f), g.name, r.version", {"k": seed[0].key}).result_set
    assert rows == [[1, "LocalStorageCapacityIsolation", "1.26"]]
    edge = graph.ro_query("MATCH (:Claim {id:$c})-[a:ABOUT]->(:FeatureGate {name:'LocalStorageCapacityIsolation'}) "
                          "RETURN a.method, a.link_confidence, a.linker_confidence, a.verified_by", {"c": seed[0].claim_id}).result_set
    assert edge[0][0] == "manual" and edge[0][1] == 1.0 and edge[0][2] is not None and edge[0][2] < 0.8
