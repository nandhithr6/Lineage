import pytest

from lineage.agent import resolve as R
from lineage.agent.tools import ToolBox
from lineage.graph.loader import load_all
from tests.test_loader import graph  # noqa: F401


@pytest.fixture
def g(graph):  # noqa: F811
    load_all(graph)
    R.clear_cache()
    return graph


def test_each_kind_resolves_real_names_and_normalises_only_the_same_name(g):
    assert R.resolve_gate(g, "localstoragecapacityisolation").canonical == "LocalStorageCapacityIsolation"  # letter case only
    assert R.resolve_sig(g, "storage").canonical == "sig-storage" and R.resolve_sig(g, "sig-node").ok
    assert R.resolve_person(g, "@Alculquicondor").canonical == "alculquicondor"
    assert R.resolve_kep(g, "sig-storage/361-local-ephemeral-storage-isolation").ok and R.resolve_kep(g, "KEP-361").ok and R.resolve_kep(g, "local ephemeral storage").ok
    assert R.resolve_release(g, "1.30").ok and R.resolve_release(g, "v1.37").ok and R.resolve_release(g, "latest").ok and R.resolve_release(g, None).ok
    assert R.resolve_path(g, "pkg/volume").canonical == "pkg/volume" and R.resolve_path(g, "pkg/volume/OWNERS").canonical == "pkg/volume"
    assert R.resolve_path(g, "content/en/docs/concepts/policy/resource-quotas.md", "website").ok
    assert R.resolve_page(g, "resource-quotas").ok and R.resolve_finding(g, "resource-quotas").ok and R.resolve_kep_or_gate(g, "LocalStorageCapacityIsolation").label == "FeatureGate"
    assert R.resolve_entity(g, "alculquicondor").label == "Person" and R.resolve_entity(g, "sig-storage").label == "SIG" and R.resolve_entity(g, "NodeSwap").label == "FeatureGate"


def test_unknown_names_are_unresolved_with_text_suggestions_and_no_substitution(g):
    r = R.resolve_gate(g, "LocalStorageCapacityIsolatio")  # a typo
    assert not r.ok and r.canonical is None and "LocalStorageCapacityIsolation" in r.suggestions  # offered as text only
    assert not R.resolve_gate(g, "NoSuchGate").ok and not R.resolve_sig(g, "sig-nope").ok and not R.resolve_kep(g, "KEP-99999999").ok
    assert not R.resolve_person(g, "zorblax-the-invented").ok and not R.resolve_page(g, "no-such-page-anywhere").ok
    future = R.resolve_release(g, "1.45")
    assert not future.ok and "future cannot be known" in future.note and not R.resolve_release(g, "banana").ok
    p = R.resolve_path(g, "pkg/volume/doesnotexist")
    assert not p.ok and "pkg/volume" in p.suggestions  # the nearest loaded ancestor is suggested, not used
    assert not R.resolve_path(g, "pkg/volume", "nope").ok


def test_names_are_passed_as_parameters_so_injection_text_is_just_an_unknown_name(g):
    before = g.ro_query("MATCH (n) RETURN count(n)").result_set[0][0]
    for evil in ('x"}) DETACH DELETE (n) //', "' OR 1=1 //", "a\nMATCH (n) DETACH DELETE n"):
        assert not R.resolve_gate(g, evil).ok and not R.resolve_path(g, evil).ok and not R.resolve_kep(g, evil).ok
    assert g.ro_query("MATCH (n) RETURN count(n)").result_set[0][0] == before


def test_unresolved_argument_means_the_tool_does_not_run(g, monkeypatch):
    tb = ToolBox(g)
    ran = []
    monkeypatch.setattr(ToolBox, "gate_timeline", lambda self, gate: ran.append(gate) or {})
    b = tb.call("gate_timeline", {"gate": "NoSuchGate"})
    assert ran == [] and b["outcome"] == "unknown_entity" and b["status"] == "insufficient_evidence" and b["evidence"] == []
    assert b["unresolved"][0]["argument"] == "gate" and b["unresolved"][0]["given"] == "NoSuchGate" and "No lookup was run" in b["params"]["note"]


def test_every_entity_argument_of_every_tool_is_checked(g, monkeypatch):
    tb = ToolBox(g)
    for tool, spec in ToolBox.ENTITY_ARGS.items():
        monkeypatch.setattr(ToolBox, tool, lambda self, **kw: (_ for _ in ()).throw(AssertionError("tool must not run")))
        for arg, kind in spec:
            bad = {"release": "1.99", "path": "no/such/dir", "sig": "sig-nope"}.get(kind, "ZzNoSuchEntityZz")
            good_other = {"what_changed": {"entity": "LocalStorageCapacityIsolation", "from_release": "1.30", "to_release": "1.31"}, "owners_of": {"path": "pkg/volume"}}.get(tool, {})
            b = tb.call(tool, {**good_other, arg: bad})
            assert b["outcome"] == "unknown_entity" and any(u["argument"] == arg for u in b["unresolved"]), (tool, arg)


def test_close_match_suggestions_are_text_and_bounded():
    pool = ["LocalStorageCapacityIsolation", "LocalStorageCapacityIsolationFSQuotaMonitoring", "PodSecurity", "NodeSwap"]
    got = R.close_matches("LocalStorageCapacity", pool)
    assert got and all(x in pool for x in got) and len(got) <= 3 and R.close_matches("zzzz", pool) == []


def test_preflight_catches_unknown_releases_and_typos_in_the_question(g):
    assert [u["kind"] for u in R.preflight(g, "Who will approve pkg/controller/job in release 1.45?")] == ["release"]
    typo = R.preflight(g, "What is the stage of the LocalStorageCapacityIsolatio gate?")
    assert typo and typo[0]["kind"] == "gate" and typo[0]["did_you_mean"][0] == "LocalStorageCapacityIsolation"
    assert R.preflight(g, "Who owned pkg/controller/job in 1.30 and who owns it now?") == []
    assert R.preflight(g, "What happened to LocalStorageCapacityIsolation in v1.37 or 1.38?") == []  # real gate; 1.38 is the latest commit
    assert R.preflight(g, "Tell me about PersistentVolumeClaim and StatefulSet in Kubernetes") == []  # ordinary CamelCase words are not typos of a gate
    assert R.preflight(g, "What is NoSuchGateThatIsFarFromAnyRealGate?") == []  # far from every real name: left to the tools to refuse
    assert "1.45" in R.unknown_entity_answer(R.preflight(g, "release 1.45?")) and "future cannot be known" in R.unknown_entity_answer(R.preflight(g, "release 1.45?"))


def test_the_model_is_not_called_when_the_question_names_something_unknown(g):
    from lineage.agent.llm import FakeLLM, LLMResponse
    from lineage.agent.loop import run_agent

    llm = FakeLLM([LLMResponse("It is stable [e1].")])
    r = run_agent(llm, ToolBox(g), "What is the stage of the LocalStorageCapacityIsolatio gate?")
    assert llm.seen == [] and r.outcome == "unknown_entity" and r.status == "insufficient_evidence" and r.evidence == []  # no model call, no tool call
    assert "did you mean: LocalStorageCapacityIsolation" in r.answer and r.tool_trace[0]["tool"] == "resolve_question" and r.tool_trace[0]["unresolved"][0]["given"] == "LocalStorageCapacityIsolatio"
    r2 = run_agent(FakeLLM([LLMResponse("They will be X [e1].")]), ToolBox(g), "Who will approve pkg/controller/job in release 1.45?")
    assert r2.outcome == "unknown_entity" and "release 1.45" in r2.answer and r2.citations == []


def test_a_kep_written_by_number_reaches_the_tool_as_its_real_id(g):
    from lineage.agent.tools import ToolBox
    tools = ToolBox(g)
    real_id = "sig-storage/361-local-ephemeral-storage-isolation"
    for written in ("KEP-361", "kep 361", "361", "KEP #361"):
        r = R.resolve_kep_or_gate(g, written)
        assert r.ok and r.label == "KEP" and r.canonical == real_id, written  # a number becomes the id
        done = tools.call("decision_lineage", {"kep_or_gate": written})
        assert done["outcome"] == "answered_with_path" and done["validated_args"]["kep_or_gate"] == real_id and done["evidence"][0]["from"] == f"KEP:{real_id}", written
    topic = R.resolve_kep_or_gate(g, "local ephemeral storage")  # a topic stays as written, so the tool can return every matching KEP
    assert topic.ok and topic.canonical == "local ephemeral storage"
