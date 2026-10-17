from lineage.detect.drift import HistoryRow, evaluate_claim


def hist(*spans):
    """spans: (stage, first_minor, last_minor) -> one HistoryRow per release; overlapping releases get overlap=True."""
    rows = []
    for stage, a, b in spans:
        rows += [HistoryRow(100 + m, f"1.{m}", stage) for m in range(a, b + 1)]
    return rows


SEED = hist(("alpha", 7, 9), ("beta", 10, 24), ("stable", 25, 26))  # LocalStorageCapacityIsolation, removed after 1.26


def test_seed_style_positive_is_a_stale_stage_finding():
    ev = evaluate_claim("c1", "alpha", "1.8", "LocalStorageCapacityIsolation", True, SEED)
    assert ev and ev.kind == "stale_stage" and ev.severity == "finding"
    assert ev.actual_stages == ["stable"] and ev.release == "1.26" and ev.gate_removed
    assert ev.evidence == [{"gate": "LocalStorageCapacityIsolation", "release": "1.26", "stage": "stable"}]
    assert ev.key == "stale_stage|c1|LocalStorageCapacityIsolation|1.26|alpha"


def test_correct_label_stays_silent():
    assert evaluate_claim("c2", "stable", "1.25", "LocalStorageCapacityIsolation", True, SEED) is None


def test_overlap_either_stage_counts_as_correct():
    both = [HistoryRow(130, "1.30", "beta", True), HistoryRow(130, "1.30", "stable", True)]
    assert evaluate_claim("c3", "beta", None, "PodHostIPs", False, both) is None
    assert evaluate_claim("c4", "stable", None, "PodHostIPs", False, both) is None
    assert evaluate_claim("c5", "alpha", None, "PodHostIPs", False, both).kind == "stale_stage"


def test_removed_after_alpha_is_a_review_item_not_a_finding():
    ev = evaluate_claim("c6", "alpha", "1.11", "SomeDroppedGate", True, hist(("alpha", 11, 14)))
    assert ev and ev.kind == "documents_removed_feature" and ev.severity == "review"


def test_removed_after_deprecated_is_a_review_item():
    ev = evaluate_claim("c7", "beta", None, "Gone", True, hist(("beta", 5, 8), ("deprecated", 9, 12)))
    assert ev and ev.severity == "review"


def test_wrong_since_version_is_minor():
    ev = evaluate_claim("c9", "alpha", "1.8", "G", False, hist(("alpha", 7, 37)))
    assert ev and ev.kind == "wrong_since_version" and ev.severity == "minor" and ev.entered_version == "1.7"


def test_no_version_in_label_cannot_raise_a_since_finding():
    assert evaluate_claim("c10", "alpha", None, "G", False, hist(("alpha", 7, 37))) is None


def test_live_gate_stale_stage():
    ev = evaluate_claim("c11", "beta", "1.20", "G", False, hist(("beta", 20, 30), ("stable", 31, 37)))
    assert ev and ev.kind == "stale_stage" and ev.release == "1.37" and not ev.gate_removed


from lineage.detect.drift import judge_claim


def test_runner_up_that_fits_exactly_turns_a_finding_into_a_review_item():
    history = {"GRPCContainerProbeTLS": hist(("alpha", 37, 37)), "GRPCContainerProbe": hist(("beta", 24, 26), ("stable", 27, 37))}
    ranked = [(0.955, "GRPCContainerProbeTLS", False, "linker", True), (0.817, "GRPCContainerProbe", False, "linker", True)]
    ev = judge_claim("c1", "stable", "1.27", ranked, history)
    assert ev and ev.kind == "link_ambiguous" and ev.severity == "review"


def test_weak_runner_up_does_not_veto_a_real_finding():
    history = {"A": hist(("beta", 20, 30), ("stable", 31, 37)), "B": hist(("beta", 20, 37))}
    ranked = [(0.93, "A", False, "linker", True), (0.35, "B", False, "linker", True)]  # B fits (beta) but is below 0.5
    ev = judge_claim("c2", "beta", "1.20", ranked, history)
    assert ev and ev.kind == "stale_stage"


from lineage.detect.drift import is_anchored


def test_unanchored_link_gives_possible_drift_not_a_finding():
    history = {"A": hist(("beta", 20, 30), ("stable", 31, 37))}
    ev = judge_claim("c3", "beta", "1.20", [(0.93, "A", False, "linker", False)], history)
    assert ev and ev.kind == "possible_drift" and ev.severity == "review"
    ev = judge_claim("c3", "beta", "1.20", [(0.93, "A", False, "linker", True)], history)
    assert ev and ev.kind == "stale_stage" and ev.severity == "finding"


def test_anchoring():
    assert is_anchored("linker", '{"name_mention": 0.9, "kep": 0.5}')
    assert not is_anchored("linker", '{"name_mention": 0.5, "kep": 0.8, "text_sim": 0.8}')  # bare mention is not an anchor
    assert not is_anchored("linker", '{"kep": 0.8, "text_sim": 0.9}')
    assert is_anchored("manual", None) and not is_anchored("linker", None)


def test_style_guide_labels_are_never_drift_eligible():
    from lineage.graph.loader import is_drift_eligible

    assert not is_drift_eligible("hardcoded", "content/en/docs/contribute/style/hugo-shortcodes/index.md")
    assert is_drift_eligible("hardcoded", "content/en/docs/concepts/policy/resource-quotas.md")
    assert not is_drift_eligible("gate_lookup", "content/en/docs/concepts/policy/resource-quotas.md")


def test_anchor_by_elimination_needs_a_clear_margin():
    from lineage.detect.drift import anchor_reason

    elim = '[{"gate": "X", "would_be_confidence": 0.9}]'
    assert anchor_reason("linker", "{}", elim, 0.84, 0.44) == "elimination"
    assert anchor_reason("linker", "{}", elim, 0.84, 0.50) is None  # next survivor not clearly behind
    assert anchor_reason("linker", "{}", elim, 0.79, 0.10) is None  # not confident
    assert anchor_reason("linker", "{}", "[]", 0.95, 0.10) is None  # nothing was eliminated: similar-sounding is not an anchor
    weak = '[{"gate": "X", "would_be_confidence": 0.49}]'
    assert anchor_reason("linker", "{}", weak, 0.95, 0.10) is None  # the eliminated rival was never a real contender
    assert anchor_reason("linker", '{"name_mention": 0.9}', None, 0.9, 0.9) == "name_mention"


def test_a_label_without_a_valid_stage_is_never_drift_eligible():
    """Found by re-ingesting October 2025: `{{< feature-state feature_gate="X" >}}` has no state, so there is nothing to compare."""
    from lineage.graph.loader import is_drift_eligible

    assert not is_drift_eligible("hardcoded", "content/en/docs/a.md", None)
    assert not is_drift_eligible("hardcoded", "content/en/docs/a.md", "experimental")


def test_feature_gate_shortcode_variant_is_a_gate_lookup():
    from pathlib import Path

    from lineage.ingest.docs_labels import extract_labels

    text = "## Gate\n{{< feature-state feature_gate=\"PodTopologyLabelsAdmission\" >}}\n"
    (label,) = extract_labels(text, "content/en/docs/a.md", Path("a.md"), "0" * 40)
    assert label.kind == "gate_lookup" and label.gate_name == "PodTopologyLabelsAdmission" and label.state is None
