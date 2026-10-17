"""Build or check the saved answers for the demo questions.

    uv run --env-file .env python -m lineage.agent.build_demo_cache           # answer every missing/stale demo question with the real agent
    uv run --env-file .env python -m lineage.agent.build_demo_cache --check   # report what is saved, missing or stale (no LLM calls)

An answer is saved only if the agent finished with status "ok" and every citation resolves to evidence from the tools; anything else is reported, not saved.
Re-run it whenever the snapshot or the detector changes: entries for another snapshot are never served.
"""
from __future__ import annotations

import argparse
import logging

from lineage.agent.answer_cache import DEMO_QUESTIONS, HERO_QUESTION, AnswerCache
from lineage.agent.llm import build_llm
from lineage.agent.loop import run_agent
from lineage.agent.tools import ToolBox
from lineage.api.models import AskResponse
from lineage.config import load_settings
from lineage.graph.client import connect

log = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--force", action="store_true", help="re-answer questions that already have a current entry")
    args = ap.parse_args()
    cache = AnswerCache()
    print(f"snapshot id: {cache.sid}")
    todo = [q for q in DEMO_QUESTIONS if args.force or cache.get(q) is None]
    rag_todo = args.force or cache.get(HERO_QUESTION, "retrieval") is None
    for q in DEMO_QUESTIONS:
        print(f"  {'saved  ' if q not in todo else 'MISSING'} {q}")
    print(f"  {'saved  ' if not rag_todo else 'MISSING'} {HERO_QUESTION}  [plain retrieval, for the side-by-side contrast]")
    if cache.stale_entries():
        print(f"  stale entries (other snapshot, never served): {len(cache.stale_entries())}")
    if args.check or not (todo or rag_todo):
        return
    s = load_settings()
    g = connect(s).select_graph(s.falkordb_graph)
    llm = build_llm()
    for q in todo:
        res = run_agent(llm, ToolBox(g), q, [])
        ok = res.status == "ok" and not res.unsupported_citations and res.citations
        print(f"{'SAVE' if ok else 'SKIP'} [{res.status}] {q}\n     {res.answer[:200]!r}")
        if ok:
            resp = AskResponse(answer=res.answer, status=res.status, citations=res.citations, evidence=res.evidence, path=res.path,
                               tool_trace=res.tool_trace, unsupported_citations=res.unsupported_citations, outcome=res.outcome, validation=res.validation)
            cache.put(q, resp.model_dump(), provider=getattr(llm, "name", type(llm).__name__))
    if rag_todo:  # the hero question answered by plain retrieval too, so the contrast never depends on a live model
        from lineage.agent.local_embeddings import LocalEmbedder
        from lineage.search.rag import answer_with_retrieval
        from lineage.search.vector import FalkorRetriever

        resp = answer_with_retrieval(llm, FalkorRetriever(g, LocalEmbedder()), HERO_QUESTION)
        print(f"SAVE [retrieval:{resp['status']}] {HERO_QUESTION}\n     {resp['answer'][:200]!r}")
        cache.put(HERO_QUESTION, resp, provider=getattr(llm, "name", type(llm).__name__), mode="retrieval")
    removed = cache.prune()
    if removed:
        print(f"pruned {removed} entries saved for older snapshots")


if __name__ == "__main__":
    main()
