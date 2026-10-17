import importlib.util
import json
import types
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("verify", Path(__file__).resolve().parent.parent / "scripts" / "verify.py")
verify = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify)


def jl(p: Path, rows):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(verify, "ROOT", tmp_path)
    jl(tmp_path / "snapshot/doc_labels.jsonl", [{"id": "a#x:1", "heading": "H", "page_title": "P", "shortcode": "{{< feature-state >}}", "source_url": "u", "section_text": "text"},
                                                {"id": "b#y:1", "heading": "H2", "page_title": "P2", "shortcode": "s", "source_url": "u2", "section_text": "t2"}])
    jl(tmp_path / "snapshot/gates.jsonl", [{"name": "G", "description": "d", "stages": [{"stage": "alpha", "from_version": "1.1", "to_version": None}], "removed": False, "source_url": "gu"}])
    jl(tmp_path / "eval/link_audit.jsonl", [{"claim_id": "a#x:1", "linked_gate": "G", "confidence": 0.9, "verdict": "correct", "note": ""}, {"claim_id": "b#y:1", "linked_gate": "G", "confidence": 0.85, "verdict": "wrong", "note": "n"}])
    return tmp_path


def answers_from(monkeypatch, seq):
    it = iter(seq)
    monkeypatch.setattr(verify, "ask", lambda *a, **k: next(it))


def test_links_records_verdicts_saves_after_each_answer_and_resumes(root, monkeypatch, capsys):
    answers_from(monkeypatch, ["y", "q"])  # answer the first, quit on the second
    verify.links(types.SimpleNamespace(sample=0, seed=1))
    rows = verify.read_jsonl(root / "eval/link_audit.jsonl")
    assert rows[0]["human_verdict"] == "correct" and rows[0]["human_labeled_by"] == verify.WHO and "human_verdict" not in rows[1]
    answers_from(monkeypatch, ["n"])  # a second run only asks what is left
    verify.links(types.SimpleNamespace(sample=0, seed=1))
    rows = verify.read_jsonl(root / "eval/link_audit.jsonl")
    assert rows[1]["human_verdict"] == "wrong"
    verify.report(None)
    out = capsys.readouterr().out
    assert "checked: 2; correct 1" in out and "You agreed with the AI on 2/2" in out


def test_sample_always_includes_the_ai_doubts(root, monkeypatch):
    rows = verify.read_jsonl(root / "eval/link_audit.jsonl") + [{"claim_id": f"c{i}#z:1", "linked_gate": "G", "confidence": 0.9, "verdict": "correct", "note": ""} for i in range(10)]
    jl(root / "eval/link_audit.jsonl", rows)
    seen = []
    monkeypatch.setattr(verify, "ask", lambda *a, **k: seen.append(1) or "s")
    verify.links(types.SimpleNamespace(sample=3, seed=1))
    assert len(seen) == 3  # 1 "wrong" + 2 random


def test_gold_ticks_and_marks_wrong(root, monkeypatch):
    (root / "eval").mkdir(exist_ok=True)
    (root / "eval/gold_review.md").write_text("# Gold\n\n- [ ] **own1** (ownership): Who owns X?\n  - expected: a\n  - source: https://e/x\n- [ ] **own2** (ownership): Who owns Y?\n  - expected: b\n  - source: https://e/y\n")
    answers_from(monkeypatch, ["y", "n"])
    verify.gold(None)
    text = (root / "eval/gold_review.md").read_text()
    assert "- [x] **own1**" in text and "**WRONG (owner check)** **own2**" in text and text.count("- [x]") == 1


def test_wilson_interval_is_sane():
    lo, hi = verify.wilson(69, 73)
    assert 0.86 < lo < 0.90 < hi < 0.98 and verify.wilson(0, 0) == (0.0, 0.0)
