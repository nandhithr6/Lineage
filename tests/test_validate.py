import json
import logging

import pytest

from lineage.agent.llm import FakeLLM, LLMResponse
from lineage.agent.loop import run_agent
from lineage.agent.tools import ToolBox
from lineage.agent.validate import AnswerValidator, template_answer
from tests.test_agent import tc, toolbox  # noqa: F401
from tests.test_loader import graph  # noqa: F401

EV = [
    {"id": "e1", "kind": "edge", "type": "STAGE_IN", "from": "FeatureGate:LocalStorageCapacityIsolation", "to": "Release:1.25", "props": {"stage": "stable"},
     "source_url": "https://github.com/kubernetes/website/blob/abc/content/en/docs/x.md"},
    {"id": "e2", "kind": "edge", "type": "APPROVER_OF", "from": "Person:alculquicondor", "to": "CodePath:kubernetes:pkg/controller/job", "props": {"via_team": None},
     "source_url": "https://github.com/kubernetes/kubernetes/blob/abc/pkg/controller/job/OWNERS"},
    {"id": "e3", "kind": "node", "type": "KEP", "from": "KEP:sig-storage/361-local-ephemeral-storage-isolation", "to": None, "props": {"title": "Local Ephemeral Storage"}, "source_url": None},
]
V = AnswerValidator(known_people={"alculquicondor", "janetkuo", "pohly", "dims"}, vocabulary={"also", "listed", "currently", "approvers", "nobody", "stable", "release"})


def bad(answer, question=""):
    return {(r["kind"], r["term"]) for r in V.validate(answer, EV, question).rejected}


def test_a_faithful_answer_is_accepted():
    ok = ("The gate LocalStorageCapacityIsolation reached stable at v1.25 [e1]. Its approvers for `pkg/controller/job` are alculquicondor [e2]. "
          "The design decision is KEP-361 [e3]. See https://github.com/kubernetes/website/blob/abc/content/en/docs/x.md.")
    v = V.validate(ok, EV, "q")
    assert v.accepted and v.rejected == [] and v.checked["gate"] >= 1 and v.checked["version"] == 1 and v.checked["path"] == 1 and v.checked["kep"] == 1 and v.checked["url"] == 1


def test_an_invented_gate_name_is_rejected():
    assert ("gate", "ZorblaxTelepathyGate") in bad("The ZorblaxTelepathyGate reached stable [e1].")
    assert ("gate", "LocalStorageCapacityIsolationFSQuotaMonitoring") in bad("It also relates to LocalStorageCapacityIsolationFSQuotaMonitoring [e1].")  # real, but not in the evidence


def test_invented_and_leaked_handles_are_rejected_in_every_form():
    assert ("handle", "zorblax") in bad("Ask @zorblax about it [e2].")
    assert ("handle", "zorblax") in bad("The approvers were alculquicondor and zorblax [e2].")
    assert ("handle", "zorblax") in bad("Approvers: alculquicondor, **zorblax** [e2].")
    assert ("handle", "janetkuo") in bad("The approvers include alculquicondor and janetkuo [e2].")  # a real person the evidence does not show
    assert bad("The approvers were alculquicondor [e2].") == set()
    assert bad("The approvers are also listed here, currently nobody else [e2].") == set()  # ordinary words after a role word are not handles
    assert bad("Ask pohly.", "Should I ask pohly?") == set()  # named by the user in the question: allowed


def test_invented_versions_keps_paths_and_urls_are_rejected():
    assert ("version", "1.99") in bad("It reached stable at 1.99 [e1].")
    assert ("kep", "999") in bad("This is KEP-999 [e3].")
    assert ("path", "pkg/volume/csi") in bad("Owners of pkg/volume/csi are alculquicondor [e2].")
    assert ("url", "https://github.com/kubernetes/kubernetes/pull/42") in bad("See https://github.com/kubernetes/kubernetes/pull/42 [e2].")
    assert bad("Stable since v1.25 [e1].") == set() and bad("Stable since 1.25.") == set()  # a leading v does not matter
    assert bad("Asked about 1.45.", "What about 1.45?") == set()


def test_the_template_is_assembled_from_the_evidence_rows_only():
    text, shown = template_answer(EV, [{"kind": "gate", "term": "ZorblaxTelepathyGate"}])
    assert "ZorblaxTelepathyGate" in text.split("\n")[0] and [e["id"] for e in shown] == ["e1", "e2", "e3"]
    assert "[e1] STAGE_IN: FeatureGate:LocalStorageCapacityIsolation → Release:1.25" in text and "stage: stable" in text and "github.com/kubernetes/website" in text
    assert V.validate(text.split("\n", 1)[1], EV, "").accepted  # the fallback itself never trips the validator


def test_agent_replaces_an_invented_answer_with_the_template_and_logs_it(toolbox, caplog, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setenv("LINEAGE_VALIDATOR_LOG", str(tmp_path / "rejections.jsonl"))
    llm = FakeLLM([tc("gate_timeline", gate="LocalStorageCapacityIsolation"), LLMResponse("The gate reached stable at 1.25 [e3], and the ZorblaxTelepathyGate depends on it [e1].")])
    with caplog.at_level(logging.WARNING, logger="lineage.validator"):
        r = run_agent(llm, toolbox, "What happened to LocalStorageCapacityIsolation?")
    assert r.status == "ok" and r.outcome == "answered_with_path" and r.validation["accepted"] is False
    assert r.validation["rejected"] == [{"kind": "gate", "term": "ZorblaxTelepathyGate"}] and "ZorblaxTelepathyGate" in r.validation["draft"]
    assert "I could not verify the wording" in r.answer and "STAGE_IN" in r.answer and r.citations  # evidence rows, each citable
    assert "depends on it" not in r.answer
    assert "answer rejected by the validator" in caplog.text
    rec = json.loads((tmp_path / "rejections.jsonl").read_text().splitlines()[0])
    assert rec["rejected"][0]["term"] == "ZorblaxTelepathyGate" and "ZorblaxTelepathyGate" in rec["draft"]


def test_agent_keeps_a_faithful_answer_untouched(toolbox):  # noqa: F811
    llm = FakeLLM([tc("gate_timeline", gate="LocalStorageCapacityIsolation"), LLMResponse("It reached stable at 1.25 [e3].")])
    r = run_agent(llm, toolbox, "What happened to LocalStorageCapacityIsolation?")
    assert r.answer == "It reached stable at 1.25 [e3]." and r.validation == {"accepted": True, "rejected": []}


def test_every_saved_demo_answer_passes_the_validator(graph):  # noqa: F811
    from lineage.agent.answer_cache import load
    from lineage.graph.loader import load_all

    rows = load()
    if not rows:
        pytest.skip("no saved demo answers")
    load_all(graph)
    v = AnswerValidator.from_graph(graph)
    for row in rows.values():
        if row.get("mode") == "retrieval":
            continue
        r = row["response"]
        res = v.validate(r["answer"], r["evidence"], row["question"])
        assert res.accepted, (row["question"], res.rejected)
