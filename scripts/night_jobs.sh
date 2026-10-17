#!/bin/sh
# Long, quota-bound jobs. Safe to re-run: every step resumes from its cache / skips finished rows. Run from the repo root:
#   nohup sh scripts/night_jobs.sh > night_jobs.log 2>&1 &
# 1. embed the remaining doc/KEP passages (free tier: ~1,000 texts/day, resumable cache)
# 2. re-run ONLY the held-out rows that ended in a provider error (503s) - the questions themselves are never edited
# 3. run the embedding RAG baseline on the main gold set
set -u
RUN="uv run --env-file .env python"
echo "== $(date -u +%FT%TZ) embeddings"; $RUN -m eval.embed_corpus
echo "== $(date -u +%FT%TZ) held-out: retry errored rows"; $RUN -m eval.run_qa --mode llm --systems lineage,rag_tfidf --gold eval/gold_heldout.jsonl --tag _heldout --retry-errors
echo "== $(date -u +%FT%TZ) embedding RAG baseline (main gold set)"; $RUN -m eval.run_qa --mode llm --systems rag_embedding
echo "== $(date -u +%FT%TZ) done"
