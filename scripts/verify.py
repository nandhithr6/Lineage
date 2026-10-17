#!/usr/bin/env python3
"""Walk through the AI-assisted drafts one item at a time, show what you need to decide, and record YOUR verdict in the file (saved after every answer, so you can quit any time).

    python3 scripts/verify.py status                  progress on every file
    python3 scripts/verify.py links [--sample 25]     eval/link_audit.jsonl: is this docs label really about this feature gate?
    python3 scripts/verify.py prs [--sample 20]       eval/pr_kep_audit.jsonl: does this pull request really belong to this design document?
    python3 scripts/verify.py semantic                the 4 links the embedding signal would add (eval/results/semantic_real_claims.json)
    python3 scripts/verify.py findings                eval/finding_audit.jsonl: is this label really stale?
    python3 scripts/verify.py answers                 the saved Ask answers: is every claim backed by the evidence shown?
    python3 scripts/verify.py gold                    eval/gold_review.md: tick each question whose expected answer is right
    python3 scripts/verify.py report                  precision of the confident links from your verdicts (with an interval)

Keys at each prompt: y = yes/correct, n = no/wrong, u = unsure, s = skip for now, q = quit. Standard library only. See docs/VERIFY.md for how to judge each kind of item.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import re
import sys
import textwrap
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WHO = "owner (human check)"


def read_jsonl(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]


def write_jsonl(p: Path, rows: list[dict]) -> None:
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))


def snap(name: str) -> list[dict]:
    return read_jsonl(ROOT / "snapshot" / name)


def wrap(s: str, n: int = 100, indent: str = "   ") -> str:
    return "\n".join(textwrap.fill(line, n, initial_indent=indent, subsequent_indent=indent) if line.strip() else "" for line in str(s).splitlines())


def ask(prompt: str = "verdict [y/n/u/s/q]: ", allowed: str = "ynusq") -> str:
    while True:
        try:
            a = input(prompt).strip().lower()[:1]
        except (EOFError, KeyboardInterrupt):
            print()
            return "q"
        if a in allowed:
            return a
        print(f"   please type one of: {', '.join(allowed)}")


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - m) / d, (c + m) / d)


def header(i: int, total: int, title: str) -> None:
    print("\n" + "=" * 100 + f"\n[{i}/{total}] {title}\n" + "-" * 100)


# ---- links -----------------------------------------------------------------------------------------------------------------------------
def links(args) -> None:
    path = ROOT / "eval/link_audit.jsonl"
    rows = read_jsonl(path)
    labels = {x["id"]: x for x in snap("doc_labels.jsonl")}
    gates = {g["name"]: g for g in snap("gates.jsonl")}
    todo = [r for r in rows if not r.get("human_verdict")]
    if args.sample and len(todo) > args.sample:
        wrong = [r for r in todo if r["verdict"] == "wrong"]  # the AI's own doubts are always worth a human look
        rest = [r for r in todo if r["verdict"] != "wrong"]
        random.Random(args.seed).shuffle(rest)
        todo = wrong + rest[: max(0, args.sample - len(wrong))]
    print(f"{len(todo)} links to check ({sum(1 for r in rows if r.get('human_verdict'))} already done). Question for each: IS THE DOCS LABEL REALLY ABOUT THIS FEATURE GATE?")
    for i, r in enumerate(todo, 1):
        lab, gate = labels.get(r["claim_id"]), gates.get(r["linked_gate"])
        header(i, len(todo), f"{r['claim_id'].split('/')[-1]}  ->  {r['linked_gate']}   (linker confidence {r['confidence']}; the AI said: {r['verdict']})")
        if lab:
            print(f" DOCS SECTION \"{lab['heading']}\" on page \"{lab['page_title']}\"  {lab['shortcode']}\n   open: {lab['source_url']}")
            print(wrap(lab["section_text"][:700].replace("\n", " ")))
        if gate:
            stages = "; ".join(f"{s['stage']} {s['from_version']}" + (f"-{s['to_version']}" if s["to_version"] else "") for s in gate["stages"])
            print(f"\n FEATURE GATE {gate['name']}  ({stages}{'; removed' if gate['removed'] else ''})\n   open: {gate['source_url']}")
            print(wrap(gate["description"][:500].replace("\n", " ")))
        if r.get("note"):
            print(f"\n AI note: {r['note']}")
        a = ask()
        if a == "q":
            break
        if a == "s":
            continue
        r["human_verdict"] = {"y": "correct", "n": "wrong", "u": "unsure"}[a]
        r["human_labeled_by"], r["human_date"] = WHO, date.today().isoformat()
        write_jsonl(path, rows)


# ---- prs ------------------------------------------------------------------------------------------------------------------------------
def prs(args) -> None:
    path = ROOT / "eval/pr_kep_audit.jsonl"
    rows = read_jsonl(path)
    bodies = {p["number"]: p for p in snap("prs.jsonl")}
    keps = {k["id"]: k for k in snap("keps.jsonl")}
    todo = [r for r in rows if not r.get("human_verdict")]
    if args.sample and len(todo) > args.sample:
        random.Random(args.seed).shuffle(todo)
        todo = todo[: args.sample]
    print(f"{len(todo)} PR->KEP links to check. Question: IS THIS PULL REQUEST PART OF (OR DIRECTLY ABOUT) THIS DESIGN DOCUMENT? A mere mention is 'n'. 'weak' rows are expected to be mentions only.")
    for i, r in enumerate(todo, 1):
        header(i, len(todo), f"PR #{r['pr']} -> KEP {r['kep']}   [{r['group']}: {r['method']} {r['confidence']}]")
        pr = bodies.get(r["pr"], {})
        print(f" PR: {r['pr_title']}\n   open: {r['pr_url']}")
        print(wrap((pr.get("body") or "")[:450].replace("\n", " ")))
        k = keps.get(r["kep"], {})
        print(f"\n KEP: {r['kep_title']}\n   open: {k.get('source_url', '')}")
        print(wrap((k.get("summary") or "")[:350].replace("\n", " ")))
        print(f"\n why the linker linked them: {r['evidence']}")
        if r.get("ai_verdict"):
            print(f" AI note: {r['ai_verdict']}")
        a = ask()
        if a == "q":
            break
        if a == "s":
            continue
        r["human_verdict"] = {"y": "correct", "n": "wrong", "u": "unsure"}[a]
        r["human_labeled_by"], r["human_date"] = WHO, date.today().isoformat()
        write_jsonl(path, rows)


# ---- semantic ---------------------------------------------------------------------------------------------------------------------------
def semantic(args) -> None:
    path = ROOT / "eval/results/semantic_real_claims.json"
    data = json.loads(path.read_text())
    labels = {x["id"]: x for x in snap("doc_labels.jsonl")}
    gates = {g["name"]: g for g in snap("gates.jsonl")}
    todo = [x for x in data["newly_confident"] if not x.get("human_verdict")]
    print(f"{len(todo)} links that become confident ONLY with the embedding signal. Question: IS THE DOCS LABEL REALLY ABOUT THIS FEATURE GATE? If all are 'y', the signal is safe to switch on.")
    for i, x in enumerate(todo, 1):
        lab, gate = labels.get(x["claim"]), gates.get(x["gate"])
        header(i, len(todo), f"{x['claim'].split('/')[-1]}  ->  {x['gate']}   (confidence {x['confidence']}; signals {x['signals']})")
        if lab:
            print(f" DOCS SECTION \"{lab['heading']}\"  {lab['shortcode']}\n   open: {lab['source_url']}")
            print(wrap(lab["section_text"][:700].replace("\n", " ")))
        if gate:
            print(f"\n FEATURE GATE {gate['name']}\n   open: {gate['source_url']}")
            print(wrap(gate["description"][:500].replace("\n", " ")))
        a = ask()
        if a == "q":
            break
        if a == "s":
            continue
        x["human_verdict"] = {"y": "correct", "n": "wrong", "u": "unsure"}[a]
        x["human_labeled_by"], x["human_date"] = WHO, date.today().isoformat()
        path.write_text(json.dumps(data, indent=1))
    done = [x for x in data["newly_confident"] if x.get("human_verdict")]
    if done and len(done) == len(data["newly_confident"]):
        ok = sum(x["human_verdict"] == "correct" for x in done)
        print(f"\n{ok}/{len(done)} of the new links are correct. " + ("All correct: you can enable the signal (python -m lineage.link.linker --semantic) after telling Claude." if ok == len(done) else "Not all correct: leave the signal off."))


# ---- findings ---------------------------------------------------------------------------------------------------------------------------
def findings(args) -> None:
    path = ROOT / "eval/finding_audit.jsonl"
    rows = read_jsonl(path)
    labels = {x["id"]: x for x in snap("doc_labels.jsonl")}
    gates = {g["name"]: g for g in snap("gates.jsonl")}
    links_ = {r["claim_id"]: r for r in snap("links.jsonl")}
    todo = [r for r in rows if not r.get("human_verdict")]
    print(f"{len(todo)} findings to check. Question: IS THE LABEL REALLY OUT OF DATE (is the feature really in another stage than the docs claim)?")
    for i, r in enumerate(todo, 1):
        lab = labels.get(r["claim_id"])
        cand = (links_.get(r["claim_id"], {}).get("candidates") or [{}])[0]
        gate = gates.get(cand.get("gate", ""))
        header(i, len(todo), f"{r['kind']}: {r['claim_id'].split('/')[-1]}   (AI said: {r['verdict']})")
        if lab:
            print(f" DOCS SAY: {lab['shortcode']}  in section \"{lab['heading']}\"\n   open: {lab['source_url']}")
        if gate:
            stages = "; ".join(f"{s['stage']} {s['from_version']}" + (f"-{s['to_version']}" if s["to_version"] else "") for s in gate["stages"])
            print(f"\n GATE {gate['name']}: {stages}{'; removed' if gate['removed'] else ''}\n   open: {gate['source_url']}")
        print(f"\n AI note: {r.get('note', '')}")
        a = ask()
        if a == "q":
            break
        if a == "s":
            continue
        r["human_verdict"] = {"y": "true_finding", "n": "false_finding", "u": "unsure"}[a]
        r["human_labeled_by"], r["human_date"] = WHO, date.today().isoformat()
        write_jsonl(path, rows)


# ---- answers ----------------------------------------------------------------------------------------------------------------------------
def answers(args) -> None:
    path = ROOT / "eval/answers_review.jsonl"
    review = {r["key"]: r for r in read_jsonl(path)} if path.exists() else {}
    saved = [r for r in read_jsonl(ROOT / "snapshot/demo_answers.jsonl")]
    todo = [r for r in saved if r["key"] not in review]
    print(f"{len(todo)} saved answers to check. Question: IS EVERY CLAIM IN THE ANSWER BACKED BY THE EVIDENCE ROWS SHOWN (and does the answer address the question)?")
    for i, r in enumerate(todo, 1):
        resp = r["response"]
        header(i, len(todo), f"[{r.get('mode', 'graph')}] {r['question']}")
        print(" ANSWER:\n" + wrap(resp["answer"]))
        print("\n EVIDENCE the answer cites:")
        cited = {c["id"] for c in resp.get("citations", [])} or {e["id"] for e in resp["evidence"][:8]}
        for e in resp["evidence"]:
            if e["id"] in cited:
                props = ", ".join(f"{k}: {str(v)[:50]}" for k, v in list((e.get("props") or {}).items())[:3] if v not in (None, ""))
                print(f"   [{e['id']}] {e['type']}: {e.get('from')}" + (f" -> {e['to']}" if e.get("to") else "") + (f"  ({props})" if props else "") + (f"\n        {e['source_url']}" if e.get("source_url") else ""))
        a = ask()
        if a == "q":
            break
        if a == "s":
            continue
        review[r["key"]] = {"key": r["key"], "question": r["question"], "mode": r.get("mode", "graph"), "human_verdict": {"y": "supported", "n": "not_supported", "u": "unsure"}[a],
                            "human_labeled_by": WHO, "human_date": date.today().isoformat()}
        write_jsonl(path, list(review.values()))


# ---- gold -------------------------------------------------------------------------------------------------------------------------------
def gold(args) -> None:
    path = ROOT / "eval/gold_review.md"
    text = path.read_text()
    items = list(re.finditer(r"^- \[( |x)\] \*\*(\w+)\*\* \((\w+)\): (.*?)(?=^- \[|\Z)", text, re.M | re.S))
    todo = [m for m in items if m.group(1) == " "]
    print(f"{len(todo)} gold questions to tick ({len(items) - len(todo)} already ticked). Open the source link, check that the EXPECTED answer is right. y ticks the box; n adds 'WRONG' so it can be fixed (the file is a DRAFT: gold_questions.jsonl may then be corrected by Claude).")
    for i, m in enumerate(todo, 1):
        header(i, len(todo), f"{m.group(2)} ({m.group(3)})")
        print(wrap(m.group(4).strip()))
        a = ask("verdict [y = tick / n = wrong / s = skip / q = quit]: ", "ynsq")
        if a == "q":
            break
        if a == "s":
            continue
        text = text.replace(m.group(0), m.group(0).replace("- [ ]", "- [x]" if a == "y" else "- [ ] **WRONG (owner check)**", 1), 1)
        path.write_text(text)


# ---- status / report --------------------------------------------------------------------------------------------------------------------
def status(args) -> None:
    def count(path: Path, key="human_verdict"):
        rows = read_jsonl(path) if path.exists() else []
        return sum(1 for r in rows if r.get(key)), len(rows)
    sem = json.loads((ROOT / "eval/results/semantic_real_claims.json").read_text())["newly_confident"]
    gold_text = (ROOT / "eval/gold_review.md").read_text()
    review = ROOT / "eval/answers_review.jsonl"
    print("progress (done / total):")
    print("  links     eval/link_audit.jsonl          %d / %d" % count(ROOT / "eval/link_audit.jsonl"))
    print("  prs       eval/pr_kep_audit.jsonl        %d / %d" % count(ROOT / "eval/pr_kep_audit.jsonl"))
    print("  semantic  4 new links                    %d / %d" % (sum(1 for x in sem if x.get("human_verdict")), len(sem)))
    print("  findings  eval/finding_audit.jsonl       %d / %d" % count(ROOT / "eval/finding_audit.jsonl"))
    print("  answers   saved Ask answers              %d / %d" % ((len(read_jsonl(review)) if review.exists() else 0), len(read_jsonl(ROOT / "snapshot/demo_answers.jsonl"))))
    print("  gold      eval/gold_review.md ticks      %d / %d" % (gold_text.count("- [x]"), gold_text.count("- [x]") + gold_text.count("- [ ]")))


def report(args) -> None:
    rows = [r for r in read_jsonl(ROOT / "eval/link_audit.jsonl") if r.get("human_verdict") in ("correct", "wrong")]
    if not rows:
        print("No human verdicts on links yet: run `python3 scripts/verify.py links` first.")
        return
    ok = sum(r["human_verdict"] == "correct" for r in rows)
    lo, hi = wilson(ok, len(rows))
    agree = sum((r["human_verdict"] == "correct") == (r["verdict"] == "correct") for r in rows)
    print(f"Confident links you checked: {len(rows)}; correct {ok} ({ok / len(rows):.0%}, 95% interval {lo:.0%}-{hi:.0%}).")
    print(f"You agreed with the AI on {agree}/{len(rows)}." + ("" if len(rows) == len(read_jsonl(ROOT / 'eval/link_audit.jsonl')) else " (a sample, not all 73: the interval says how far to trust it)"))
    pr_file = ROOT / "eval/pr_kep_audit.jsonl"
    prs_ = [r for r in (read_jsonl(pr_file) if pr_file.exists() else []) if r.get("human_verdict") in ("correct", "wrong") and r["group"] == "strong"]
    if prs_:
        ok2 = sum(r["human_verdict"] == "correct" for r in prs_)
        lo2, hi2 = wilson(ok2, len(prs_))
        print(f"Strong PR->KEP links you checked: {len(prs_)}; correct {ok2} ({ok2 / len(prs_):.0%}, 95% interval {lo2:.0%}-{hi2:.0%}).")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("task", choices=["status", "links", "prs", "semantic", "findings", "answers", "gold", "report"])
    ap.add_argument("--sample", type=int, default=0, help="check only N random items (links: the AI's 'wrong' ones are always included)")
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()
    if args.task != "status" and not sys.stdin.isatty():
        print("This is interactive: run it in a terminal.")
        return
    {"status": status, "links": links, "prs": prs, "semantic": semantic, "findings": findings, "answers": answers, "gold": gold, "report": report}[args.task](args)


if __name__ == "__main__":
    main()
