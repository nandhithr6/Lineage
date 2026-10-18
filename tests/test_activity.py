from datetime import datetime, timezone

from lineage.graph.loader import is_bot, load_all, load_prs, nearest_codepath
from lineage.graph.queries import experts_for, official_vs_actual, orphaned_components
from tests.test_loader import graph  # noqa: F401

TODAY = datetime(2026, 10, 15, tzinfo=timezone.utc)


def pr(n, created, dirs, reviews=(), author="zed", sigs=("sig/storage",)):
    return {"number": n, "title": f"PR {n}", "url": f"https://github.com/kubernetes/kubernetes/pull/{n}", "merged_at": created, "created_at": created, "author": author,
            "merged_by": "x", "sigs": list(sigs), "dirs": list(dirs), "files_total": len(dirs), "files_truncated": False, "reviews_total": len(reviews), "reviews_truncated": False,
            "reviews": [{"login": l, "state": s, "at": at} for l, s, at in reviews]}


PRS = [
    pr(1, "2026-10-01T00:00:00Z", ["pkg/volume/csi"], [("msau42", "APPROVED", "2026-10-02T00:00:00Z"), ("bob", "COMMENTED", "2026-10-02T00:00:00Z")]),
    pr(2, "2024-11-01T00:00:00Z", ["pkg/volume"], [("xing-yang", "APPROVED", "2024-11-02T00:00:00Z")]),
    pr(3, "2026-10-03T00:00:00Z", ["pkg/volume"], [("k8s-ci-robot", "COMMENTED", "2026-10-03T00:00:00Z"), ("zed", "COMMENTED", "2026-10-03T00:00:00Z")], author="dependabot[bot]"),
    pr(4, "2026-09-10T00:00:00Z", ["pkg/controller/job"], [("newperson", "APPROVED", "2026-09-11T00:00:00Z")], sigs=("sig/apps",)),
    pr(5, "2026-09-10T00:00:00Z", ["go.mod", ""], [("rootguy", "APPROVED", "2026-09-11T00:00:00Z")]),  # only the repo root: must not become a TOUCHES edge
    pr(6, "2026-10-04T00:00:00Z", ["pkg/volume"], [("releaseman", "APPROVED", "2026-10-05T00:00:00Z")]),
    pr(7, "2026-10-05T00:00:00Z", [f"pkg/controller/{d}" for d in ("job", "cronjob", "daemon", "deployment", "disruption", "history", "replicaset", "replication", "statefulset", "resourceclaim")] + ["pkg/volume"],
       [("sweeper", "APPROVED", "2026-10-06T00:00:00Z")]),
]
PRS[5]["title"] = "Automated cherry pick of #131868: Remove superfluous expansion calls"  # a backport approved by a release manager
PRS[6]["title"] = "Update go to 1.26 everywhere"  # touches 11 code paths: cross-cutting


def test_cherry_pick_uses_the_base_branch_when_known():
    from lineage.graph.loader import is_cherry_pick
    assert is_cherry_pick("Fix X", "release-1.31") and not is_cherry_pick("Automated cherry pick of #1: x", "master")  # the branch wins over the title
    assert is_cherry_pick("Automated cherry pick of #1: x") and not is_cherry_pick("Fix X")  # no branch known: fall back to the title


def test_helpers():
    assert is_bot("k8s-ci-robot") and is_bot("dependabot[bot]") and is_bot(None) and not is_bot("alice")
    assert nearest_codepath("pkg/volume/csi/x", {"pkg", "pkg/volume"}) == "pkg/volume" and nearest_codepath("go.mod", {"pkg"}) is None


def loaded(graph):  # noqa: F811
    load_all(graph, include_prs=False)
    known = {r[0] for r in graph.ro_query("MATCH (c:CodePath {repo:'kubernetes'}) RETURN c.path").result_set}
    return load_prs(graph, PRS, known, "2026-10-15T00:00:00+00:00")


def test_pr_graph_shape_bots_and_root_are_excluded(graph):  # noqa: F811
    counts = loaded(graph)
    assert counts["prs"] == 7 and counts["touches"] == 4 + 1 + 11  # PR5 touches only the root: dropped
    q = lambda c: graph.ro_query(c).result_set  # noqa: E731
    assert q("MATCH (p:Person) WHERE p.handle CONTAINS 'robot' OR p.handle CONTAINS 'bot' RETURN count(p)")[0][0] == 0
    assert q("MATCH (p:Person {handle:'zed'})-[r:REVIEWED]->(:PR {number:3}) RETURN count(r)")[0][0] == 1  # zed reviewed PR 3 (authored by a bot)
    assert q("MATCH (p:Person {handle:'zed'})-[r:REVIEWED]->(:PR {number:1}) RETURN count(r)")[0][0] == 0
    assert q("MATCH (:Person {handle:'msau42'})-[a:APPROVED]->(:PR {number:1}) RETURN a.source_url")[0][0].endswith("/pull/1")
    loaded(graph)  # idempotent
    assert q("MATCH (:Person)-[r:REVIEWED|APPROVED|AUTHORED]->(:PR) RETURN count(r)")[0][0] == counts["authored"] + counts["reviewed"] + counts["approved"]


def test_experts_are_recency_weighted_and_cover_subfolders(graph):  # noqa: F811
    loaded(graph)
    ex = {e["handle"]: e for e in experts_for(graph, "kubernetes", "pkg/volume", TODAY)}
    assert list(ex)[0] == "msau42" and ex["msau42"]["approvals"] == 1 and ex["msau42"]["prs_reviewed"] == 1  # PR1 touched pkg/volume/csi -> mapped to pkg/volume
    assert ex["msau42"]["score"] > ex["bob"]["score"] > ex["xing-yang"]["score"] > 0  # approval beats a comment; a 2-year-old review decays
    assert abs(ex["msau42"]["score"] - 1.5 * 0.5 ** (13 / 180)) < 0.02 and ex["xing-yang"]["score"] < 0.15
    assert ex["msau42"]["examples"][0]["url"].endswith("/pull/1") and ex["msau42"]["last_active"] == "2026-10-02"
    assert "k8s-ci-robot" not in ex
    assert "releaseman" not in ex and "sweeper" not in ex  # cherry-pick approvals and a repo-wide change are not folder expertise
    assert graph.ro_query("MATCH (p:PR) WHERE p.cherry_pick RETURN count(p)").result_set[0][0] == 1
    assert graph.ro_query("MATCH (p:PR) WHERE p.cross_cutting RETURN count(p)").result_set[0][0] == 1


def test_official_vs_actual_and_orphans(graph):  # noqa: F811
    loaded(graph)
    v = official_vs_actual(graph, "kubernetes", "pkg/volume", 12, TODAY)
    assert "msau42" in v["active_approvers"] and "xing-yang" in v["inactive_approvers"] and "bob" in v["unlisted_active"] and "zed" in v["unlisted_active"]
    orphans = {o["path"]: o for o in orphaned_components(graph, 12, TODAY)}
    assert "pkg/volume" not in orphans  # an approver was active
    assert "pkg/controller/job" in orphans and "newperson" in orphans["pkg/controller/job"]["unlisted_active"]  # active, but not a listed owner
    assert all(o["path"] for o in orphans.values())  # the root is never reported
    assert all(o["active_people"] > 0 for o in orphans.values())  # orphaned = someone worked there; silence alone is "dormant", not a risk
    from lineage.graph.queries import dormant_components
    dormant = {d["path"] for d in dormant_components(graph, 12, TODAY)}
    assert dormant.isdisjoint(orphans)
    assert official_vs_actual(graph, "kubernetes", "pkg/volume", 36, TODAY)["inactive_approvers"] != v["inactive_approvers"]  # a longer window changes the answer


def test_tools_and_api_expose_activity(graph):  # noqa: F811
    from fastapi.testclient import TestClient

    from lineage.agent.tools import ToolBox
    from lineage.api.main import app, get_graph

    loaded(graph)
    tb = ToolBox(graph)
    b = tb.experts_for("pkg/volume")
    types = {e["type"] for e in b["evidence"]}
    assert b["status"] == "ok" and types == {"REVIEWS_ACTIVELY", "OfficialVsActual"} and b["evidence"][0]["source_url"].endswith("/pull/1")
    ov = next(e for e in b["evidence"] if e["type"] == "OfficialVsActual")
    assert "sig/storage" in ov["props"]["coverage"] and "bob" in ov["props"]["active_but_not_listed"]
    assert tb.experts_for("pkg/kubelet/cm")["status"] == "insufficient_evidence" and "census" in tb.experts_for("pkg/nothing")["params"]["note"]
    assert any(e["props"].get("active_but_not_listed") == [] or "newperson" in e["props"].get("active_but_not_listed", []) for e in tb.orphaned_components()["evidence"])
    app.dependency_overrides[get_graph] = lambda: graph
    try:
        c = TestClient(app)
        r = c.get("/experts/kubernetes/pkg/volume").json()
        assert r["experts"][0]["handle"] == "msau42" and "xing-yang" in r["listed_but_inactive"] and "census" in r["coverage_note"]
        assert c.get("/experts/website/content/en").status_code == 422
        assert "pkg/controller/job" in {o["path"] for o in c.get("/orphans").json()}
    finally:
        app.dependency_overrides.clear()


def test_reviewer_suggestions_use_activity_and_drop_inactive_owners(graph):  # noqa: F811
    from lineage.detect.drift import run_detector, write_findings
    from lineage.graph.trace import build_trace, suggest_reviewers

    load_all(graph, include_prs=False)
    base = {r["handle"] for r in suggest_reviewers(graph, "content/en/docs/concepts/policy/resource-quotas.md", "sig-storage", "sig-storage/361-local-ephemeral-storage-isolation")}
    assert "xing-yang" in base  # without activity data, listed approvers are suggested as before
    known = {r[0] for r in graph.ro_query("MATCH (c:CodePath {repo:'kubernetes'}) RETURN c.path").result_set}
    load_prs(graph, PRS, known, "2026-10-15T00:00:00+00:00")
    got = {r["handle"]: r for r in suggest_reviewers(graph, "content/en/docs/concepts/policy/resource-quotas.md", "sig-storage", "sig-storage/361-local-ephemeral-storage-isolation", today=TODAY)}
    # xing-yang is a listed approver AND chair, but was active only 2 years ago (data exists for these paths): the 'code approver' credit is dropped, other roles stay
    assert "code" not in {x["category"] for x in got["xing-yang"]["reasons"]} and "lead" in {x["category"] for x in got["xing-yang"]["reasons"]}
    assert "active" not in {x["category"] for x in got["xing-yang"]["reasons"]}  # a review two years ago is not activity
    assert "active" in {x["category"] for x in got["msau42"]["reasons"]} and "bob" in got


def test_pagerank_ranks_the_reviewer_everyone_relies_on_first(graph):  # noqa: F811
    """algo.pageRank('Person','REVIEWED_BY'): rank flows author -> reviewer, so being relied on by people who are themselves relied on counts most.
    Here r is relied on by 4 people and itself relies on s, so s (who collects r's rank as well as a's) edges ahead of r; both far exceed everyone else."""
    from lineage.graph.queries import review_network_top

    graph.query("UNWIND ['a','b','c','d','r','s','lonely'] AS h MERGE (:Person {handle: h})")
    graph.query("UNWIND [['a','r',3],['b','r',1],['c','r',2],['d','r',1],['r','s',4],['a','s',1]] AS e "
                "MATCH (x:Person {handle:e[0]}), (y:Person {handle:e[1]}) MERGE (x)-[:REVIEWED_BY {count: e[2]}]->(y)")
    graph.query("MATCH (p:Person {handle:'r'}), (c:CodePath) WITH p, c LIMIT 1 MERGE (p)-[:APPROVER_OF {valid_from: 130}]->(c)") if graph.ro_query("MATCH (c:CodePath) RETURN count(c)").result_set[0][0] else None
    top = review_network_top(graph, 10)
    assert [t["handle"] for t in top][:2] == ["s", "r"] and top[0]["score"] > top[1]["score"] > 3 * top[2]["score"]
    r = next(t for t in top if t["handle"] == "r")
    assert r["authors_served"] == 4 and r["reviews"] == 7  # 3+1+2+1, counted once (not multiplied by the approver join)
    assert {t["handle"] for t in top[2:]} == {"a", "b", "c", "d", "lonely"}
    assert abs(sum(t["score"] for t in review_network_top(graph, 100)) - 1.0) < 0.05
    assert next(t for t in top if t["handle"] == "lonely")["authors_served"] == 0


def test_issues_and_pr_kep_links_are_loaded_with_provenance_and_shown_in_the_trace(graph):  # noqa: F811
    from lineage.detect.drift import run_detector, write_findings
    from lineage.graph.loader import load_issues, load_pr_keps
    from lineage.graph.trace import build_trace

    load_all(graph, include_prs=False)
    write_findings(graph, run_detector(graph)[0])
    known = {r[0] for r in graph.ro_query("MATCH (c:CodePath {repo:'kubernetes'}) RETURN c.path").result_set}
    prs = [dict(p) for p in PRS[:2]]
    prs[0]["closes"] = [{"repo": "kubernetes/kubernetes", "number": 77, "title": "Quota is wrong", "url": "https://github.com/kubernetes/kubernetes/issues/77", "state": "CLOSED",
                         "created_at": "2025-01-01T00:00:00Z", "closed_at": "2026-10-01T00:00:00Z"}]
    load_prs(graph, prs, known, "2026-10-15T00:00:00+00:00")
    assert load_issues(graph, prs, "2026-10-15T00:00:00+00:00") == (1, 1)
    assert load_issues(graph, prs, "2026-10-15T00:00:00+00:00") == (1, 1)  # idempotent
    kep = "sig-storage/361-local-ephemeral-storage-isolation"
    links = [{"pr": 1, "kep": kep, "method": "kep_url", "confidence": 0.95, "evidence": "keps/sig-storage/361", "source_url": prs[0]["url"]},
             {"pr": 2, "kep": kep, "method": "feature_gate_name", "confidence": 0.5, "evidence": "names feature gate X", "source_url": prs[1]["url"]}]
    assert load_pr_keps(graph, links, "2026-10-15T00:00:00+00:00") == 2
    q = lambda c: graph.ro_query(c).result_set  # noqa: E731
    assert q("MATCH (:PR)-[e:FIXES]->(i:Issue {id:'kubernetes/kubernetes#77'}) RETURN e.extracted_by, e.confidence, e.source_url, i.state")[0] == ["structured", 1.0, prs[0]["url"], "CLOSED"]
    row = q("MATCH (:PR {number:1})-[e:IMPLEMENTS]->(:KEP) RETURN e.method, e.confidence, e.extracted_by, e.source_url, e.recorded_at")[0]
    assert row[:3] == ["kep_url", 0.95, "inferred"] and row[3].endswith("/pull/1") and row[4]
    load_pr_keps(graph, links[:1], "2026-10-16T00:00:00+00:00")  # derived hypotheses are replaced, not accumulated
    assert q("MATCH ()-[e:IMPLEMENTS]->() RETURN count(e)")[0][0] == 1
    k = build_trace(graph, "resource-quotas")["sections"]["kep"]
    assert k["prs_strong"] == 1 and k["prs_weak"] == 0 and [p["number"] for p in k["prs"]] == [1] and k["prs"][0]["issues"][0]["id"] == "kubernetes/kubernetes#77"


def test_pr_reviews_tool_answers_from_the_loaded_pull_requests(graph):  # noqa: F811
    from lineage.agent.tools import ToolBox
    loaded(graph)
    known = {r[0] for r in graph.ro_query("MATCH (c:CodePath {repo:'kubernetes'}) RETURN c.path").result_set}
    load_prs(graph, [pr(90, "2026-10-01T00:00:00Z", ["pkg/volume"], [])], known, "2026-10-15T00:00:00+00:00")  # loaded, no formal review
    tools = ToolBox(graph)
    done = tools.call("pr_reviews", {"number": 1})
    assert done["outcome"] == "answered_with_path"
    head = done["evidence"][0]
    assert head["type"] == "PR" and head["props"]["formal_reviews_recorded"] == 2 and head["source_url"].endswith("/pull/1")
    reviews = {e["from"]: e for e in done["evidence"] if e["type"] == "REVIEWED"}
    assert reviews["Person:msau42"]["props"]["approved"] is True and reviews["Person:bob"]["props"]["approved"] is False and reviews["Person:msau42"]["source_url"]
    none = tools.call("pr_reviews", {"number": 90})  # loaded with no review: that is an ANSWER, stated as such
    assert none["outcome"] == "answered_with_path" and none["evidence"][0]["props"]["formal_reviews_recorded"] == 0 and "no formal review" in none["evidence"][0]["props"]["note"]
    missing = tools.call("pr_reviews", {"number": 424242})  # not loaded: never reported as "no reviews"
    assert missing["outcome"] == "not_in_graph" and "not among the loaded data" in missing["params"]["note"] and "not evidence that it had none" in missing["params"]["note"]
