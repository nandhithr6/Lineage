"""Q&A evaluation: Lineage (graph tools) vs a RAG baseline (same facts as text, no graph).  CLAUDE.md §12.B

Modes
  retrieval (default, no LLM key needed): do the facts needed to answer even REACH the answerer?
     Lineage  = the output of the one correct tool for the question (an ORACLE tool choice: an upper bound for the agent).
     RAG      = the top-5 passages by similarity (real retrieval, no oracle).
  llm (needs LLM_BASE_URL/LLM_API_KEY/LLM_MODEL): both systems answer with the SAME model; scored on accuracy, source correctness,
     refusal quality (unanswerable questions) and median latency.

Run: uv run --env-file .env python -m eval.run_qa [--mode retrieval|llm] [--chart]
All gold questions are DRAFT until a human verifies them (eval/gold_review.md); results say so.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import time
from collections import defaultdict
from pathlib import Path

from eval.baseline_rag import Retriever, build_corpus, rag_prompt
from lineage.agent.llm import LLM, FallbackLLM, ProviderError, build_llm
from lineage.agent.loop import run_agent
from lineage.agent.tools import ToolBox
from lineage.config import load_settings
from lineage.graph.client import connect
from lineage.link.linker import read_jsonl

RESULTS = Path("eval/results")
REFUSAL_WORDS = ["insufficient evidence", "cannot", "can't", "can not", "no data", "not available", "don't have", "do not have", "unable", "not contain", "no information"]
CATEGORIES = ["ownership", "expertise", "time_travel", "decision_lineage", "drift", "multi_hop"]


def present(item, low: str) -> bool:
    """An item is a string, or a list of acceptable alternatives (a GitHub handle OR the person's display name): any one counts."""
    alts = [item] if isinstance(item, str) else item
    return any(a.lower() in low for a in alts)


def passes(checks: list[dict], text: str, include_phrase: bool) -> bool:
    low = text.lower()
    for c in checks:
        if c.get("refusal"):
            if not any(w in low for w in REFUSAL_WORDS):
                return False
        elif c.get("phrase") and not include_phrase:
            continue
        elif "all" in c:
            if not all(present(s, low) for s in c["all"]):
                return False
        elif "any" in c:
            if sum(present(s, low) for s in c["any"]) < c.get("min", 1):
                return False
    return True


def fact_coverage(checks: list[dict], text: str) -> float:
    """Share of required facts (the 'all' strings, and min of the 'any' groups) present in the text, ignoring yes/no wording."""
    low = text.lower()
    need = got = 0
    for c in checks:
        if c.get("phrase") or c.get("refusal"):
            continue
        if "all" in c:
            need += len(c["all"])
            got += sum(present(s, low) for s in c["all"])
        elif "any" in c:
            k = c.get("min", 1)
            need += k
            got += min(k, sum(present(s, low) for s in c["any"]))
    return got / need if need else 1.0


def norm(url: str) -> str:
    return url.split("#")[0]


def source_hit(gold: list[str], urls: set[str]) -> bool:
    return bool(gold) and bool({norm(g) for g in gold} & {norm(u) for u in urls if u})


def evaluate_answer(q: dict, answer: str, cited_urls: set[str]) -> dict:
    """Score one written answer (LLM mode)."""
    if q["category"] == "unanswerable":
        refused = passes(q["checks"], answer, True)
        return {"correct": refused, "source_ok": None, "refused": refused}
    ok = passes(q["checks"], answer, True)
    return {"correct": ok, "source_ok": source_hit(q["sources"], cited_urls), "refused": any(w in answer.lower() for w in REFUSAL_WORDS)}


# --- retrieval-only -------------------------------------------------------------------------------------
def retrieval_mode(questions: list[dict], tools: ToolBox, retriever: Retriever) -> list[dict]:
    rows = []
    for q in questions:
        if q["category"] == "unanswerable":
            continue
        t = q["oracle_tool"]
        bundle = tools.call(t["name"], t["args"])
        l_text = json.dumps({"evidence": bundle["evidence"], "path": bundle["path"]}, default=str)
        l_urls = {e["source_url"] for e in bundle["evidence"] if e["source_url"]}
        hits = retriever.search(q["question"], 5)
        r_text = " ".join(p.text for p, _ in hits)
        r_urls = {p.source_url for p, _ in hits if p.source_url}
        rows.append({"id": q["id"], "category": q["category"],
                     "lineage_pass": passes(q["checks"], l_text, False), "lineage_cov": fact_coverage(q["checks"], l_text), "lineage_src": source_hit(q["sources"], l_urls),
                     "rag_pass": passes(q["checks"], r_text, False), "rag_cov": fact_coverage(q["checks"], r_text), "rag_src": source_hit(q["sources"], r_urls),
                     "oracle_tool": t["name"], "lineage_status": bundle["status"]})
    return rows


def summarise(rows: list[dict]) -> dict:
    out: dict[str, dict] = {}
    groups = defaultdict(list)
    for r in rows:
        groups[r["category"]].append(r)
        groups["ALL"].append(r)
    for cat, rs in groups.items():
        n = len(rs)
        out[cat] = {"n": n, **{k: round(sum(r[k] for r in rs) / n, 3) for k in ("lineage_pass", "lineage_cov", "lineage_src", "rag_pass", "rag_cov", "rag_src")}}
    return out


# --- LLM mode -------------------------------------------------------------------------------------------
def answer_lineage(q: dict, llm: LLM, tools: ToolBox) -> dict:
    t0 = time.perf_counter()
    try:
        res = run_agent(llm, tools, q["question"])
    except ProviderError as e:
        return {"error": str(e)[:200], "answer": "", "correct": False, "source_ok": False, "refused": False, "latency": time.perf_counter() - t0, "status": "error"}
    a = evaluate_answer(q, res.answer, {e["source_url"] for e in res.citations if e.get("source_url")})
    return {**a, "answer": res.answer, "latency": time.perf_counter() - t0, "status": res.status, "tools": [t["tool"] for t in res.tool_trace], "iterations": res.iterations,
            "unsupported_citations": res.unsupported_citations}


def answer_rag(q: dict, llm: LLM, retriever) -> dict:
    t0 = time.perf_counter()
    try:
        hits = retriever.search(q["question"], 5)
        text = llm.chat(rag_prompt(q["question"], hits), []).text or ""
    except (ProviderError, Exception) as e:  # embedding or chat failure: record it, keep going
        return {"error": str(e)[:200], "answer": "", "correct": False, "source_ok": False, "refused": False, "latency": time.perf_counter() - t0, "status": "error"}
    cited = {hits[int(n) - 1][0].source_url for n in re.findall(r"\[p(\d+)\]", text) if 0 < int(n) <= len(hits)}
    return {**evaluate_answer(q, text, {u for u in cited if u}), "answer": text, "latency": time.perf_counter() - t0, "status": "ok", "retrieved": [p.id for p, _ in hits]}


def llm_mode(questions: list[dict], llm: LLM, tools: ToolBox, retriever, systems: tuple[str, ...] | None = None, out: Path | None = None) -> list[dict]:
    """Run each system on each question with the SAME llm, appending every result to `out` (JSON lines) so a long, rate-limited run can resume.

    `retriever` is one retriever (system name "rag") or a dict of name -> retriever (systems "rag_tfidf", "rag_embedding", ...).
    """
    retrievers = retriever if isinstance(retriever, dict) else {"rag": retriever}
    systems = systems or ("lineage", *retrievers)
    done = {(r["id"], r["system"]): r for r in (read_jsonl(out) if out and out.exists() else [])}
    rows = list(done.values())
    for q in questions:
        for system in systems:
            if (q["id"], system) in done:
                continue
            r = answer_lineage(q, llm, tools) if system == "lineage" else answer_rag(q, llm, retrievers[system])
            row = {"id": q["id"], "category": q["category"], "system": system, **r}
            rows.append(row)
            if out:
                with out.open("a") as f:
                    f.write(json.dumps(row, default=str) + "\n")
            print(f"  {q['id']:5s} {system:8s} correct={r['correct']!s:5s} status={r['status']:<22s} {r['latency']:5.1f}s" + (f"  ERROR {r['error'][:60]}" if r.get("error") else ""), flush=True)
    return rows


def summarise_llm(rows: list[dict]) -> dict:
    out: dict = {}
    for system in sorted({r["system"] for r in rows}):
        rs = [r for r in rows if r["system"] == system]
        ans = [r for r in rs if r["category"] != "unanswerable"]
        un = [r for r in rs if r["category"] == "unanswerable"]
        frac = lambda xs, k: round(sum(bool(r[k]) for r in xs) / len(xs), 3) if xs else None  # noqa: E731
        errs = [r for r in rs if r.get("error")]
        by_cat = {c: {"n": len(g), "accuracy": frac(g, "correct")} for c in CATEGORIES if (g := [r for r in ans if r["category"] == c])}
        out[system] = {"n_answerable": len(ans), "n_unanswerable": len(un), "accuracy": frac(ans, "correct"), "source_correct": frac(ans, "source_ok"),
                       "refusal_quality": frac(un, "correct"), "errors": len(errs), "by_category": by_cat,
                       "median_latency_s": round(statistics.median(r["latency"] for r in rs), 2) if rs else None}
    return out


# --- chart (plain SVG, then PNG through headless Chrome when available) ----------------------------------
def svg_chart(summary: dict, title: str, a_key: str, b_key: str, a_label: str, b_label: str, note: str) -> str:
    cats = [c for c in CATEGORIES if c in summary] + ["ALL"]
    W, H, L, B = 960, 460, 60, 90
    bw, gap = 34, 22
    group = 2 * bw + gap
    step = (W - L - 20) / len(cats)
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="system-ui,Segoe UI,Arial,sans-serif">',
           f'<rect width="{W}" height="{H}" fill="#ffffff"/>', f'<text x="{W / 2}" y="28" text-anchor="middle" font-size="18" font-weight="600" fill="#111827">{title}</text>']
    for i in range(0, 11, 2):
        y = H - B - (H - B - 60) * i / 10
        out += [f'<line x1="{L}" y1="{y:.1f}" x2="{W - 10}" y2="{y:.1f}" stroke="#e5e7eb"/>', f'<text x="{L - 8}" y="{y + 4:.1f}" text-anchor="end" font-size="11" fill="#6b7280">{i * 10}%</text>']
    for j, c in enumerate(cats):
        x0 = L + step * j + (step - group) / 2
        for k, (key, color) in enumerate(((a_key, "#2563eb"), (b_key, "#9ca3af"))):
            v = summary[c][key]
            h = (H - B - 60) * v
            x = x0 + k * (bw + gap / 2)
            out += [f'<rect x="{x:.1f}" y="{H - B - h:.1f}" width="{bw}" height="{h:.1f}" fill="{color}" rx="3"/>',
                    f'<text x="{x + bw / 2:.1f}" y="{H - B - h - 5:.1f}" text-anchor="middle" font-size="11" fill="#111827">{round(v * 100)}%</text>']
        label = c.replace("_", " ") + f" (n={summary[c]['n']})"
        out.append(f'<text x="{x0 + group / 2:.1f}" y="{H - B + 18}" text-anchor="middle" font-size="12" fill="#374151">{label}</text>')
    out += [f'<rect x="{L}" y="{H - 52}" width="12" height="12" fill="#2563eb" rx="2"/><text x="{L + 18}" y="{H - 42}" font-size="12" fill="#111827">{a_label}</text>',
            f'<rect x="{L + 400}" y="{H - 52}" width="12" height="12" fill="#9ca3af" rx="2"/><text x="{L + 418}" y="{H - 42}" font-size="12" fill="#111827">{b_label}</text>',
            f'<text x="{L}" y="{H - 14}" font-size="11" fill="#6b7280">{note}</text>', "</svg>"]
    return "\n".join(out)


def to_png(svg_path: Path, png_path: Path) -> bool:
    chrome = Path("/mnt/c/Program Files/Google/Chrome/Application/chrome.exe")
    if not chrome.exists():
        return False
    html = svg_path.with_suffix(".html")
    html.write_text(f'<html><body style="margin:0">{svg_path.read_text()}</body></html>')
    def win(p: Path) -> str:
        return subprocess.run(["wslpath", "-w", str(p.resolve())], capture_output=True, text=True).stdout.strip()
    subprocess.run([str(chrome), "--headless=new", "--disable-gpu", "--hide-scrollbars", "--window-size=960,460", f"--screenshot={win(png_path)}", f"file:///{win(html)}"],
                   capture_output=True, timeout=60)
    html.unlink(missing_ok=True)
    return png_path.exists()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["retrieval", "llm", "chart-llm"], default="retrieval")
    ap.add_argument("--chart", action="store_true")
    ap.add_argument("--systems", default="lineage,rag_tfidf", help="comma list for --mode llm: lineage, rag_tfidf, rag_embedding")
    ap.add_argument("--limit", type=int, default=0, help="only the first N questions (smoke test)")
    ap.add_argument("--only", default="", help="comma list of question ids")
    args = ap.parse_args()
    if args.mode == "chart-llm":  # draw the real LLM-run result from the saved summary; no model or graph calls
        S = json.loads((RESULTS / "qa_llm_summary.json").read_text())["summary"]
        a, b = S["lineage"], S["rag_tfidf"]
        summ = {c: {"n": a["by_category"][c]["n"], "lineage": a["by_category"][c]["accuracy"], "rag": b["by_category"][c]["accuracy"]} for c in CATEGORIES if c in a["by_category"]}
        summ["ALL"] = {"n": a["n_answerable"], "lineage": a["accuracy"], "rag": b["accuracy"]}
        svg = svg_chart(summ, "Answer accuracy, same model for both systems (Gemini flash-lite)", "lineage", "rag", "Lineage: graph tools + agent loop", "RAG baseline: top-5 passages (TF-IDF)",
                        "36 DRAFT gold questions, one run, small sample. Gold set was revised after seeing early runs (see eval/README.md).")
        (RESULTS / "qa_llm.svg").write_text(svg)
        print("chart:", "PNG written" if to_png(RESULTS / "qa_llm.svg", RESULTS / "qa_llm.png") else "SVG only")
        return
    s = load_settings()
    tools = ToolBox(connect(s).select_graph(s.falkordb_graph))
    questions = read_jsonl(Path("eval/gold_questions.jsonl"))
    retriever = Retriever(build_corpus())  # TF-IDF; replaced by the embedding retriever in --mode llm
    RESULTS.mkdir(parents=True, exist_ok=True)
    if args.mode == "retrieval":
        rows = retrieval_mode(questions, tools, retriever)
        summ = summarise(rows)
        (RESULTS / "qa_retrieval.json").write_text(json.dumps({"status": "DRAFT gold questions", "summary": summ, "rows": rows}, indent=1))
        print(f"{'category':18s} {'n':>3s} | {'Lineage pass':>12s} {'cov':>5s} {'src':>5s} | {'RAG pass':>9s} {'cov':>5s} {'src':>5s}")
        for c in CATEGORIES + ["ALL"]:
            if c in summ:
                r = summ[c]
                print(f"{c:18s} {r['n']:3d} | {r['lineage_pass']:12.0%} {r['lineage_cov']:5.0%} {r['lineage_src']:5.0%} | {r['rag_pass']:9.0%} {r['rag_cov']:5.0%} {r['rag_src']:5.0%}")
        if args.chart:
            svg = svg_chart(summ, "Can the answerer even see the facts? (retrieval only, no LLM)", "lineage_pass", "rag_pass",
                            "Lineage: output of the correct graph tool (oracle choice)", "RAG baseline: top-5 passages (TF-IDF)",
                            "36 DRAFT gold questions. Share of questions whose required facts are all present. Not an LLM accuracy result.")
            (RESULTS / "qa_retrieval.svg").write_text(svg)
            print("chart:", "PNG written" if to_png(RESULTS / "qa_retrieval.svg", RESULTS / "qa_retrieval.png") else "SVG only (no headless Chrome found)")
    else:
        llm = build_llm(s, fallback=False)  # ONE provider (Gemini) for both systems: the comparison must be fair
        names = [p.name for p in llm.providers]
        if names != ["gemini"]:
            raise SystemExit(f"The fair comparison needs Gemini only; configured chain is {names}. Set GEMINI_API_KEY and GEMINI_MODEL.")
        systems = tuple(args.systems.split(","))
        retrievers = {}
        if "rag_tfidf" in systems:
            retrievers["rag_tfidf"] = retriever
        if "rag_embedding" in systems:
            from lineage.agent.embeddings import GeminiEmbedder
            from eval.baseline_rag import EmbeddingRetriever
            retrievers["rag_embedding"] = EmbeddingRetriever(build_corpus(), GeminiEmbedder.from_settings(s))  # needs the whole corpus in the embedding cache
        out = RESULTS / "qa_llm.jsonl"
        qs = [q for q in questions if (not args.only or q["id"] in args.only.split(","))][: args.limit or None]
        rows = llm_mode(qs, llm, tools, retrievers, systems, out)
        summ = summarise_llm(rows)
        meta = {"status": "DRAFT gold questions", "model": s.gemini_model, "provider": "gemini only (no fallback)", "systems": list(systems),
                "llm_stats": {p.name: p.stats for p in llm.providers}, "questions_run": len(qs), "summary": summ}
        (RESULTS / "qa_llm_summary.json").write_text(json.dumps(meta, indent=1))
        print(json.dumps(meta, indent=1))


if __name__ == "__main__":
    main()
