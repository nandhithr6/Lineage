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
