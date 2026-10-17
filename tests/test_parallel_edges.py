"""FalkorDB's known limitation (docs.falkordb.com/cypher/known-limitations): a relationship in a pattern that is not referenced elsewhere is only checked for EXISTENCE,
so parallel edges between the same two nodes are silently collapsed. These tests pin the behaviour and guard the queries that count."""
import re
from pathlib import Path

from lineage.graph.queries import effective_owners, experts_for
from tests.test_activity import PRS, loaded  # noqa: F401
from tests.test_loader import graph  # noqa: F401

# relationship types that really have parallel edges in the data (measured on the live graph: STAGE_IN 6 pairs, APPROVER_OF 19, REVIEWER_OF 18, EMERITUS_APPROVER_OF 2)
PARALLEL_TYPES = ("STAGE_IN", "APPROVER_OF", "REVIEWER_OF", "EMERITUS_APPROVER_OF")


def test_the_limitation_is_real_on_this_falkordb(graph):  # noqa: F811
    graph.query("CREATE (a:T {n:'a'})-[:E {v:1}]->(b:T {n:'b'}), (a)-[:E {v:2}]->(b)")
    q = lambda c: graph.ro_query(c).result_set[0][0]  # noqa: E731
    assert q("MATCH (a:T)-[e:E]->(b:T) RETURN count(e)") == 2  # bound and referenced: both edges
    assert q("MATCH (a:T)-[:E]->(b:T) RETURN count(b)") == 1  # unreferenced: existence only
    assert q("MATCH (a:T)-[e:E]->(b:T) WHERE ID(e) >= 0 RETURN count(b)") == 2  # the documented workaround


def test_no_query_matches_a_parallel_edge_type_without_referencing_the_relationship():
    bad = []
    for path in Path("lineage").rglob("*.py"):
        if "ingest" in path.parts:
            continue
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            for m in re.finditer(r"\[:([A-Z_|]+)", line):  # "[:TYPE" with no variable before the colon = an unreferenced relationship
                if any(t in m.group(1).split("|") for t in PARALLEL_TYPES) and "MATCH" in line.upper():
                    bad.append(f"{path}:{n}: {line.strip()[:100]}")
    assert not bad, "unreferenced parallel-edge relationship in a query:\n" + "\n".join(bad)


def test_parallel_ownership_edges_are_all_returned_and_activity_counts_each_pr_once(graph):  # noqa: F811
    loaded(graph)
    before = [e for e in effective_owners(graph, "kubernetes", "pkg/volume") if e["role"] == "APPROVER_OF"]
    graph.query("MATCH (c:CodePath {id:'kubernetes:pkg/volume'}) MERGE (p:Person {handle:'twice'}) "
                "CREATE (p)-[:APPROVER_OF {valid_from: 130, valid_to: 133, filter: '', source_url: 'u1'}]->(c) CREATE (p)-[:APPROVER_OF {valid_from: 135, valid_to: NULL, filter: '', source_url: 'u2'}]->(c)")
    after = effective_owners(graph, "kubernetes", "pkg/volume", 138)
    assert len([e for e in after if e["handle"] == "twice" and e["role"] == "APPROVER_OF"]) == 1  # only the interval valid at 1.38 counts
    assert len([e for e in effective_owners(graph, "kubernetes", "pkg/volume", 131) if e["handle"] == "twice"]) == 1  # and the older interval at 1.31: both edges are visible
    assert len([e for e in after if e["role"] == "APPROVER_OF"]) == len(before) + 1
    # a second, parallel TOUCHES edge must not make one PR look like two reviews
    pr1 = experts_for(graph, "kubernetes", "pkg/volume")
    graph.query("MATCH (p:PR {number: 1}), (c:CodePath {id:'kubernetes:pkg/volume/csi'}) CREATE (p)-[:TOUCHES {source_url: 'dup'}]->(c)")
    assert {e["handle"]: e["prs_reviewed"] for e in experts_for(graph, "kubernetes", "pkg/volume")} == {e["handle"]: e["prs_reviewed"] for e in pr1}
