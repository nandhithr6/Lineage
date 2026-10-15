import pytest

from lineage.detect.fix import entered_version, offset_hunks, pick_actual, rewrite_shortcode, unified_diff
from lineage.graph.loader import load_all
from tests.test_loader import graph  # noqa: F401


def test_rewrite_only_touches_state_and_version():
    line = '{{< feature-state for_k8s_version="v1.8" state="alpha" >}}'
    assert rewrite_shortcode(line, "stable", "1.25") == '{{< feature-state for_k8s_version="v1.25" state="stable" >}}'
    swapped = '{{< feature-state state="beta" for_k8s_version="v1.24" >}}'
    assert rewrite_shortcode(swapped, "stable", "1.33") == '{{< feature-state state="stable" for_k8s_version="v1.33" >}}'
    no_version = '{{< feature-state state="alpha" >}}'
    assert rewrite_shortcode(no_version, "stable", "1.25") == '{{< feature-state state="stable" >}}'  # never adds a version


def test_pick_actual_prefers_further_along_stage_on_overlap():
    assert pick_actual(["beta", "stable"]) == "stable" and pick_actual(["alpha"]) == "alpha"


def test_entered_version():
    h = [{"stage": "alpha", "from": "1.7"}, {"stage": "beta", "from": "1.10"}, {"stage": "stable", "from": "1.25"}]
    assert entered_version(h, "stable") == "1.25" and entered_version(h, "deprecated") is None


def test_unified_diff_changes_one_line_only():
    d = unified_diff("a.md", ["x", "OLD", "y"], 2, "NEW")
    changed = [l for l in d.splitlines() if l[:1] in "+-" and not l.startswith(("---", "+++"))]
    assert changed == ["-OLD", "+NEW"] and "--- a/a.md" in d


def test_seed_fix_end_to_end(graph):  # noqa: F811
    from lineage.detect.drift import run_detector, write_findings
    from lineage.detect.fix import draft_fix

    load_all(graph)
    results, _ = run_detector(graph)
    write_findings(graph, results)
    seed = next(r for r in results if r.gate == "LocalStorageCapacityIsolation")
    fix = draft_fix(graph, seed.key)
    assert fix["status"] == "ok" and fix["drafted_by"] == "rules"
    assert '-{{< feature-state for_k8s_version="v1.8" state="alpha" >}}' in fix["diff"]
    assert '+{{< feature-state for_k8s_version="v1.25" state="stable" >}}' in fix["diff"]
    assert "removed" in fix["explanation"] and fix["reviewers"]
    # review items get no draft
    review = next(r for r in results if r.severity == "review")
    assert draft_fix(graph, review.key)["status"] == "not_applicable"
    assert draft_fix(graph, "nope|nope")["status"] == "insufficient_evidence"


def test_offset_hunks_shifts_both_sides():
    d = "--- a/x\n+++ b/x\n@@ -2,5 +2,5 @@\n-a\n+b\n"
    assert "@@ -192,5 +192,5 @@" in offset_hunks(d, 190)
    assert offset_hunks("@@ -3 +3 @@", 10) == "@@ -13 +13 @@"


def test_draft_fix_works_without_a_website_clone(graph, tmp_path):  # noqa: F811
    """On a server the website repo is absent: the diff must still be correct, built from the section text in the graph."""
    import re
    from lineage.detect.drift import run_detector, write_findings
    from lineage.detect.fix import draft_fix

    load_all(graph)
    results, _ = run_detector(graph)
    write_findings(graph, results)
    seed = next(r for r in results if r.gate == "LocalStorageCapacityIsolation")
    with_clone = draft_fix(graph, seed.key)
    without = draft_fix(graph, seed.key, repo_root=tmp_path / "no-clone-here")
    assert without["status"] == "ok"
    changed = lambda d: [x for x in d.splitlines() if x[:1] in "+-" and not x.startswith(("---", "+++"))]
    assert changed(without["diff"]) == changed(with_clone["diff"])
    def changed_page_line(d):  # the page line number of the '-' line: hunk start + context lines before it
        lines = d.splitlines()
        head = next(i for i, x in enumerate(lines) if x.startswith("@@"))
        body = lines[head + 1:]
        start = int(re.search(r"^@@ -(\d+)", d, re.M).group(1))
        return start + next(i for i, x in enumerate(body) if x.startswith("-"))

    assert changed_page_line(with_clone["diff"]) == changed_page_line(without["diff"]) == 194  # real page line, not an excerpt line
