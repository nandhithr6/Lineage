import json

import pytest

from lineage.agent.llm import FakeLLM, LLMResponse, ToolCall
from lineage.agent.loop import renumber, run_agent
from lineage.agent.tools import ToolBox, release_num
from tests.test_loader import graph  # noqa: F401  (fixture: throwaway graph, skipped if FalkorDB is down)
from lineage.graph.loader import load_all


def tc(name, **args):
    return LLMResponse(tool_calls=[ToolCall("c1", name, args)])


def test_release_num():
    assert release_num("1.30") == 130 and release_num("v1.37") == 137 and release_num(None) == 138 and release_num("latest") == 138


def test_renumber_keeps_ids_unique_across_calls():
    a = renumber({"evidence": [{"id": "e1"}, {"id": "e2"}]}, 0)
    b = renumber({"evidence": [{"id": "e1"}]}, 2)
    assert [e["id"] for e in a["evidence"]] == ["e1", "e2"] and b["evidence"][0]["id"] == "e3"


@pytest.fixture
def toolbox(graph):  # noqa: F811
    load_all(graph)
    return ToolBox(graph)


def test_gate_timeline_tool(toolbox):
    b = toolbox.gate_timeline("LocalStorageCapacityIsolation")
    assert b["status"] == "ok" and [e["props"]["stage"] for e in b["evidence"] if e["type"] == "STAGE_IN"] == ["alpha", "beta", "stable"]
    assert all(e["source_url"] for e in b["evidence"])
    assert toolbox.gate_timeline("NoSuchGate")["status"] == "insufficient_evidence"


def test_owners_of_tool_time_travel(toolbox):
    now = toolbox.owners_of("pkg/controller/job")
    then = toolbox.owners_of("pkg/controller/job", as_of="1.30")
    assert now["status"] == "ok" and then["status"] == "ok"
    approvers = lambda b: {e["from"] for e in b["evidence"] if e["type"] == "APPROVER_OF" and not e["props"]["via_team"]}
    assert "Person:alculquicondor" in approvers(then) and "Person:mimowo" in approvers(now)


def test_doc_claims_and_decision_lineage_tools(toolbox):
    c = toolbox.doc_claims("resource-quotas")
    assert c["status"] == "ok" and any(e["props"]["linked_gate"] == "LocalStorageCapacityIsolation" and e["props"]["link_method"] == "linker" for e in c["evidence"])
    d = toolbox.decision_lineage("LocalStorageCapacityIsolation")
    assert d["status"] == "ok" and any(e["type"] == "OWNED_BY" and e["to"] == "SIG:sig-storage" for e in d["evidence"])


def test_agent_answers_with_citations_from_tool_evidence(toolbox):
    llm = FakeLLM([tc("gate_timeline", gate="LocalStorageCapacityIsolation"),
                   LLMResponse("The gate reached stable at 1.25 [e3] and was then removed [e1].")])
    r = run_agent(llm, toolbox, "What happened to LocalStorageCapacityIsolation?")
    assert r.status == "ok" and {c["id"] for c in r.citations} == {"e1", "e3"} and r.iterations == 2
    assert len(r.tool_trace) == 1 and {k: r.tool_trace[0][k] for k in ("tool", "params", "status", "evidence", "outcome")} == {
        "tool": "gate_timeline", "params": {"gate": "LocalStorageCapacityIsolation"}, "status": "ok", "evidence": 4, "outcome": "answered_with_path"}
    assert isinstance(r.tool_trace[0]["duration_ms"], int)
    tool_msg = llm.seen[1][-1]  # the second model call saw the tool result, with unique evidence ids and sources
    assert tool_msg["role"] == "tool" and '"id": "e3"' in tool_msg["content"] and "source_url" in tool_msg["content"]
    assert json.loads(tool_msg["content"])["status"] == "ok"


def test_agent_refuses_when_no_evidence_even_if_the_model_tries_to_answer(toolbox):
    llm = FakeLLM([tc("gate_timeline", gate="NoSuchGate"), LLMResponse("It is stable since 1.99 [e1].")])
    r = run_agent(llm, toolbox, "Tell me about NoSuchGate")
    assert r.status == "insufficient_evidence" and r.answer.startswith("Unknown entity") and r.citations == [] and "NoSuchGate" in r.answer


def test_agent_refuses_without_calling_any_tool(toolbox):
    r = run_agent(FakeLLM([LLMResponse("Kubernetes 1.99 is great.")]), toolbox, "Who won the world cup?")
    assert r.status == "insufficient_evidence" and "no tools were called" in r.answer


def test_unknown_citations_are_removed_and_reported(toolbox):
    llm = FakeLLM([tc("gate_timeline", gate="LocalStorageCapacityIsolation"), LLMResponse("Stable at 1.25 [e3] and also 1.99 [e77].")])
    r = run_agent(llm, toolbox, "q")
    assert r.status == "ok" and "[e77]" not in r.answer and r.unsupported_citations == ["e77"] and "[e3]" in r.answer


def test_uncited_answer_is_asked_once_to_cite(toolbox):
    llm = FakeLLM([tc("gate_timeline", gate="LocalStorageCapacityIsolation"), LLMResponse("It went stable."), LLMResponse("It went stable [e3].")])
    r = run_agent(llm, toolbox, "q")
    assert r.status == "ok" and r.iterations == 3
    assert "cite the evidence id" in llm.seen[2][-1]["content"]


def test_iteration_cap(toolbox):
    llm = FakeLLM([tc("gate_timeline", gate="LocalStorageCapacityIsolation")] * 10)
    r = run_agent(llm, toolbox, "q", max_iterations=3)
    assert r.status == "incomplete" and r.iterations == 3 and len(r.tool_trace) == 3


def test_bad_tool_arguments_do_not_crash(toolbox):
    llm = FakeLLM([tc("gate_timeline", wrong_arg=1), tc("no_such_tool"), LLMResponse("x")])
    r = run_agent(llm, toolbox, "q")
    assert r.status == "insufficient_evidence" and [t["status"] for t in r.tool_trace] == ["insufficient_evidence"] * 2


def test_sig_leaders_tool(toolbox):
    b = toolbox.sig_leaders("sig-storage")
    roles = {(e["from"], e["props"]["role"]) for e in b["evidence"] if e["type"] != "SIG"}
    assert b["status"] == "ok" and ("Person:xing-yang", "chair") in roles and ("Person:msau42", "tech lead") in roles
    assert toolbox.sig_leaders("Storage")["status"] == "ok"  # display name works too
    assert toolbox.sig_leaders("sig-nope")["status"] == "insufficient_evidence"
    assert "email" not in json.dumps(b).lower() and "company" not in json.dumps(b).lower()


def test_what_changed_tool(toolbox):
    p = toolbox.what_changed("pkg/controller/job", "1.30", "1.37")
    kinds = {(e["type"], e["from"]) for e in p["evidence"]}
    assert p["status"] == "ok" and ("OWNER_REMOVED", "Person:alculquicondor") in kinds and ("OWNER_ADDED", "Person:mimowo") in kinds
    assert all(e["source_url"] for e in p["evidence"])
    g = toolbox.what_changed("LocalStorageCapacityIsolation", "1.9", "1.25")
    assert g["evidence"][0]["props"]["stage_then"] == ["alpha"] and g["evidence"][0]["props"]["stage_now"] == ["stable"]
    assert toolbox.what_changed("pkg/controller/job", "1.10", "1.37")["status"] == "insufficient_evidence"  # history not sampled that far back
    quiet = toolbox.what_changed("pkg/volume", "1.36", "1.37")
    assert quiet["status"] == "ok" and quiet["evidence"][0]["type"] == "NoChange"  # "nothing changed" is a citable answer


def test_grouped_citations_are_understood_and_unknown_ids_removed_inside_groups():
    from lineage.agent.loop import cited_ids, drop_unknown
    assert cited_ids("Stable [e3] then removed [e7, e8]; also [e9,e10] and [e3].") == ["e3", "e7", "e8", "e9", "e10"]
    assert cited_ids("an array like [1, 2] or [e] or [eX] is not a citation") == []
    assert drop_unknown("a [e1, e99] b [e98] c [e2][e3]", {"e1", "e2", "e3"}) == "a [e1] b  c [e2][e3]"


def test_agent_accepts_a_grouped_citation(toolbox):
    llm = FakeLLM([tc("gate_timeline", gate="LocalStorageCapacityIsolation"), LLMResponse("Alpha then beta [e2, e3], stable later [e4, e77].")])
    r = run_agent(llm, toolbox, "q")
    assert r.status == "ok" and {c["id"] for c in r.citations} == {"e2", "e3", "e4"} and r.unsupported_citations == ["e77"] and "[e2, e3]" in r.answer and "e77" not in r.answer


def test_clean_path_accepts_the_ways_models_name_a_folder():
    from lineage.agent.tools import clean_path
    assert [clean_path(x) for x in ("OWNERS", "/", ".", "", "pkg/volume/OWNERS", "pkg/volume/", "/pkg/volume")] == ["", "", "", "", "pkg/volume", "pkg/volume", "pkg/volume"]


def test_owners_of_root_by_any_name(toolbox):
    for name in ("OWNERS", "/", ""):
        b = toolbox.owners_of(name, repo="website")
        assert b["status"] == "ok" and any(e["type"] == "EMERITUS_APPROVER_OF" and e["from"] == "Person:stewart-yu" for e in b["evidence"])


def test_doc_claims_is_case_insensitive_and_shows_candidates_honestly(toolbox):
    for q in ("namespaces", "Namespaces", "NAMESPACES"):
        assert toolbox.doc_claims(q)["status"] == "ok"
    claim = next(e for e in toolbox.doc_claims("Pod OS")["evidence"] if e["props"]["section"] == "Pod OS")
    assert claim["props"]["link_note"].startswith("NOT confident") and claim["props"]["candidate_gates"][0]["confidence"] < 0.8
    seed = next(e for e in toolbox.doc_claims("resource-quotas")["evidence"] if e["props"]["section"] == "Quota for local ephemeral storage")
    assert seed["props"]["link_method"] == "linker" and seed["props"]["link_note"] == "confident"


def test_owners_of_says_why_when_history_does_not_exist(toolbox):
    for repo, as_of in (("website", "1.30"), ("kubernetes", "1.20")):
        b = toolbox.owners_of("content/en" if repo == "website" else "pkg/volume", repo=repo, as_of=as_of)
        assert b["status"] == "insufficient_evidence" and "history" in b["params"]["note"] and b["evidence"] == []
    assert "latest commit" in toolbox.owners_of("content/en", repo="website", as_of="1.30")["params"]["note"]
    assert toolbox.owners_of("content/en", repo="website")["status"] == "ok"  # latest still works


def test_fullwidth_citation_brackets_are_normalised():
    from lineage.agent.loop import cited_ids, normalize_citations
    assert normalize_citations("Xing Yang is a chair 【e6】. Also 【e7, e8】 and 【e9†source】 and [e10].") == "Xing Yang is a chair [e6]. Also [e7, e8] and [e9] and [e10]."
    assert cited_ids(normalize_citations("a 【e1】 b 【e2, e3】")) == ["e1", "e2", "e3"]
    assert normalize_citations("plain 【not a citation】 and [1]") == "plain 【not a citation】 and [1]"


def test_agent_accepts_fullwidth_citations(toolbox):
    llm = FakeLLM([tc("gate_timeline", gate="LocalStorageCapacityIsolation"), LLMResponse("Stable at 1.25【e3】.")])
    r = run_agent(llm, toolbox, "q")
    assert r.status == "ok" and "[e3]" in r.answer and [c["id"] for c in r.citations] == ["e3"]


def test_future_or_unsampled_releases_are_refused_not_answered_with_todays_owners(toolbox):
    """Regression for held-out h_un2: 'approvers in release 1.45' used to return the LATEST owners and the model listed them."""
    for rel in ("1.45", "2.0", "1.29", "1.10"):
        b = toolbox.owners_of("pkg/controller/job", as_of=rel)
        assert b["status"] == "insufficient_evidence" and b["evidence"] == [] and "Do not substitute" in b["params"]["note"], rel
    assert "future cannot be known" in toolbox.owners_of("pkg/controller/job", as_of="1.45")["params"]["note"]
    assert toolbox.owners_of("pkg/controller/job", as_of="1.38")["status"] == "ok"  # 1.38 = latest (HEAD), sampled
    assert toolbox.owners_of("pkg/controller/job", as_of="1.30")["status"] == "ok"
    assert toolbox.what_changed("pkg/controller/job", "1.30", "1.45")["status"] == "insufficient_evidence"
    assert toolbox.what_changed("LocalStorageCapacityIsolation", "1.30", "1.45")["status"] == "insufficient_evidence"
    assert toolbox.what_changed("LocalStorageCapacityIsolation", "1.20", "1.30")["status"] == "ok"


def test_agent_refuses_a_future_release_even_if_the_model_lists_current_owners(toolbox):
    llm = FakeLLM([tc("owners_of", path="pkg/controller/job", as_of="1.45"), LLMResponse("The approvers will be alculquicondor [e1].")])
    r = run_agent(llm, toolbox, "Who will be the approvers of pkg/controller/job in release 1.45?")
    assert r.status == "insufficient_evidence" and r.citations == [] and "alculquicondor" not in r.answer


def test_trace_records_validated_arguments_duration_rows_and_outcome(toolbox):
    llm = FakeLLM([tc("gate_timeline", gate="localstoragecapacityisolation"), LLMResponse("Stable at 1.25 [e3].")])
    r = run_agent(llm, toolbox, "q")
    (step,) = r.tool_trace
    assert step["params"] == {"gate": "LocalStorageCapacityIsolation"} and step["given"] == {"gate": "localstoragecapacityisolation"}  # canonical name used, original kept
    assert step["evidence"] == 4 and step["outcome"] == "answered_with_path" and isinstance(step["duration_ms"], int) and step["duration_ms"] >= 0
    llm2 = FakeLLM([tc("gate_timeline", gate="NoSuchGate"), LLMResponse("x")])
    (bad,) = run_agent(llm2, toolbox, "q").tool_trace
    assert bad["params"] == {"gate": "NoSuchGate"} and bad["given"] is None and bad["evidence"] == 0 and bad["outcome"] == "unknown_entity" and bad["unresolved"][0]["argument"] == "gate"


def test_removed_gate_never_offers_a_removal_release(toolbox):
    node = toolbox.call("gate_timeline", {"gate": "ReadWriteOncePod"})["evidence"][0]
    assert node["props"]["removed"] is True and "not recorded" in node["props"]["removal_release"]


def test_looked_up_label_says_it_cannot_go_stale(toolbox):
    rows = toolbox.call("doc_claims", {"page_or_section": "Sidecar Containers"})["evidence"]
    assert rows and all("can NEVER be out of date" in r["props"]["label_note"] for r in rows if r["props"]["kind"] == "gate_lookup")
    hard = toolbox.call("doc_claims", {"page_or_section": "resource-quotas"})["evidence"]
    assert any("CAN go out of date" in r["props"].get("label_note", "") for r in hard)


def test_team_members_resolves_an_owners_alias_to_people_now_and_in_a_past_release(toolbox):
    now = toolbox.call("team_members", {"team": "sig-node-approvers"})  # written the way people write it, without the repo prefix
    assert now["outcome"] == "answered_with_path" and now["validated_args"]["team"] == "kubernetes:sig-node-approvers"
    handles = {e["from"] for e in now["evidence"]}
    assert "Person:mrunalp" in handles and "Person:sergeykanzhelev" in handles and all(e["source_url"] for e in now["evidence"])
    then = {e["from"] for e in toolbox.call("team_members", {"team": "kubernetes:sig-node-approvers", "as_of": "1.30"})["evidence"]}
    assert "Person:mrunalp" in then and "Person:sergeykanzhelev" not in then  # sergeykanzhelev joined the alias at 1.32
    assert toolbox.call("team_members", {"team": "sig-nope-approvers"})["outcome"] == "unknown_entity"
    assert toolbox.call("team_members", {"team": "sig-node-approvers", "as_of": "1.45"})["outcome"] == "unknown_entity"  # a release that does not exist
