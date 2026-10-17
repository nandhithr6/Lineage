from eval.baseline_rag import Passage, Retriever, tokens
from eval.build_gold import build
from eval.run_qa import evaluate_answer, fact_coverage, passes, source_hit, summarise, svg_chart
from lineage.agent.llm import FakeLLM, LLMResponse, ToolCall
from lineage.agent.tools import ToolBox
from lineage.graph.loader import load_all
from tests.test_loader import graph  # noqa: F401


def test_check_semantics():
    checks = [{"all": ["stable", "1.25"]}, {"any": ["stale", "out of date"], "min": 1, "phrase": True}]
    assert passes(checks, "It is out of date: stable since 1.25", True)
    assert not passes(checks, "It is out of date: stable", True)  # missing 1.25
    assert not passes(checks, "Stable since 1.25", True)  # missing the yes/no wording
    assert passes(checks, "Stable since 1.25", False)  # raw evidence: wording checks are skipped
    assert fact_coverage(checks, "stable only") == 0.5
    assert passes([{"any": ["a", "b", "c"], "min": 2}], "a and c", False) and not passes([{"any": ["a", "b", "c"], "min": 2}], "only a", False)


def test_refusal_scoring():
    q = {"category": "unanswerable", "checks": [{"refusal": True}], "sources": []}
    assert evaluate_answer(q, "Insufficient evidence: nothing found.", set())["correct"]
    assert not evaluate_answer(q, "It is Alice.", set())["correct"]


def test_source_hit_ignores_fragments():
    assert source_hit(["https://x/y.md#L19"], {"https://x/y.md#L21"}) and not source_hit(["https://x/y.md"], {"https://x/z.md"}) and not source_hit([], {"a"})


def test_gold_set_shape_and_independence_from_the_system():
    qs = build()
    cats = {}
    for q in qs:
        cats[q["category"]] = cats.get(q["category"], 0) + 1
        assert q["status"].startswith("DRAFT") and q["checks"]
        if q["category"] != "unanswerable":
            assert q["sources"] and q["oracle_tool"]["name"]
    assert cats == {c: 6 for c in ["ownership", "expertise", "time_travel", "decision_lineage", "drift", "multi_hop", "unanswerable"]}
    assert len({q["id"] for q in qs}) == len(qs)


def test_retriever_ranks_the_relevant_passage_first():
    ps = [Passage("a", "Team sig-storage chairs gnufied xing-yang", "u1"), Passage("b", "Team sig-node chairs haircommander", "u2"), Passage("c", "unrelated cooking recipe", None)]
    assert Retriever(ps).search("who chairs sig-storage", 1)[0][0].id == "a"
    assert "storage" in tokens("sig-storage")  # hyphenated names are also indexed by their parts


def test_chart_is_valid_svg_with_all_categories():
    rows = [{"category": c, "lineage_pass": True, "lineage_cov": 1, "lineage_src": True, "rag_pass": False, "rag_cov": 0, "rag_src": False}
            for c in ["ownership", "drift"]]
    svg = svg_chart(summarise(rows), "t", "lineage_pass", "rag_pass", "A", "B", "note")
    assert svg.startswith("<svg") and svg.endswith("</svg>") and "ownership" in svg and "100%" in svg and "0%" in svg


def test_llm_mode_runs_end_to_end_with_a_scripted_model(graph, tmp_path):  # noqa: F811
    """The harness itself works: same model for both systems, resumable output, scoring, error handling. (No real model is used.)"""
    from eval.run_qa import llm_mode, summarise_llm
    from lineage.agent.llm import ProviderError

    load_all(graph)
    q = {"id": "own4", "category": "ownership", "question": "Who are the chairs of sig-storage?", "checks": [{"all": ["gnufied", "xing-yang"]}],
         "sources": ["https://github.com/kubernetes/community/blob/x/sigs.yaml"], "oracle_tool": None}
    un = {"id": "un1", "category": "unanswerable", "question": "Who reviewed the most PRs?", "checks": [{"refusal": True}], "sources": [], "oracle_tool": None}
    llm = FakeLLM([LLMResponse(tool_calls=[ToolCall("c", "sig_leaders", {"sig": "sig-storage"})]),
                   LLMResponse("The chairs are gnufied [e2] and xing-yang [e3]."),   # lineage, question 1
                   LLMResponse("The chairs are gnufied and xing-yang [p1]."),          # rag, question 1
                   LLMResponse("Insufficient evidence."),                              # lineage, question 2 (no tool called -> refusal in code)
                   LLMResponse("I cannot tell from the passages: insufficient evidence.")])  # rag, question 2
    out = tmp_path / "rows.jsonl"
    retriever = Retriever([Passage("a", "Team sig-storage chairs gnufied xing-yang", "https://x/sigs.yaml")])
    rows = llm_mode([q, un], llm, ToolBox(graph), retriever, out=out)
    s = summarise_llm(rows)
    assert all(r["correct"] for r in rows) and len(rows) == 4
    assert s["lineage"]["accuracy"] == 1.0 and s["lineage"]["refusal_quality"] == 1.0 and s["rag"]["accuracy"] == 1.0 and s["lineage"]["median_latency_s"] is not None
    # resuming skips what is already on disk: a model that would raise is never called
    class Boom:
        def chat(self, *a, **k):
            raise AssertionError("must not be called")
    assert len(llm_mode([q, un], Boom(), ToolBox(graph), retriever, out=out)) == 4
    assert len(out.read_text().splitlines()) == 4


def test_provider_failure_is_recorded_not_fatal(graph, tmp_path):  # noqa: F811
    from eval.run_qa import llm_mode, summarise_llm
    from lineage.agent.llm import ProviderError

    class Down:
        def chat(self, *a, **k):
            raise ProviderError("quota exhausted")
    q = {"id": "own4", "category": "ownership", "question": "Who chairs sig-storage?", "checks": [{"all": ["gnufied"]}], "sources": ["u"], "oracle_tool": None}
    rows = llm_mode([q], Down(), ToolBox(graph), Retriever([Passage("a", "x", None)]), out=tmp_path / "r.jsonl")
    assert [r["status"] for r in rows] == ["error", "error"] and not any(r["correct"] for r in rows)
    assert summarise_llm(rows)["lineage"]["errors"] == 1  # reported separately from wrong answers


def test_checks_accept_a_handle_or_a_display_name():
    checks = [{"all": [["gnufied", "Hemant Kumar"], ["xing-yang", "Xing Yang"]]}]
    assert passes(checks, "The chairs are Xing Yang and Hemant Kumar.", False)
    assert passes(checks, "chairs: gnufied, xing-yang", False)
    assert not passes(checks, "The chairs are Xing Yang.", False)
    assert fact_coverage(checks, "Xing Yang only") == 0.5


def test_a_frozen_heldout_set_cannot_be_edited_and_scored(tmp_path, monkeypatch):
    import hashlib
    import json as _json

    import eval.run_qa as R
    gold = tmp_path / "gold_heldout.jsonl"
    gold.write_text('{"id": "h1"}\n')
    freeze = tmp_path / "freeze.json"
    freeze.write_text(_json.dumps({"sha256": hashlib.sha256(gold.read_bytes()).hexdigest()}))
    real_path = R.Path
    monkeypatch.setattr(R, "Path", lambda p: freeze if str(p).endswith("heldout_freeze.json") else real_path(p))
    R.verify_frozen(gold)  # unchanged: fine
    gold.write_text('{"id": "h1", "edited": true}\n')
    import pytest
    with pytest.raises(SystemExit) as e:
        R.verify_frozen(gold)
    assert "edited" in str(e.value)
    R.verify_frozen(tmp_path / "gold_questions.jsonl")  # other files are not frozen


def test_retry_errors_reruns_only_provider_failures_and_summary_separates_them(graph, tmp_path):  # noqa: F811
    from eval.run_qa import llm_mode, summarise_llm
    from lineage.agent.llm import ProviderError

    load_all(graph)
    q = {"id": "own4", "category": "ownership", "question": "Who chairs sig-storage?", "checks": [{"all": ["gnufied"]}], "sources": ["u"], "oracle_tool": None}
    out = tmp_path / "rows.jsonl"

    class Down:
        def chat(self, *a, **k):
            raise ProviderError("503 high demand")

    rows = llm_mode([q], Down(), ToolBox(graph), Retriever([Passage("a", "gnufied chairs sig-storage", "u")]), out=out)
    s = summarise_llm(rows)
    assert s["lineage"]["errors"] == 1 and s["lineage"]["accuracy"] == 0.0 and s["lineage"]["n_answered"] == 0 and s["lineage"]["accuracy_among_answered"] is None
    ok = FakeLLM([LLMResponse(tool_calls=[ToolCall("c", "sig_leaders", {"sig": "sig-storage"})]), LLMResponse("gnufied chairs it [e2]."), LLMResponse("gnufied [p1]")])
    rows = llm_mode([q], ok, ToolBox(graph), Retriever([Passage("a", "gnufied chairs sig-storage", "u")]), out=out, retry_errors=True)
    assert all(r["status"] != "error" for r in rows) and len(out.read_text().splitlines()) == 2
    assert summarise_llm(rows)["lineage"]["errors"] == 0


def test_expertise_source_accepts_any_pr_that_shows_the_claim():
    """Held-out run: the answer named real reviewers and cited their latest PR, but the gold file lists three other sample PRs, so every expertise 'source' failed."""
    from eval.run_qa import evaluate_answer, expertise_source_ok

    q = {"category": "expertise", "question": "Who reviews pkg/x?", "checks": [{"any": [["alice", "Alice A"], "bob"], "min": 1}],
         "sources": ["https://github.com/kubernetes/kubernetes/pull/1"], "oracle_tool": {"name": "experts_for", "args": {"path": "pkg/x"}}}
    prs = {"https://github.com/kubernetes/kubernetes/pull/9": {"dirs": ["pkg/x/sub"], "reviews": [{"login": "Alice"}]},
           "https://github.com/kubernetes/kubernetes/pull/8": {"dirs": ["pkg/other"], "reviews": [{"login": "alice"}]},
           "https://github.com/kubernetes/kubernetes/pull/7": {"dirs": ["pkg/x"], "reviews": [{"login": "carol"}]}}
    answer = "alice reviews it [e1]"
    assert expertise_source_ok(q, answer, {"https://github.com/kubernetes/kubernetes/pull/9"}, prs)
    assert not expertise_source_ok(q, answer, {"https://github.com/kubernetes/kubernetes/pull/8"}, prs)  # reviewed by alice, but a different folder
    assert not expertise_source_ok(q, answer, {"https://github.com/kubernetes/kubernetes/pull/7"}, prs)  # right folder, reviewer is not the person named
    assert not expertise_source_ok(q, "nobody useful", {"https://github.com/kubernetes/kubernetes/pull/9"}, prs)
    r = evaluate_answer(q, answer, {"https://github.com/kubernetes/kubernetes/pull/9"}, prs)
    assert r["correct"] and r["source_ok"] and not r["source_ok_strict"]
    other = dict(q, category="ownership", oracle_tool={"name": "owners_of", "args": {"path": "pkg/x"}})
    assert not evaluate_answer(other, answer, {"https://github.com/kubernetes/kubernetes/pull/9"}, prs)["source_ok"]  # the fair rule is for expertise only
