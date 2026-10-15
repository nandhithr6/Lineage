"""Ask the agent a question from the terminal: `uv run --env-file .env python -m lineage.agent.cli "your question"`.

Prints the answer, every cited evidence item with its source link, the tool calls used and which provider answered each request.
Use --no-fallback to force Gemini only.
"""
from __future__ import annotations

import argparse
import logging

from lineage.agent.llm import FallbackLLM, build_llm
from lineage.agent.loop import run_agent
from lineage.agent.tools import ToolBox
from lineage.config import load_settings
from lineage.graph.client import connect


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("--no-fallback", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="  [%(levelname)s] %(message)s")
    s = load_settings()
    llm = build_llm(s, fallback=not args.no_fallback)
    res = run_agent(llm, ToolBox(connect(s).select_graph(s.falkordb_graph)), args.question)
    print(f"\nQUESTION: {args.question}\nSTATUS: {res.status}   iterations: {res.iterations}\n\nANSWER:\n{res.answer}\n")
    if res.unsupported_citations:
        print("unsupported citations removed:", res.unsupported_citations)
    print("CITED EVIDENCE:")
    for e in res.citations:
        print(f"  [{e['id']}] {e['type']:<11} {e['from']} -> {e['to']}\n        {e['source_url'] or '(derived from the graph)'}")
    print("\nTOOLS USED:", "; ".join(f"{t['tool']}({', '.join(map(str, t['params'].values()))}) -> {t['evidence']} items" for t in res.tool_trace) or "none")
    if isinstance(llm, FallbackLLM):
        print(f"PROVIDERS: answered={llm.answered} fallbacks={llm.fallbacks} stats=" + str({p.name: p.stats for p in llm.providers}))


if __name__ == "__main__":
    main()
