from lineage.link.pr_kep import STRONG, build_indexes, gate_to_keps, links_for_pr, resolve_number

KEPS = [
    {"id": "sig-storage/361-local-ephemeral-storage-isolation", "number": 361, "owning_sig": "sig-storage", "participating_sigs": ["sig-node"], "feature_gates": ["LocalStorageCapacityIsolation"]},
    {"id": "sig-node/1029-ephemeral-storage-quotas", "number": 1029, "owning_sig": "sig-node", "participating_sigs": [], "feature_gates": ["LocalStorageCapacityIsolationFSQuotaMonitoring"]},
    {"id": "sig-apps/2000-job-thing", "number": 2000, "owning_sig": "sig-apps", "participating_sigs": [], "feature_gates": []},
    {"id": "sig-node/2000-other-thing", "number": 2000, "owning_sig": "sig-node", "participating_sigs": [], "feature_gates": []},
    {"id": "sig-network/3000-lonely", "number": 3000, "owning_sig": "sig-network", "participating_sigs": [], "feature_gates": ["TwoKepsGate1", "TwoKepsGate1"]},
]
BY_ID, BY_NUMBER, _ = build_indexes(KEPS)
GATES = gate_to_keps(KEPS)


def pr(title, body="", sigs=("sig/storage",), number=1):
    return {"number": number, "title": title, "body": body, "sigs": list(sigs)}


def links(p):
    return {x["kep"]: x for x in links_for_pr(p, BY_ID, BY_NUMBER, GATES)}


def test_kep_url_is_exact_and_strongest():
    got = links(pr("Add quota", "KEP: https://github.com/kubernetes/enhancements/tree/master/keps/sig-storage/361-local-ephemeral-storage-isolation/README.md"))
    x = got["sig-storage/361-local-ephemeral-storage-isolation"]
    assert x["method"] == "kep_url" and x["confidence"] == 0.95 and "361-local-ephemeral" in x["evidence"]


def test_number_mentions_use_the_prs_own_sig_to_disambiguate():
    assert links(pr("x", "Part of KEP-361", sigs=("sig/storage",)))["sig-storage/361-local-ephemeral-storage-isolation"]["confidence"] == 0.90
    assert links(pr("x", "Part of KEP-361", sigs=("sig/node",)))["sig-storage/361-local-ephemeral-storage-isolation"]["confidence"] == 0.90  # shared with sig-node
    got = links(pr("x", "KEP 1029", sigs=("sig/apps",)))  # unique number, other SIG: weaker
    assert got["sig-node/1029-ephemeral-storage-quotas"]["confidence"] == 0.70 and got["sig-node/1029-ephemeral-storage-quotas"]["method"] == "kep_number"
    assert links(pr("x", "Tracking issue: kubernetes/enhancements#1029"))["sig-node/1029-ephemeral-storage-quotas"]["method"] == "enhancement_issue"


def test_an_ambiguous_number_is_not_guessed():
    assert resolve_number(2000, {"sig-storage"}, BY_NUMBER) is None  # two KEPs share 2000, neither is sig-storage
    assert resolve_number(2000, {"sig-apps"}, BY_NUMBER)[0] == "sig-apps/2000-job-thing"
    assert resolve_number(999, {"sig-apps"}, BY_NUMBER) is None  # no such KEP
    assert links(pr("fix", "Related to KEP-2000", sigs=("sig/storage",))) == {}


def test_enhancement_pull_urls_are_not_kep_numbers():
    assert links(pr("x", "see https://github.com/kubernetes/enhancements/pull/361")) == {}  # a PR number, not a KEP/issue number


def test_gate_name_mention_is_weak_and_whole_word_only():
    got = links(pr("Fix LocalStorageCapacityIsolation when disabled"))
    assert got["sig-storage/361-local-ephemeral-storage-isolation"]["method"] == "feature_gate_name" and got["sig-storage/361-local-ephemeral-storage-isolation"]["confidence"] < STRONG
    assert links(pr("Rename MyLocalStorageCapacityIsolationX helper")) == {}
    assert "sig-node/1029-ephemeral-storage-quotas" not in links(pr("Fix LocalStorageCapacityIsolation"))  # the longer gate name is a different gate


def test_a_gate_shared_by_two_keps_is_ambiguous_and_short_names_are_ignored():
    keps = KEPS + [{"id": "sig-x/1-a", "number": 1, "owning_sig": "sig-x", "participating_sigs": [], "feature_gates": ["SharedGateName"]},
                   {"id": "sig-y/2-b", "number": 2, "owning_sig": "sig-y", "participating_sigs": [], "feature_gates": ["SharedGateName", "Short"]}]
    g = gate_to_keps(keps)
    assert "SharedGateName" not in g and "Short" not in g and "LocalStorageCapacityIsolation" in g


def test_best_method_wins_per_kep_and_output_is_sorted_strongest_first():
    p = pr("LocalStorageCapacityIsolation quota", "KEP-361 and https://github.com/kubernetes/enhancements/tree/master/keps/sig-storage/361-local-ephemeral-storage-isolation")
    out = links_for_pr(p, BY_ID, BY_NUMBER, GATES)
    assert len(out) == 1 and out[0]["method"] == "kep_url"


def test_membership_prs_that_merely_list_keps_are_not_implementations():
    body = "Work: https://github.com/kubernetes/enhancements/tree/master/keps/sig-storage/361-local-ephemeral-storage-isolation KEP-361"
    for title in ("self-nominate alice to be a sig-node reviewer", "Add bob as approver for pkg/x", "Move carol to emeritus", "Update OWNERS for pkg/x", "Nominate dave for sig-storage"):
        assert links(pr(title, body)) == {}, title
    assert links(pr("Implement local ephemeral storage isolation", body))  # a real change still links
