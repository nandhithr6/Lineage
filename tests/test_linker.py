from lineage.link.linker import CONFIDENT, Linker, camel_words, tokens

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
