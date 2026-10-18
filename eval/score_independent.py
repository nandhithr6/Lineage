"""Score the independent held-out answers (eval/results/independent_answers.jsonl) against the free-text gold answers.

Gold answers are prose, so a fixed word list cannot score them. Two measures, both disclosed in eval/README.md:
  * correct  - a language-model JUDGE (the same Gemini model as the answerers; temperature 0) is shown the question, the gold answer, the scoring note and ONE system's answer, and
               says whether the answer states what the gold answer states. It never sees the other systems or the system's name. It is a model's opinion, not ground truth.
  * source_ok - the answer cited at least one URL on the same repository path as a gold source URL (lenient: master vs. commit-pinned permalinks are treated as the same file).
A human-readable sheet (eval/results/independent_review.md) lists every judgment with the gold answer so people can check the judge; their corrections are applied by hand and reported.

Run: .venv/bin/python -m eval.score_independent
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
from collections import defaultdict
from pathlib import Path

from lineage.agent.llm import build_llm
from lineage.config import load_settings
from lineage.link.linker import read_jsonl

GOLD = Path("eval/heldout_independent.jsonl")
ANSWERS = Path("eval/results/independent_answers.jsonl")
OUT = Path("eval/results/independent_scores.json")
SHEET = Path("eval/results/independent_review.md")
JUDGE = ("You are grading one answer to a question about the Kubernetes project against a reference answer.\n"
         "Reply with JSON only: {\"correct\": true|false, \"reason\": \"one short sentence\"}.\n"
         "correct=true only if the answer states every fact the reference answer requires (names, versions, paths) and contradicts none of them. A refusal or 'insufficient evidence' is correct=false "
         "unless the reference answer itself says the question cannot be answered. Extra true detail does not matter; a wrong or missing required fact does.\n\n"
         "QUESTION: {q}\nREFERENCE ANSWER: {a}\nSCORING NOTE: {n}\nANSWER TO GRADE:\n{r}")


def path_key(url: str) -> str:
    """repo + file path of a GitHub URL, ignoring the branch or commit (so master and a pinned permalink match)."""
    m = re.match(r"https?://github\.com/([^/]+/[^/]+)/(?:blob|tree)/[^/]+/([^#?]*)", url)
    return f"{m.group(1)}:{m.group(2).strip('/')}" if m else url.split("#")[0].rstrip("/")


def source_ok(gold_urls: list[str], cited: list[str]) -> bool:
    want = {path_key(u) for u in gold_urls}
    return any(path_key(u) in want for u in cited)


def judge(llm, q: dict, answer: str) -> dict:
    if not answer.strip():
        return {"correct": False, "reason": "no answer was produced"}
    prompt = JUDGE.replace("{q}", q["question"]).replace("{a}", q["answer"]).replace("{n}", q.get("scoring_note") or "none").replace("{r}", answer[:3000])
    text = llm.chat([{"role": "user", "content": prompt}], []).text or ""
    m = re.search(r"\{.*\}", text, re.S)
    try:
        d = json.loads(m.group(0)) if m else {}
    except ValueError:
        d = {}
    return {"correct": bool(d.get("correct")), "reason": str(d.get("reason", "judge reply not parseable"))[:200]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--answers", default=str(ANSWERS))
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--sheet", default=str(SHEET))
    args = ap.parse_args()
    out_path, sheet_path = Path(args.out), Path(args.sheet)
    gold = {q["id"]: q for q in read_jsonl(GOLD)}
    rows = read_jsonl(Path(args.answers))
    llm = build_llm(load_settings(), fallback=False)
    scored = []
    for r in rows:
        q = gold[r["id"]]
        j = judge(llm, q, r.get("answer", ""))
        scored.append({"id": r["id"], "category": q["category"], "system": r["system"], "correct": j["correct"], "reason": j["reason"],
                       "source_ok": source_ok(q.get("source_urls", []), r.get("cited_urls", [])), "latency": r.get("latency"), "status": r.get("status")})
        print(f"{r['id']:8s} {r['system']:10s} correct={j['correct']!s:5s}", flush=True)
    summary: dict = {}
    for system in sorted({s["system"] for s in scored}):
        rs = [s for s in scored if s["system"] == system]
        by_cat: dict = defaultdict(list)
        for s in rs:
            by_cat[s["category"]].append(s["correct"])
        summary[system] = {"n": len(rs), "correct": sum(s["correct"] for s in rs), "accuracy": round(sum(s["correct"] for s in rs) / len(rs), 3),
                           "source_ok": round(sum(s["source_ok"] for s in rs) / len(rs), 3), "median_latency_s": round(statistics.median(s["latency"] for s in rs if s["latency"]), 1),
                           "by_category": {c: f"{sum(v)}/{len(v)}" for c, v in sorted(by_cat.items())}}
    out_path.write_text(json.dumps({"status": "scored by a language-model judge; not yet human-checked", "summary": summary, "rows": scored}, indent=1))
    ans = {(r["id"], r["system"]): r for r in rows}
    lines = ["# Independent held-out set: review sheet", "", "Judge verdicts for people to check. Tick the ones you disagree with and tell the team; do not edit the questions or gold answers.", ""]
    for qid, q in gold.items():
        lines += [f"## {qid} ({q['category']})", f"**Question:** {q['question']}", f"**Gold answer:** {q['answer']}", ""]
        for s in (x for x in scored if x["id"] == qid):
            lines += [f"- **{s['system']}** judge: {'CORRECT' if s['correct'] else 'WRONG'} (source cited: {s['source_ok']}) - {s['reason']}",
                      f"  > {ans[(qid, s['system'])].get('answer', '').strip()[:600].replace(chr(10), ' ')}"]
        lines.append("")
    sheet_path.write_text("\n".join(lines))
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
