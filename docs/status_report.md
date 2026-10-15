# Lineage: full status report (updated late 2026-10-15, after the real-LLM and PR-data session)

Project: Lineage, a Kubernetes "company brain" in FalkorDB that finds documentation that has drifted from reality, shows who owns and who actually reviews the code, and proves every answer.
Brief: `CLAUDE.md`. Every technical choice with its reason: `DECISIONS.md`. Full write-up: `README.md`. Evaluation caveats: `eval/README.md`.

## 1. What exists and runs (all verified)

One command starts everything: `docker compose up -d --build` (FalkorDB, one-shot loader + detector, API). 138 Python + 11 web tests pass.

| Area | State |
|---|---|
| Graph | 6,902 nodes and 30,263 edges (incl. 3,073 PRs, 1,224 people) in FalkorDB; every edge carries source_url, extracted_by, confidence, recorded_at; ownership edges carry valid_from/valid_to |
| Drift detector | 2 findings (both real), 4 review items; findings need an anchored or hand-verified link |
| Seed link | still hand-verified (disclosed); KEP-path signal built but does not resolve it (see section 5) |
| LLM | Gemini primary + Groq fallback, probed model names, backoff/pacing/fallback, provider logged; BOTH verified with real calls; Gemini 3 thought signatures and Groq's full-width citations handled |
| Agent | 10 read-only tools incl. what_changed, experts_for, orphaned_components, sig_leaders; run on 42 questions with real Gemini |
| PR data | 3,073 merged PRs (sig/storage, sig/node, sig/apps; 2024-10-15 to 2026-10-15) fetched with a cached, resumable, rate-limit-aware client for 186 API points; 728 people, 5,278 reviews, 1,754 approvals |
| Official vs actual | `/experts`, `/orphans`, Explore panel: e.g. pkg/volume has 11 listed approvers, 6 active in 12 months, 5 not; mount-utils lists 3 approvers, all inactive, while 9 others are active |
| API / UI | all endpoints incl. /owners, /changes, /experts, /orphans; Findings, Explore (time travel, official vs actual, entity graphs), Ask, About pages |
| Deploy prep | compose stack verified locally with real keys passed through; NOT deployed publicly |

## 2. Real Q&A results (same model for both systems, Gemini only; DRAFT gold set)

| | Lineage | RAG baseline (TF-IDF top-5) |
|---|---|---|
| Accuracy (36 answerable) | 94% (34/36), interval 82-98% | 64% (23/36), interval 48-78% |
| Source correctness | 86% | 50% |
| Refusal (6 unanswerable) | 6/6 | 6/6 |
| Multi-hop | 83% | 0% |
| Median latency | 10.5 s | 4.5 s |

Caveats (details in `eval/README.md`): the gold set is a draft revised over three rounds after seeing runs (86% then 92% then 94%); the model is a small flash-lite; one run; RAG used lexical retrieval because the embedding quota ran out
(the real vector-RAG comparison is still missing); latency is mostly free-tier pacing.

## 3. Drift detector (unchanged; see eval/results/drift_report.md)

2 of 2 reported findings real; confident links on real claims 94.5% correct (69/73) after the since-version rule, 88% before (AI-assisted audit, needs human verification); recall unknown (109 claims only "possible", 10 unresolved).

## 4. Problems found by running for real (all fixed and logged)

Gemini 3 thought-signature requirement; grouped and full-width citation formats; free-tier quotas (20 requests/day on gemini-3.6-flash, ~1,000 embedded texts/day); a **Gemini key leaked into a log through a URL parameter** (fixed; the owner should rotate the key);
release-branch backports making release managers look like volume experts (base-branch filter); tool bugs (root path, case-sensitive search, silent empty results); stale "unanswerable" gold questions.

## 5. OPEN DECISION: the seed link

With the KEP-path signal the true gate and its sibling both score ~0.9 and are capped to 0.79 as a near-tie; the KEP evidence even leans to the sibling (0.72 vs 0.56) because the section is about quota. Hand verification rests on domain knowledge and on the fact that
a gate first introduced at 1.15 cannot be what a label "alpha since v1.8" describes. Option: a veto that removes candidates whose first stage is later than the claimed since-version. It only removes impossible candidates and would resolve the seed automatically,
but it makes the claimed version an input to linking, which was deliberately avoided. Not done without the owner's decision.

## 6. Still blocked on the owner

| Item | Why |
|---|---|
| Rotate the Gemini key | it appeared in a log and a tool result (free tier, but treat as exposed) |
| Embedding quota reset, then `uv run --env-file .env python -m eval.embed_corpus`, then `eval.run_qa --systems rag_embedding` | the real vector-RAG comparison (1,538 of 2,398 passages still to embed) |
| Public deployment (server + Vercel) | needs the owner's accounts; and a free key cannot serve live traffic (20 requests/day per flagship model) |
| Human verification of eval/link_audit.jsonl, eval/finding_audit.jsonl, eval/gold_review.md | AI-assisted drafts; required before quoting |
| README placeholder "What the team did" | the rules require meaningful human contribution; only the owners can state it |
| Repo LICENSE, seed-link veto decision, commits | owner decisions |

## 7. Suggested next priorities

1. Rotate the key; fill the README placeholder; verify the three human-check files (small, required).
2. Decide the seed-link veto. 3. Finish embeddings after the quota resets and run the vector-RAG variant. 4. Deploy and rehearse docs/demo_script.md against it (cut any step that fails).
5. Optional: FalkorDB algo.* (e.g. PageRank on the review graph), sig-scheduling PRs, MCP server.
