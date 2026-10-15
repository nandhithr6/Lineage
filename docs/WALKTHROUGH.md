# Lineage: a guided walkthrough (for review, presenting and learning)

Read this top to bottom once (about 40 minutes), then do the hands-on part at the end (about an hour). Function names are given so you can search for them; line numbers drift as code changes.
Everything here was checked against the code and the data on 2026-10-15. Where something is a judgment or a limit, it says so.

---

## 1. The idea in 60 seconds

Kubernetes documentation pages carry little badges: *"alpha since v1.8"*. Someone types them by hand, and nothing updates them when the feature graduates. So some are stale. Lineage finds them **and proves it**.

To prove it, it needs facts from many places connected together: the docs page, the *feature gate* (an on/off switch that has a maturity stage per release), the *KEP* (design document) behind the feature, the *team* that owns it, and the *people* who approve and review the code.
A graph database is the natural shape for that, so everything lives in **FalkorDB**, and every fact carries **where it came from** (a GitHub link pinned to a commit) and **when it was true**.

The product: *find* a stale label, *trace* it through the graph, *explain* claimed-versus-verified with sources, *draft* the fix, and *suggest reviewers*. It never posts anything to Kubernetes.

**Track 3 (company brain) in one sentence:** the Kubernetes project is the company; the graph is its memory; every answer is a graph traversal with receipts.

## 2. The mental model: five layers

```
 SOURCES (public git repos)                    website  enhancements  community  kubernetes   + GitHub API (PRs)
        |  ingest: parse, never guess; log every oddity
 SNAPSHOT (snapshot/*.jsonl, 8 MB, in git)     gates  doc_labels  keps  sigs  owners_edges  team_members  prs  links
        |  link: which gate is a label about? (score + evidence)
        |  load: MERGE into FalkorDB (idempotent)
 GRAPH (FalkorDB)                              6,902 nodes and 30,263 edges; edges carry source_url, extracted_by, confidence, recorded_at (+ valid_from/valid_to)
        |  detect: compare claim with gate history -> Finding nodes
        |  trace / queries / tools: read-only Cypher returning "evidence bundles"
 ACCESS                                        CLI (hero)   agent (LLM + tools)   FastAPI   React UI
```

Two rules hold everywhere: **(1)** an answer's facts come from query results, never from the LLM; **(2)** if evidence is missing, say so ("insufficient evidence") instead of guessing.

## 3. The data (what the raw sources look like, and what we keep)

| Source | What it is | Parser | Key decisions |
|---|---|---|---|
| `website/.../feature-gates/*.md` | one page per gate, YAML front matter: `stages: [{stage, fromVersion, toVersion}]`, `removed` | `lineage/ingest/gates.py` (`parse_gate_file`, `expand_to_releases`) | 489 gates. Two layers: faithful stage *ranges*, and derived one-row-per-release (becomes `STAGE_IN` edges). Odd cases are kept and flagged in `issues`, never fixed silently: a stage that ends nowhere, two stages in one release (`overlap`), removed gates with no removal release. |
| `website/content/en/docs/**` | pages with `{{< feature-state ... >}}` badges | `docs_labels.py` (`extract_labels`) | 453 labels on 207 pages. 265 look their gate up (`feature_gate_name=`, can never be stale); **188 are hard-coded: those are the drift candidates.** Skips code fences and Hugo-escaped examples. Keeps the section text and links around each label (the linker needs them). |
| `enhancements/keps/*/*/kep.yaml` | design documents | `keps.py` | 648 KEPs. KEP key is the path (numbers are not unique). Fixes only unambiguous status typos, logs the rest. |
| `community/sigs.yaml` | the org chart | `sigs.py` | 37 teams/working groups/committees; **emails and employers are dropped here** (privacy). |
| `kubernetes/**/OWNERS`, `OWNERS_ALIASES` | who may approve/review each folder | `owners.py` (`parse_owners`, `collapse`, `build_rows`) | History is **sampled at release tags 1.30-1.37 and latest** and collapsed into intervals (`valid_from`/`valid_to`). Aliases become `Team` nodes. |
| GitHub GraphQL | merged PRs for sig/storage, sig/node, sig/apps, last 2 years | `github_prs.py` (`GithubGraphQL`, `fetch_window`, `normalize`) | 3,073 PRs for 186 API points. Every response cached on disk, resumable, waits out rate limits, token only in a header. |

Why a *snapshot*? It makes everything reproducible and reviewable: the graph can always be rebuilt from `snapshot/` with one command, and judges can read the data in git.

## 4. The graph and the loader

Nodes: `Release, FeatureGate, DocPage, DocSection, Claim, KEP, SIG, Person, Team, CodePath, PR, Finding`. Key edges: `DocPage-HAS_SECTION->DocSection-HAS_CLAIM->Claim-ABOUT->FeatureGate-STAGE_IN->Release`,
`KEP-INTRODUCES->FeatureGate`, `KEP-OWNED_BY->SIG`, `Person-APPROVER_OF/REVIEWER_OF->CodePath`, `Person-MEMBER_OF->Team`, `CodePath-CHILD_OF->CodePath`, `Person-REVIEWED/APPROVED->PR-TOUCHES->CodePath`, `Finding-FOR_CLAIM/FOR_GATE/AS_OF->...`.

**Why a `Claim` node (not just an edge)?** A label might link to zero, one, or several candidate gates, each with its own confidence. A node can hold that; an edge cannot, and unlinked labels would have no home to be counted.

**The loader** (`lineage/graph/loader.py`: `load_all`, `load_gates`, `load_labels`, `load_owners`, `load_prs`, `load_verified`) uses Cypher `UNWIND ... MERGE`. `MERGE` means *find it, or create it*: run the loader twice and you get identical counts (a test checks this),
so a crash means "run it again". Unique constraints on each node key make `MERGE` fast and prevent duplicates. Gotcha we hit: `MERGE` never *removes* anything, so derived edges from the linker are deleted and rebuilt on each load.

## 5. The linker: the hardest and most honest part (`lineage/link/linker.py`)

**Problem:** a hard-coded label doesn't name its gate. Which one is it about? There are 489 candidates.

**Method:** score every gate with independent kinds of evidence ("families"), combine with a noisy-OR (`1 - product(1 - p)`), keep the top 3.

| Family | Signal |
|---|---|
| name | the gate's exact name appears in the section (0.9 if next to the words "feature gate", 0.5 otherwise) |
| links | section or page links to the same docs page as the gate's description (rarer link = stronger, capped so a link alone is never "confident") |
| text | IDF-weighted similarity between the section (heading counts 3x) and the gate's name + description |
| kep | the section resembles a KEP's title/summary, and that KEP introduces the gate (a two-hop path: section to KEP to gate) |

Rules that make it trustworthy:
1. **Confident (>= 0.8) needs two different families.** One kind of evidence alone caps at 0.79.
2. **A near-tie (top two within 0.1) is not confident for either**, and everything ranked below a demoted tie is capped too (a bug we found: a weaker third candidate used to become "confident").
3. **The claimed stage is never an input** (otherwise: "link by agreement, then flag disagreement" is circular). Only the claimed *version* is used, and only as a veto, see next.
4. **The since-version rule** (`link_detailed`, added today): a label saying "since v1.8" cannot describe a gate whose first stage starts at 1.15 (it didn't exist). Such candidates are **eliminated**, never boosted, and each removal is recorded as evidence.
   This is what resolves the seed: `LocalStorageCapacityIsolation` (first stage 1.7) versus its sibling `...FSQuotaMonitoring` (1.15). Measured: removed 4 wrong and 1 correct confident links; audited precision 88% to 94.5%. Known limit: a gate added long after its feature (IPVS) can be wrongly removed.

**How it was calibrated:** 265 labels whose gate *is* named in the shortcode act as a free answer key (hide the shortcode, ask the linker to recover the gate). Pages were split in two halves, parameters chosen on half A, reported on half B.
**Why that is not enough:** every such label has a gate, and 53% of their sections name it versus 16% of real hard-coded ones, so it flatters the linker. The honest number comes from reading the confident links on the real claims: **69 of 73 correct (94.5%)**, an AI-assisted audit that you must verify.

## 6. The detector (`lineage/detect/drift.py`)

`run_detector` takes each drift-eligible claim with a *confident* link and compares its stage with the gate's stage at the latest release (or its last known release if removed): `evaluate_claim`.

| Result | Meaning |
|---|---|
| `stale_stage` (**finding**) | label stage is not among the actual stages (overlap: either counts as correct) |
| `wrong_since_version` (minor) | stage right, "since" wrong |
| `documents_removed_feature` (review) | gate removed after alpha/deprecated: the feature was dropped; pages may mention that on purpose |
| `link_ambiguous` (review) | the top link disagrees but a runner-up fits exactly: maybe a linking error |
| `possible_drift` (review) | looks stale, but the link is not **anchored** |

**Anchoring is the most important policy.** Early on the detector reported 6 findings and only 2 were real: the rest came from wrong links (a label about the Endpoints API linked to the EndpointSlice gate, etc.). So a *finding* now requires an anchored link: the gate named next to "feature gate" in the section, a gate-lookup shortcode, or a **hand verification**.
Everything else is shown as "possible drift" for a human. That is why the seed is a finding: it has a hand verification. Without it the same (automatic, correct) link would be "possible drift". Widening anchoring to links resolved by elimination is an open decision.

Current real output: **2 findings (both real), 4 review items.** Recall is unknown: 82 claims are only "possible", 31 unresolved, and many labels describe features that never had a gate.

## 7. Trace, reviewers, ownership (`lineage/graph/trace.py`, `queries.py`, `hero.py`)

- `build_trace` collects the whole chain for one finding into an **evidence bundle** `{tool, params, evidence[], path[], status}`. The CLI, the API and the agent all use it, so they can't disagree. Run it: `uv run --env-file .env python -m lineage.hero resource-quotas`.
- **Ownership walk-up** (`Q_WALK_UP`): a variable-length Cypher match `(start)-[:CHILD_OF*0..]->(anc)` climbs the folder tree; `none(n IN nodes(p)[0..length(p)] WHERE n.no_parent_owners)` stops after a folder whose OWNERS says "no parent owners". `as_of` filters edges by `valid_from <= as_of < valid_to`: that one query is also **time travel**.
  Teams are resolved to people through `MEMBER_OF`. Example: the Job controller's approver was `alculquicondor` at 1.30 and is an emeritus approver by 1.37.
- **Reviewer suggestions** (`suggest_reviewers`): a person scores once per kind of role (docs approver 1.0, code approver 1.0, KEP approver 0.8, recent active reviewer 0.7, team lead 0.5). **These weights are our own heuristic**, say so. Shown in two groups: who can *merge* the docs fix, who can *check the facts*.
- **Official vs actual** (`experts_for`, `official_vs_actual`, `orphaned_components`): recency-weighted review activity (180-day half-life, approvals 1.5x) versus the OWNERS list. Excludes backports to release branches and repo-wide PRs, because reading real output showed release managers looking like storage experts.

## 8. The agent and the LLM layer (`lineage/agent/`)

- **Tools** (`ToolBox` in `tools.py`): 10 read-only tools (`drift_findings, explain_finding, gate_timeline, doc_claims, owners_of, what_changed, experts_for, orphaned_components, sig_leaders, decision_lineage`). Each runs parameterised Cypher and returns an evidence bundle where every item has a `source_url`.
- **Loop** (`run_agent` in `loop.py`, no framework): send question + tool schemas, run requested tools, renumber evidence ids so they're unique (`e1..eN`), repeat up to 6 times, then validate. **Enforced in code, not just in the prompt:** no evidence means a refusal that says what was searched; citations to unknown ids are stripped; an uncited answer is asked once to cite; the iteration cap returns "incomplete", not a guess.
- **LLM layer** (`llm.py`): plain HTTP to Gemini (primary) and Groq (fallback for chat); pacing, backoff on 429/5xx, fallback, provider logged per request. Model names come from `.env`, picked by *probing* (`scripts/pick_models.py`), not from memory.
- **Things only a real run revealed** (so mention them): Gemini 3 needs a "thought signature" echoed back with each tool call; models write citations as `[e1]`, `[e7, e8]` or `【e6】`; the free tier is tiny (20 requests/day on the flagship; ~1,000 embedded texts/day). **A free key cannot serve live demo traffic reliably.**

## 9. API and UI

`lineage/api/main.py` (FastAPI, Pydantic models): `/health /ask /findings /findings/{id} /findings/{id}/fix /entity /about-data /owners /changes /experts /orphans`. `/ask` returns 503 with the reason when no model is available; everything else works without any LLM.
`web/` (Vite + React + Cytoscape): **Findings** (trace, link evidence including what was ruled out, stage timeline, graph, reviewers, draft diff), **Explore** (who owned this folder in 1.30, compare releases, official vs actual, entity graphs), **Ask**, **About the data**.
The finding graph uses the *real* relations (not a chain of path items) and a left-to-right layout; Explore caps neighbours so you never see the whole graph (it is a hairball: 6,902 nodes and 30,263 edges).

## 10. Evaluation, honestly (`eval/`, read `eval/README.md`)

- **Drift** (`score_drift.py`): link audit 69/73 (AI-assisted), detection logic 1 TP + 7 TN on the controls (n=8, tests the logic, not accuracy), 2/2 reported findings real, recall unknown.
- **Q&A with a real LLM**, same model for both systems (`gemini-3.1-flash-lite`, Gemini only): **Lineage 94% (34/36) vs RAG 64% (23/36)**; multi-hop 83% vs 0%. **Caveats you must say out loud:** the 36 gold questions are drafts that were revised in three rounds after seeing results (86%, 92%, 94%);
  one run, one small model; the RAG baseline used TF-IDF, and the embedding-based variant is unfinished (quota). A fresh question set written by Abiram is the right fix.

## 11. Questions a judge will ask (with true answers)

1. **"Remove FalkorDB. Does it still work?"** No. Every tool, the detector, the trace and the API read the graph; only the offline linker scoring happens in Python (its results are stored as `ABOUT` edges, then used by traversals).
2. **"Where is the graph doing real work?"** Multi-hop (section to KEP to gate to team to people), the variable-length ownership walk with `no_parent_owners`, validity intervals for time travel, provenance on every edge. *We don't yet use `algo.*` or the vector index; don't claim it.*
3. **"How do you know a link is right?"** We don't, fully. Confident needs two evidence families, near-ties are rejected, impossible candidates are eliminated, only anchored links can be findings, and we audited 73 confident links (94.5%, AI-assisted).
4. **"Your demo case was hand-verified?"** The linker now picks it on its own (0.84, after ruling out the impossible sibling); the hand verification is a documented backup, and without it the system would call it "possible drift" by policy.
5. **"Is 94% vs 64% real?"** It's a real run on one model, but the gold set is a draft revised after early runs, so treat it as optimistic until a fresh held-out set is scored.
6. **"What did the AI do versus you?"** Claude Code wrote most code under your direction; you set the scope and rules (CLAUDE.md), made the decisions logged in DECISIONS.md, and verified the seed link. **Fill in your own contribution in the README; only you can.**
7. **"What would break at scale?"** Search API caps (1,000 results per query: handled by splitting), free-tier LLM quotas, and the sampled (not complete) ownership history.

## 12. Hands-on (about an hour): run these and look

```bash
docker compose up -d --build                                  # start everything (FalkorDB, loader, API)
```
```bash
uv run --env-file .env python -m lineage.hero resource-quotas # the whole hero trace; read it line by line against section 6-7
```
```bash
uv run --env-file .env python -m lineage.detect.drift         # findings, review items, counts
```
```bash
uv run --env-file .env python -m lineage.agent.cli "Why is the Resource Quotas label stale, and who should review a fix?"   # real Gemini; watch the tool calls and citations
```
```bash
uv run --env-file .env python -m lineage.graph.demo_queries --check   # then paste each query from docs/demo_queries.md into http://localhost:3000
```
```bash
cd web && npm run dev                                         # http://localhost:5173 : Findings -> the Resource Quotas item; Explore -> controller/job, as of 1.37, compare with 1.30
```
Then read, in this order: `lineage/ingest/gates.py` (small), `lineage/link/linker.py` (`link_detailed`), `lineage/detect/drift.py` (`evaluate_claim`, `judge_claim`), `lineage/graph/queries.py` (`Q_WALK_UP`), `lineage/agent/loop.py` (`run_agent`), `tests/test_linker.py` (tests are the best documentation of the rules).
Exercises: (a) change `ambiguity_gap` in `PARAMS` and rerun `python -m eval.score_links` to see the trade-off; (b) delete `snapshot/verified_links.jsonl` content, reload, and watch the seed become "possible drift"; (c) ask the agent something unanswerable and read how it refuses.

## 13. Glossary

**SIG** = a team. **KEP** = a design document. **OWNERS** = a file that says who may approve/review a folder. **Feature gate** = a named on/off switch with a maturity stage per release (alpha, beta, stable, deprecated; later removed). **feature-state label** = the badge on a docs page.
**Anchored link** = a link strong enough to support a finding (gate named in the section, or hand-verified). **Evidence bundle** = tool output with source links. **Sampled history** = ownership recorded at release tags, not every commit.
