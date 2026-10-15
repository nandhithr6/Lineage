# Evaluation notes: read before quoting any number

Two things are evaluated (CLAUDE.md §12): the **drift detector** (`eval/score_drift.py`, report in `eval/results/drift_report.md`) and **Q&A** (`eval/run_qa.py`).
This file covers Q&A. Every gold question is a **DRAFT** until a person verifies it (`eval/gold_review.md`).

## Final result (one model for both systems, Gemini only, no fallback)

Model `gemini-3.1-flash-lite` (a small, cheap model: both systems would likely score higher with a stronger one). 36 answerable + 6 unanswerable draft questions, temperature 0, one run.

| | Lineage (graph tools + agent loop) | RAG baseline (TF-IDF top-5 passages) |
|---|---|---|
| Accuracy, 36 answerable | **94% (34/36)**, 95% interval 82-98% | **64% (23/36)**, 95% interval 48-78% |
| Questions only this system got right | 12 | 1 |
| Source correctness | 86% | 50% |
| Refusal on 6 unanswerable | 6/6 | 6/6 |
| Median latency | 10.5 s | 4.5 s |
| By category | ownership 100, expertise 100, time travel 100, decision lineage 100, drift 83, multi-hop 83 | ownership 83, expertise 83, time travel 67, decision lineage 100, drift 50, multi-hop **0** |

![chart](results/qa_llm.png)

The gap is largest on multi-hop questions (the RAG baseline needs the right passages from two different documents to land in the top 5; they almost never do), and
on drift questions. Both systems tie on decision lineage, where one passage holds the answer.

## How much to trust it (honest list)

1. **The gold set was revised after seeing runs, by the same party that built the system.** Three rounds, all kept in `eval/results/run1_before_fixes/`, `run2_after_tool_fixes/` and the final files:
   - run 1: Lineage 86%, refusal 83%. Misses were mostly *our* bugs and scoring: a tool that rejected the path `OWNERS` for the repo root, a case-sensitive docs search, a check that wanted a KEP's exact slug when the answer said "KEP-1495", and an "unanswerable" question that became answerable when PR data was added.
   - run 2 (tools fixed, whole Lineage system rerun): Lineage 92%, but refusal fell to 67%: the agent looped instead of refusing when docs ownership history was asked for (a tool returned an empty result without a reason), and the replacement "unanswerable" question (pkg/scheduler) was in fact answerable from PR data labelled sig/node.
   - final: the tool now says WHY history is unavailable; the unanswerable question asks about the website repo (no PR data); the "who actively reviews pkg/volume" gold answer is computed from raw PR data.
   Every change made the question or tool more correct, but this is "training on the test set" in spirit. Expect lower numbers on a fresh set written by someone else.
2. **Gold answers are computed from the raw snapshot files, not from the graph**, so the truth does not depend on the system under test; but they are unverified by a human.
3. **The RAG baseline is generous in one way and weak in another.** Generous: every fact is written out as plain sentences (OWNERS per release, gate stage ranges, KEP metadata). Weak: retrieval is TF-IDF, not embeddings. The embedding variant (`--systems rag_embedding`) is built but only 860 of its 2,398 passages are embedded: the free key's embedding quota ran out. It resumes automatically (`python -m eval.embed_corpus`) after the quota resets, so the comparison against real vector RAG is still **missing**.
4. **Small sample.** 36 questions; the two intervals above barely overlap, but a different set could move either number several points. Single run at temperature 0; no repeats.
5. **Latency is dominated by the free tier** (pacing, 503 "high demand" retries), not by the graph.
6. **The two remaining Lineage misses are real limits**: the Pod OS label was never confidently linked to a gate, so the honest answer is "insufficient evidence" (counted wrong because the gold says the label is correct); and the multi-hop reviewer question was answered only in part.
7. Quota: the free key allows 20 requests/day on `gemini-3.6-flash` and about 1,000 embedded texts/day; this evaluation used about 280 chat requests on `gemini-3.1-flash-lite`.

## Retrieval-only comparison (no LLM)

`python -m eval.run_qa --chart` measures only whether the needed facts reach the answerer (97% with the correct graph tool vs 75% for RAG top-5). Its graph side is an **oracle tool choice**
(an upper bound), so the real LLM run above is the number to quote.

## Reproduce

```bash
uv run python -m eval.build_gold                                     # draft gold questions from the snapshot
uv run --env-file .env python -m eval.run_qa --mode llm --systems lineage,rag_tfidf   # needs GEMINI_API_KEY/GEMINI_MODEL; resumable (eval/results/qa_llm.jsonl)
uv run python -m eval.run_qa --mode chart-llm                        # draw the chart from the saved summary
```
