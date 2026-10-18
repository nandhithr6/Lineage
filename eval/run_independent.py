"""Run the independent held-out set (eval/heldout_independent.jsonl) once through Lineage, TF-IDF retrieval and FalkorDB vector retrieval, same Gemini model.

This script only COLLECTS answers: the gold answers are free text, so no pass/fail is computed here (the existing scorer needs machine-checkable `checks`). The file is
read by code only; its hash must match eval/results/heldout_independent_freeze.json. Rows are appended to eval/results/independent_answers.jsonl, so a rate-limited run resumes.

Run: .venv/bin/python -m eval.run_independent
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from eval.baseline_rag import Retriever, build_corpus
from eval.run_qa import answer_lineage, answer_rag
from lineage.agent.llm import build_llm
from lineage.agent.local_embeddings import LocalEmbedder
from lineage.config import load_settings
from lineage.agent.tools import ToolBox
from lineage.graph.client import connect
from lineage.link.linker import read_jsonl
from lineage.search.vector import FalkorRetriever

GOLD = Path("eval/heldout_independent.jsonl")
FREEZE = Path("eval/results/heldout_independent_freeze.json")
OUT = Path("eval/results/independent_answers.jsonl")
DROP = {"correct", "source_ok", "source_ok_strict", "refused"}  # computed against empty checks, so meaningless here


def main() -> None:
    rec = json.loads(FREEZE.read_text())
    got = hashlib.sha256(GOLD.read_bytes()).hexdigest()
    if got != rec["sha256"]:
        raise SystemExit(f"{GOLD} no longer matches its freeze record ({got[:12]} != {rec['sha256'][:12]}). Refusing to run.")
    s = load_settings()
    tools = ToolBox(connect(s).select_graph(s.falkordb_graph))
    llm = build_llm(s, fallback=False)
    if [p.name for p in llm.providers] != ["gemini"]:
        raise SystemExit("The fair comparison needs Gemini only (set GEMINI_API_KEY and GEMINI_MODEL).")
    systems = {"lineage": None, "rag_tfidf": Retriever(build_corpus()), "rag_vector": FalkorRetriever(tools.g, LocalEmbedder())}
    done = {(r["id"], r["system"]) for r in read_jsonl(OUT)} if OUT.exists() else set()
    for q in read_jsonl(GOLD):
        shim = {"id": q["id"], "category": q["category"], "question": q["question"], "checks": [], "sources": [], "oracle_tool": None}  # no scoring here
        for name, retriever in systems.items():
            if (q["id"], name) in done:
                continue
            r = answer_lineage(shim, llm, tools) if name == "lineage" else answer_rag(shim, llm, retriever)
            row = {"id": q["id"], "category": q["category"], "system": name, **{k: v for k, v in r.items() if k not in DROP}}
            with OUT.open("a") as f:
                f.write(json.dumps(row, default=str) + "\n")
            print(f"{q['id']:8s} {name:10s} status={row['status']:<22s} {row['latency']:5.1f}s" + (f"  ERROR {row['error'][:60]}" if row.get("error") else ""), flush=True)


if __name__ == "__main__":
    main()
