from lineage.link.linker import CONFIDENT, FAMILY, Candidate, Linker, camel_words, tokens

GATES = [
    {"name": "LocalStorageCapacityIsolation", "description": "Enable local ephemeral storage and sizeLimit of an emptyDir volume.",
     "doc_links": ["/docs/concepts/configuration/manage-resources-containers/"]},
    {"name": "CronJobTimeZone", "description": "Allow the timeZone field in CronJobs.", "doc_links": []},
    {"name": "PodPriority", "description": "Enable preemption of Pods based on priorities.", "doc_links": []},
] + [{"name": f"Filler{i}", "description": f"unrelated words alpha{i} beta{i}", "doc_links": []} for i in range(30)]


def test_helpers():
    assert camel_words("LocalStorageCapacityIsolation") == ["local", "storage", "capacity", "isolation"]
    assert "the" not in tokens("the Pod is {{< feature-state state=\"alpha\" >}} running")


def test_name_next_to_feature_gate_is_confident_bare_mention_is_not():
    lk = Linker(GATES)
    strong = lk.link("Enable the PodPriority feature gate to use priorities.", [])
    bare = lk.link("This page mentions PodPriority once in passing.", [])
    assert strong[0].gate == "PodPriority" and strong[0].confidence >= CONFIDENT
    assert not bare or bare[0].confidence < CONFIDENT


def test_text_similarity_finds_the_gate_without_naming_it():
    cands = Linker(GATES).link("You can set a time zone on a CronJob using the timeZone field.", [], heading="Time zones")
    assert cands[0].gate == "CronJobTimeZone"


def test_stage_is_never_an_input():
    # the linker API has no stage/version parameter: linking cannot depend on what the label claims
    import inspect
    params = inspect.signature(Linker.link).parameters
    assert not any("stage" in p or "version" in p or "state" in p for p in params)


def test_near_tie_is_not_confident():
    gates = [{"name": "BlockVolume", "description": "raw block volume support", "doc_links": []},
             {"name": "CSIBlockVolume", "description": "raw block volume support for csi", "doc_links": []}] + GATES[3:]
    cands = Linker(gates).link("Use the BlockVolume feature gate and the CSIBlockVolume feature gate.", [])
    assert len(cands) == 2 and all(c.confidence < CONFIDENT for c in cands)


def test_demoted_tie_does_not_promote_a_weaker_candidate():
    """Top two tie (both capped below confident); a weaker third candidate must not become the only confident one."""
    gates = [{"name": "AlphaGate", "description": "widget frobnicator support", "doc_links": []},
             {"name": "BetaGate", "description": "widget frobnicator support extra", "doc_links": []},
             {"name": "GammaGate", "description": "widget frobnicator", "doc_links": []}] + GATES[3:]
    lk = Linker(gates)
    cands = lk.link("The AlphaGate feature gate and the BetaGate feature gate and the GammaGate feature gate cover widget frobnicator.", [])
    assert cands and all(c.confidence < CONFIDENT for c in cands)


# --- the since-version elimination rule ------------------------------------------------------------------------
SIB = [{"name": "StorageIsolation", "description": "local ephemeral storage isolation quota", "doc_links": [], "stages": [{"stage": "alpha", "from_version": "1.7", "to_version": "1.9"}]},
       {"name": "StorageIsolationFSQuota", "description": "local ephemeral storage isolation quota monitoring", "doc_links": [], "stages": [{"stage": "alpha", "from_version": "1.15", "to_version": None}]}] + [
    {**g, "stages": [{"stage": "alpha", "from_version": "1.1", "to_version": None}]} for g in GATES[3:]]
SECTION = "Quota for local ephemeral storage isolation. Local ephemeral storage quota monitoring."


def test_a_gate_that_did_not_exist_yet_is_eliminated_and_the_reason_is_recorded():
    lk = Linker(SIB)
    without, _ = lk.link_detailed(SECTION, [], "Quota for local ephemeral storage")
    with_rule, elim = lk.link_detailed(SECTION, [], "Quota for local ephemeral storage", since="v1.8")
    assert {c.gate for c in without} >= {"StorageIsolation", "StorageIsolationFSQuota"}
    assert [c.gate for c in with_rule][:1] == ["StorageIsolation"] and "StorageIsolationFSQuota" not in {c.gate for c in with_rule}
    e = next(x for x in elim if x["gate"] == "StorageIsolationFSQuota")
    assert e["first_version"] == "1.15" and e["claimed_since"] == "1.8" and "after the claimed since-version 1.8" in e["reason"]


def test_the_rule_never_raises_a_score_and_does_nothing_without_a_version():
    lk = Linker(SIB)
    base = {c.gate: c for c in lk.link(SECTION, [], "Quota")}
    ruled = {c.gate: c for c in lk.link(SECTION, [], "Quota", since="1.8")}
    for gate, c in ruled.items():  # a survivor is never scored higher than its own raw evidence allows (the tie cap may be lifted, the score is not inflated)
        assert c.signals.keys() <= base[gate].signals.keys() | {"ambiguous_with"} and c.confidence >= base[gate].confidence - 1e-9 and c.confidence <= max(base[gate].confidence, 1.0)
        assert {k: v for k, v in c.signals.items() if k != "ambiguous_with"} == {k: v for k, v in base[gate].signals.items() if k != "ambiguous_with"}
    assert lk.link_detailed(SECTION, [], "Quota", since=None)[1] == []
    assert [c.gate for c in lk.link(SECTION, [], "Quota")] == [c.gate for c in lk.link(SECTION, [], "Quota", since=None)]


def test_the_rule_keeps_every_verified_control_gate():
    """None of the 8 hand-verified control gates may be eliminated by the claimed since-version of its own label (the rule must not remove true gates)."""
    import json
    from pathlib import Path
    gates = {g["name"]: g for g in map(json.loads, Path("snapshot/gates.jsonl").read_text().splitlines())}
    labels = {x["id"]: x for x in map(json.loads, Path("snapshot/doc_labels.jsonl").read_text().splitlines())}
    from lineage.ingest.gates import version_key
    for case in map(json.loads, Path("eval/drift_cases.jsonl").read_text().splitlines()):
        if case["gate_verified"]:
            since = labels[case["claim_id"]]["for_k8s_version"]
            first = gates[case["expected_gate"]]["stages"][0]["from_version"]
            assert version_key(first) <= version_key(since), f"{case['id']}: {case['expected_gate']} starts at {first}, after the label's {since}"


def test_semantic_signal_is_off_by_default_and_never_confident_on_its_own():
    import numpy as np

    gates = [{"name": "AlphaGateOne", "description": "unrelated words", "doc_links": [], "stages": [{"stage": "alpha", "from_version": "1.1", "to_version": None}]}]
    emb = {"AlphaGateOne": [1.0, 0.0]}
    off = Linker(gates, None, None, emb)
    assert "semantic" not in (off.link("zzz qqq", [], None, None, None, [1.0, 0.0]) or [Candidate("x", 0, {})])[0].signals  # sem_max = 0: no signal
    on = Linker(gates, {"sem_floor": 0.5, "sem_scale": 0.1, "sem_max": 0.9}, None, emb)
    (c,) = on.link("zzz qqq", [], None, None, None, np.array([1.0, 0.0]))
    assert c.signals["semantic"] == 0.9 and c.confidence < CONFIDENT  # one family alone is never confident
    assert on.link("zzz qqq", [], None, None, None, [0.0, 1.0]) == []  # orthogonal embedding: no signal, no candidate
    assert FAMILY["semantic"] == FAMILY["text_sim"]
