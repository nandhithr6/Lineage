import json
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
def graph(monkeypatch, tmp_path):
    # The real PR snapshot (thousands of PRs) must not leak into unit tests: tests that need PRs load a small synthetic set themselves.
    from lineage.graph import loader
    monkeypatch.setattr(loader, "PRS_SNAPSHOT", tmp_path / "no-prs-in-unit-tests.jsonl")
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
    assert edge[0][0] == "manual" and edge[0][1] == 1.0
    assert edge[0][2] is not None and edge[0][2] >= 0.8  # the AUTOMATIC linker alone now picks the seed gate with confidence (sibling eliminated)
    elim = json.loads(graph.ro_query("MATCH (:Claim {id:$c})-[a:ABOUT]->(:FeatureGate {name:'LocalStorageCapacityIsolation'}) RETURN a.eliminated", {"c": seed[0].claim_id}).result_set[0][0])
    assert any(e["gate"] == "LocalStorageCapacityIsolationFSQuotaMonitoring" and e["first_version"] == "1.15" for e in elim)
    # Policy consequence (README): without the hand verification the link is automatic but NOT anchored, so the seed would be "possible drift", not a finding.
    graph.query("MATCH ()-[a:ABOUT {method:'manual'}]->() SET a.method = 'linker'")
    again = next(r for r in run_detector(graph)[0] if r.gate == "LocalStorageCapacityIsolation")
    assert again.kind == "possible_drift" and again.severity == "review"


def test_effective_owners_walk_up_and_time_travel(graph):
    from lineage.graph.queries import effective_owners

    load_all(graph)
    page = "content/en/docs/concepts/policy/resource-quotas.md"
    rows = effective_owners(graph, "website", page, is_file=True)
    assert {r["declared_at"] for r in rows} == {"content/en", "content/en/docs"}  # stops at no_parent_owners
    assert not any("localization" in (r["via_team"] or "") for r in rows)
    assert {"tengqm", "reylejano"} <= {r["handle"] for r in rows if r["role"] == "APPROVER_OF"}
    assert all(r["source_url"].startswith("https://github.com/kubernetes/website/blob/") for r in rows)

    def job_approvers(as_of):
        return {r["handle"] for r in effective_owners(graph, "kubernetes", "pkg/controller/job", as_of=as_of)
                if r["role"] == "APPROVER_OF" and r["declared_at"] == "pkg/controller/job" and not r["via_team"]}

    assert job_approvers(130) == {"alculquicondor"} and job_approvers(138) == {"mimowo"}


def test_hero_trace_resource_quotas(graph):
    from lineage.detect.drift import run_detector, write_findings
    from lineage.graph.trace import build_trace
    from lineage.hero import render

    load_all(graph)
    write_findings(graph, run_detector(graph)[0])
    b = build_trace(graph, "resource-quotas")
    assert b["status"] == "ok" and b["tool"] == "explain_finding"
    s = b["sections"]
    assert s["finding"]["kind"] == "stale_stage" and s["link"]["method"] == "manual"
    assert [h["stage"] for h in s["gate"]["history"]] == ["alpha", "beta", "stable"]
    assert s["kep"]["id"] == "sig-storage/361-local-ephemeral-storage-isolation" and s["kep"]["sig"] == "sig-storage"
    groups = {r["group"] for r in s["reviewers"]}
    assert groups == {"docs approver", "domain expert"} and not any(r["handle"] == "stewart-yu" for r in s["reviewers"])  # emeritus
    assert all(e["source_url"] or e["type"] == "Finding" for e in b["evidence"] if e["type"] != "Reviewer")
    text = render(b)
    assert "LocalStorageCapacityIsolation" in text and "verified by hand" in text and "source [e1]" in text


def test_hero_says_insufficient_evidence_when_nothing_matches(graph):
    from lineage.graph.trace import build_trace
    from lineage.hero import render

    load_all(graph)
    out = render(build_trace(graph, "no-such-page-anywhere"))
    assert out.startswith("INSUFFICIENT EVIDENCE") and "nothing is guessed" in out


def test_stale_linker_edges_do_not_survive_a_reload(graph):
    """A candidate the linker no longer produces must disappear (MERGE alone would leave it behind); manual links stay."""
    load_all(graph)
    n = graph.ro_query("MATCH ()-[a:ABOUT {method:'linker'}]->() RETURN count(a)").result_set[0][0]
    graph.query("MATCH (c:Claim {kind:'hardcoded'}), (g:FeatureGate {name:'Sysctls'}) WITH c, g LIMIT 1 "
                "MERGE (c)-[a:ABOUT]->(g) SET a.method = 'linker', a.link_confidence = 0.99")  # a leftover from an older linker version
    assert graph.ro_query("MATCH ()-[a:ABOUT {method:'linker'}]->() RETURN count(a)").result_set[0][0] == n + 1
    load_all(graph)
    assert graph.ro_query("MATCH ()-[a:ABOUT {method:'linker'}]->() RETURN count(a)").result_set[0][0] == n
    assert graph.ro_query("MATCH ()-[a:ABOUT {method:'manual'}]->() RETURN count(a)").result_set[0][0] == 1


def test_demo_queries_are_small_and_non_empty(graph):
    """Every focused query for the FalkorDB browser returns something readable (not empty, not a hairball)."""
    from lineage.detect.drift import run_detector, write_findings
    from lineage.graph.demo_queries import check, export

    load_all(graph)
    write_findings(graph, run_detector(graph)[0])
    assert check(graph, quiet=True) == 0
    assert "MATCH" in export() and "hairball" in export()
