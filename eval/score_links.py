"""Score the linker on claims whose gate is KNOWN: labels written as feature_gate_name="X".

We hide that shortcode line (and any other shortcodes) from the section text and ask the linker to recover X.
Pages are split in two halves by a stable hash: tune on half A, report on half B. This is a proxy for the
hard-coded labels, whose sections may be written differently, so treat it as an estimate, not the truth.

Run: uv run python -m eval.score_links
"""
from __future__ import annotations

import json
import zlib
from pathlib import Path

from lineage.ingest.keps import SNAPSHOT as KEPS
from lineage.link.linker import CONFIDENT, Linker, _SHORTCODE, read_jsonl

GATES = Path("snapshot/gates.jsonl")
LABELS = Path("snapshot/doc_labels.jsonl")


def half(page_path: str) -> str:
    return "A" if zlib.crc32(page_path.encode()) % 2 == 0 else "B"


def score(labels: list[dict], linker: Linker) -> dict:
    n = conf_n = conf_ok = top1 = in_cands = 0
    for lab in labels:
        cands = linker.link(_SHORTCODE.sub(" ", lab["section_text"]), lab["doc_links"], lab["heading"], lab["page_doc_links"], since=lab.get("for_k8s_version"))
        n += 1
        truth = lab["gate_name"]
        if cands and cands[0].gate == truth:
            top1 += 1
        if any(c.gate == truth for c in cands):
            in_cands += 1
        if cands and cands[0].confidence >= CONFIDENT:
            conf_n += 1
            conf_ok += cands[0].gate == truth
    return {
        "claims": n,
        "confident_coverage": round(conf_n / n, 3),
        "confident_precision": round(conf_ok / conf_n, 3) if conf_n else None,
        "top1_accuracy": round(top1 / n, 3),
        "truth_in_top3": round(in_cands / n, 3),
    }


def score_cases(linker: Linker) -> None:
    """Show what the linker does on the hand-picked cases (too few to be statistics, but each one matters)."""
    labels = {x["id"]: x for x in read_jsonl(LABELS)}
    for case in read_jsonl(Path("eval/drift_cases.jsonl")):
        lab = labels[case["claim_id"]]
        cands = linker.link(lab["section_text"], lab["doc_links"], lab["heading"], lab["page_doc_links"], since=lab.get("for_k8s_version"))
        top = [(c.gate, c.confidence) for c in cands[:2]]
        exp, bad = case["expected_gate"], case["forbidden_confident_gate"]
        if bad:
            verdict = "FAIL" if any(c.gate == bad and c.confidence >= CONFIDENT for c in cands) else "ok (not confidently linked to the forbidden gate)"
        elif exp:
            hit = next((c for c in cands if c.gate == exp), None)
            verdict = ("LINKED confidently" if hit and hit.confidence >= CONFIDENT else
                       "possible only" if hit else "MISSED") + (" (but top-1 is another gate)" if cands and cands[0].gate != exp else "")
        else:
            verdict = "no gate expected: " + ("confident link to " + cands[0].gate + " (inspect)" if cands and cands[0].confidence >= CONFIDENT else "no confident link")
        print(f"{case['id']:30s} expected={exp!s:32s} got={top} -> {verdict}")


def main() -> None:
    gates = read_jsonl(GATES)
    labels = [x for x in read_jsonl(LABELS) if x["kind"] == "gate_lookup" and x["gate_name"] in {g["name"] for g in gates}]
    keps = read_jsonl(KEPS)
    for name, params, k in [("no KEP signal", None, None), ("full", None, keps),
                            ("no name_mention", {"name_mention": 0.0, "name_bare": 0.0}, None),
                            ("no name + KEP", {"name_mention": 0.0, "name_bare": 0.0}, keps)]:
        linker = Linker(gates, params, k)
        for h in ("A", "B"):
            sub = [x for x in labels if half(x["page_path"]) == h]
            print(f"{name:16s} half {h}:", json.dumps(score(sub, linker)))
    print("\nHand-picked cases (eval/drift_cases.jsonl):")
    score_cases(Linker(gates, None, keps))


if __name__ == "__main__":
    main()
