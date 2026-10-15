# CLAUDE.md — Lineage (Graph Hacks: FalkorDB, Track 3 Company Brain)

This file is the project brief for Claude Code. Read it fully before doing anything.
Team: Nan (main builder), Abiram (part-time: eval questions, frontend help, demo video, blog).

---

## 1. What we're building

**Pitch:** A company brain that knows when its knowledge is out of date, and proves every answer.

- **Company:** the Kubernetes open-source project, treated as a ~3,000-person company with a real org chart,
  ownership maps, design decisions, release history, work history, and documentation that drifts out of date.
- **Product:** Lineage. Every fact in the graph carries where it came from and when it was true.

### Hero workflow (the demo is built around this)
1. **Find** — a docs page labels a feature with a stage that is no longer true (user asks, or the watcher flags it).
2. **Trace** — follow the graph: DocPage → FeatureGate → stage per Release → KEP that decided it → implementing PRs → owners.
3. **Explain** — show the claimed stage vs. the verified stage, the exact sources, and the graph path, side by side.
4. **Repair** — draft the corrected doc text (with sources) and suggest reviewers (official owners + recent reviewers of that path).
   Lineage only drafts. It never posts to Kubernetes.

### Supporting capabilities
| Capability | Example question |
|---|---|
| Answers with proof | "Who should I ask about the job controller?" |
| Time travel | "Who owned this path in v1.30? What changed since?" |
| Ownership truth | "Who officially owns this vs. who actually reviews it?" |
| Drift detector | "Which docs claim beta for features that are already stable?" |
| Fix and route | "Draft the fix and tell me who should review it." |

Full plan doc (still says Go in places; **we switched to Python**):
https://claude.ai/code/artifact/867527a2-3c1c-4c9d-ad32-f90667044e79

---

## 2. Hackathon rules (non-negotiable)

- **Window:** Oct 15 12:01 AM IST → Oct 18 11:59 PM IST. **Target submission: Oct 18, 9 PM IST.** Feature freeze: Oct 18, 2 PM.
- **FalkorDB must be central.** "If the product still works once FalkorDB is taken out, the graph is decoration" → disqualified.
- **Data:** public, synthetic, or our own only. No private, login-protected, paywalled, or personal data. **Drop emails** from sigs.yaml.
- **AI assistants allowed, must be disclosed.** We must understand and be able to explain the code, graph model, queries,
  architecture, and decisions. Projects "entirely generated using AI without meaningful participant contribution,
  verification, or technical understanding may be rejected."
- **Submission must include:** public repo; README with setup + graph data model; the Cypher queries / graph algorithms
  the product depends on; demo video; live deployment (preferred) or complete local setup; tracks entered.
- **Tracks:** One project; submit to Track 3 + Track 1 (+ Track 2 only if memory ships); can win at most one track.

Sources: https://www.wemakedevs.org/hackathons/falkordb · /rules · /schedule · /resources

---

## 3. Stack (decided)

| Layer | Choice | Notes |
|---|---|---|
| Language | Python 3.11+ | |
| API | FastAPI + uvicorn | Pydantic models for every request/response |
| Graph + vectors | FalkorDB, official Python client `falkordb` | One graph DB for traversals, algorithms, and vector search |
| Agent | Hand-written tool-calling loop | No LangChain / LlamaIndex / Graphiti. We want full control of provenance and to explain every line |
| LLM | Provider TBD (whichever we have credits for), must support tool calling | Keep provider behind a thin interface |
| Embeddings | Same provider's embedding model | Embed docs sections + KEP summaries only (memory) |
| Parsing | `pyyaml`, `python-frontmatter`, `httpx` | gate files have YAML front matter |
| Tests | `pytest` | Parsers and query tools must have tests |
| Frontend | Vite + React + Cytoscape.js | Chat, evidence panel, highlighted graph path, findings list |
| Deploy | FalkorDB in Docker with persistent volume + FastAPI on one small server; frontend on Vercel | See §11 |

### FalkorDB facts we rely on (verified in docs)
- Local: `docker run -p 6379:6379 -p 3000:3000 -it --rm falkordb/falkordb:latest` → browser UI at http://localhost:3000.
- Python: `from falkordb import FalkorDB; db = FalkorDB(host="localhost", port=6379); g = db.select_graph("lineage")`,
  then `g.query(cypher, params)` for writes and `g.ro_query(cypher, params)` for reads.
- FalkorDB implements a **subset** of OpenCypher with extensions. Don't assume Neo4j-only syntax/procedures (no APOC).
  Check https://docs.falkordb.com/cypher/cypher-support and /cypher/known-limitations when unsure.
- Vector index: HNSW, cosine or euclidean, 1–4096 dims, on nodes or edges.
  `CREATE VECTOR INDEX FOR (d:DocSection) ON (d.embedding) OPTIONS {dimension: 1536, similarityFunction: 'cosine'}`
  Store with `vecf32([...])`. Query: `CALL db.idx.vector.queryNodes('DocSection', 'embedding', 10, vecf32($v)) YIELD node, score`.
  Vector queries don't combine well with property filters → search vectors first, then expand along edges.
- Algorithms via `CALL algo.<name>(...)`: BFS, SPpath, SSpath, PageRank, betweenness, harmonic centrality, WCC, CDLP, MSF, MaxFlow.
  Check exact signatures at https://docs.falkordb.com/algorithms/ before use.
- Many isolated graphs per instance (used later for per-user memory).
- **FalkorDB Cloud free tier is NOT for the live demo:** 100MB RAM, no persistence/backups, stopped after 1 day idle,
  deleted after 7 days. Fine for quick experiments only.

---

## 4. Data sources (all public)

| Source | Path | What it contains |
|---|---|---|
| kubernetes/community | `sigs.yaml` | Org chart |
| kubernetes/enhancements | `keps/<sig-dir>/<number>-<slug>/kep.yaml` (+ `README.md`) | Design decisions (KEPs) |
| kubernetes/kubernetes | `OWNERS` in many dirs; `OWNERS_ALIASES` at root | Code ownership |
| kubernetes/website | `content/en/docs/reference/command-line-tools-reference/feature-gates/<GateName>.md` | One file per feature gate, incl. removed ones |
| kubernetes/website | `content/en/docs/**/*.md` | Docs pages with `feature-state` labels |
| GitHub GraphQL API | — | PRs, reviews, linked issues (3 SIGs × ~2 years) |

### Cloning
```
git clone https://github.com/kubernetes/community.git
git clone https://github.com/kubernetes/enhancements.git
git clone --filter=blob:none https://github.com/kubernetes/kubernetes.git
git clone --filter=blob:none https://github.com/kubernetes/website.git
```
Blobless clones keep full commit history (needed for OWNERS history) but fetch old file contents on demand.
Raw clones live in `data/raw/` and are **git-ignored**. Record each repo's commit SHA at snapshot time.

### File formats (verified)

**sigs.yaml** — top-level `sigs:` list (other group types may also exist; inspect). Each SIG:
`dir`, `name`, `mission_statement`, `label`, `leadership: {chairs, tech_leads, emeritus_leads}` (each person:
`github`, `name`, `company`, `email` → drop email), `meetings`, `contact`,
`subprojects: [{name, description, owners: [raw.githubusercontent URLs to OWNERS files], leads}]`.
→ The subproject `owners` URLs are how a SIG connects to code paths.

**OWNERS** (YAML; spec: https://www.kubernetes.dev/docs/guide/owners/)
- Keys: `approvers`, `reviewers`, `emeritus_approvers`, `labels`, `options: {no_parent_owners: bool}`, `filters`.
- Applies to its directory and everything below. Parents also apply unless `no_parent_owners: true`.
- `filters`: map of Go regex (relative path) → simple config; if `filters` is used, owner keys must be inside it.
- Entries may be **aliases** → resolve through root `OWNERS_ALIASES` (`aliases: {name: [users]}`).
- Usernames are **case-insensitive** → lowercase them.
- Project's own definition of inactive: <10 contributions in a year; inactive approvers move to `emeritus_approvers`.

**kep.yaml** (template: keps/NNNN-kep-template/kep.yaml)
`title`, `kep-number`, `authors`, `owning-sig`, `participating-sigs`,
`status` (provisional | implementable | implemented | deferred | rejected | withdrawn | replaced),
`creation-date`, `reviewers`, `approvers`, `see-also`, `replaces`, `stage` (alpha|beta|stable),
`latest-milestone`, `milestone: {alpha, beta, stable}`, `feature-gates: [{name, components}]`, `disable-supported`, `metrics`.
Older KEPs may be missing fields or use variants → parse defensively, log gaps.

**Feature gate file** (front matter):
```yaml
title: LocalStorageCapacityIsolation
content_type: feature_gate
stages:
  - stage: alpha   # defaultValue: false, fromVersion: "1.7", toVersion: "1.9"
  - stage: beta    # fromVersion: "1.10", toVersion: "1.24"
  - stage: stable  # fromVersion: "1.25", toVersion: "1.26"
removed: true
```
Body = description, often with links to concept pages (useful for label-to-gate linking).

**feature-state shortcode** (docs: https://kubernetes.io/docs/contribute/style/hugo-shortcodes/)
- `{{< feature-state for_k8s_version="v1.10" state="beta" >}}` → renders "Beta since Kubernetes v1.10". **Hard-coded.**
- `{{< feature-state state="stable" >}}` → version defaults to the page/site version. Hard-coded state.
- `{{< feature-state feature_gate_name="NodeSwap" >}}` → looked up from the gate file. **Never stale.** Skip for drift.
- Valid states: alpha, beta, deprecated, stable.
- Current docs site = **v1.37**; older versions live at v1-36.docs.kubernetes.io etc.

**GitHub GraphQL limits:** 5,000 points/hour per user token; ≤100 concurrent requests; ≤2,000 points/min;
`first`/`last` must be 1–100; requests >10s time out (and cost extra points). Cache every response to disk.

---

## 5. Drift detection rules (get these exactly right)

1. `for_k8s_version` is the version the feature **entered** the stated stage — NOT the docs version.
2. A hard-coded label on the docs site for release R claims: (a) the feature is at stage S as of R, (b) it entered S at V.
3. Findings:
   - **Stale stage (main):** actual stage at R ≠ S, or the gate graduated and was removed.
   - **Wrong transition version (minor):** history shows the feature entered S at a version ≠ V.
     Noisy: a docs section may describe a sub-feature that arrived later than the gate (e.g. quota for ephemeral
     storage labelled v1.8 while the gate went alpha in 1.7). Report separately, lower severity.
   - **No finding:** both match. Correct labels must stay silent.
4. Hard-coded labels **don't name their gate.** The DocSection → FeatureGate link is inferred from:
   shared link targets (docs section and gate description link to the same page) + text similarity.
   Every link has `link_confidence`. Low confidence → show as "possible", never as a finding.
5. **Two-stage evaluation:** (a) was the label linked to the right gate? (b) given a verified link, is the stage wrong?
   A linking error must never masquerade as a drift finding.
6. Don't tune the detector to one example. Report counts: labels found, confidently linked, unresolved, findings.

---

## 6. Seed test cases

**Positive (verified by us on Oct 14):**
Resource Quotas page (`content/en/docs/concepts/policy/resource-quotas.md`), section "Quota for local ephemeral storage":
`{{< feature-state for_k8s_version="v1.8" state="alpha" >}}`.
Gate `LocalStorageCapacityIsolation`: alpha 1.7–1.9, beta 1.10–1.24, stable 1.25–1.26, removed.
→ Stale stage: should read stable since v1.25 (gate since removed / locked on).
Linking signal: the gate description and the Resource Quotas page both link to
`/docs/concepts/configuration/manage-resources-containers/` and both discuss local ephemeral storage.

**Negative controls (from GPT — NOT yet verified by us; check each gate file first):**
| Docs section | Label | Gate (claimed) |
|---|---|---|
| Namespaces – automatic labelling | stable v1.22 | NamespaceDefaultLabelName |
| CronJob – time zones | stable v1.27 | CronJobTimeZone |
| Pod Priority and Preemption | stable v1.14 | PodPriority |
| Pods – Pod OS | stable v1.25 | IdentifyPodOS |
| Persistent Volumes – raw block | stable v1.18 | BlockVolume |
| Persistent Volumes – snapshots | stable v1.20 | VolumeSnapshotDataSource |
| Security Context – fsGroupChangePolicy | stable v1.23 | ConfigurableFSGroupPolicy |
| Ingress API | stable v1.19 | (API GA, no single gate) |
| CRD version deprecation | stable v1.19 | (verify) |

**Ambiguous:** ServiceAccount token volume projection (label stable v1.20) vs. `BoundServiceAccountTokenVolume`
(stable v1.22). Probably related-but-different → must NOT be linked confidently.

Store every case in `eval/drift_cases.jsonl` with: page URL, source file, exact shortcode, matched gate (if verified),
stage history, expected outcome (positive/negative/ambiguous), link confidence, notes.

---

## 7. Graph model

**The schema is NOT final.** First inspect the data and trace the seed example by hand; then design.
Below are candidates and principles, not commitments.

### Principles
- Every edge carries: `valid_from`, `valid_to` (null = still true), `recorded_at` (when Lineage learned it),
  `source_url` (GitHub **permalink pinned to a commit**), `extracted_by` (`structured` | `llm` | `inferred`), `confidence`.
- Never delete facts. A change closes `valid_to` and creates a new edge (bitemporal: valid time + record time).
- Provenance comes from query results, never from the LLM.
- Add node/edge types only when a real query needs them.

### Candidate core nodes (Tier 1)
| Node | Key | Properties |
|---|---|---|
| `DocPage` | repo path | url, title |
| `DocSection` | page path + heading anchor | heading, text, links, embedding |
| `FeatureGate` | gate name | removed, description, links |
| `Release` | "1.25" | num (125, sortable), date |
| `KEP` | number | title, status, stage, owning_sig, summary, embedding |
| `Person` | lowercased GitHub handle | name |
| `SIG` | dir (e.g. sig-node) | name, label |
| `CodePath` | repo + dir | — |
| `PR` (Tier 2) | number | title, merged_at, url |

### Candidate edges
| Edge | From → To | Properties |
|---|---|---|
| `HAS_SECTION` | DocPage → DocSection | |
| `CLAIMS_STAGE` | DocSection → FeatureGate | claimed_stage, claimed_since, hardcoded, shortcode, link_confidence, link_method |
| `STAGE_IN` | FeatureGate → Release | stage, default_value (one edge per release in range, or per stage with from/to — decide) |
| `INTRODUCES` | KEP → FeatureGate | |
| `OWNED_BY` | KEP → SIG | |
| `REPLACES` / `SEE_ALSO` | KEP → KEP | |
| `AUTHORED` / `APPROVED_KEP` | Person → KEP | |
| `CHAIRS` / `TECH_LEAD_OF` / `EMERITUS_LEAD_OF` | Person → SIG | |
| `OWNS` | SIG → CodePath | via subproject OWNERS URLs |
| `APPROVER_OF` / `REVIEWER_OF` / `EMERITUS_APPROVER_OF` | Person → CodePath | from OWNERS + git history |
| `AUTHORED` / `REVIEWED` / `APPROVED` (Tier 2) | Person → PR | |
| `TOUCHES` (Tier 2) | PR → CodePath | |
| `IMPLEMENTS` (Tier 2) | PR → KEP | |
| `DOCUMENTED_BY` | DocPage → CodePath? | for routing doc fixes to website OWNERS (decide) |

Open design questions to settle after inspection:
- STAGE_IN as one edge per stage (with from/to) vs. one per release (simpler "as of" queries).
- How to model OWNERS inheritance: materialize effective owners per path, or compute by walking up.
- Doc fix reviewers: kubernetes/website OWNERS for the docs path + owners/experts of the feature's SIG.

### Example queries (illustrative; check FalkorDB Cypher support)
```cypher
// Stage of a gate as of a release
MATCH (g:FeatureGate {name: $gate})-[s:STAGE_IN]->(r:Release)
WHERE r.num = $release_num
RETURN s.stage, s.source_url

// Owners of a path as of a date
MATCH (p:Person)-[o:APPROVER_OF]->(c:CodePath {path: $path})
WHERE o.valid_from <= $as_of AND (o.valid_to IS NULL OR o.valid_to > $as_of)
RETURN p.handle, o.source_url

// Stale-stage candidates (confident links only)
MATCH (s:DocSection)-[c:CLAIMS_STAGE]->(g:FeatureGate)-[st:STAGE_IN]->(r:Release {num: $current})
WHERE c.hardcoded AND c.link_confidence >= $min_conf AND c.claimed_stage <> st.stage
RETURN s, c, g, st
```

---

## 8. Agent design

### Loop (hand-written)
1. Send system prompt + conversation + tool schemas to the LLM.
2. If it returns tool calls → run each tool (parameterized Cypher, read-only) → append results.
3. Repeat until it returns a final answer (cap iterations, e.g. 6).
4. Final answer must reference evidence IDs from tool results.

### Evidence bundle (every tool returns this shape)
```json
{
  "tool": "gate_timeline",
  "params": {"gate": "LocalStorageCapacityIsolation"},
  "evidence": [
    {"id": "e1", "kind": "edge", "type": "STAGE_IN",
     "from": "FeatureGate:LocalStorageCapacityIsolation", "to": "Release:1.25",
     "props": {"stage": "stable"}, "source_url": "https://github.com/kubernetes/website/blob/<sha>/...",
     "valid_from": "...", "valid_to": null}
  ],
  "path": ["DocSection:...", "FeatureGate:...", "Release:1.25"],
  "status": "ok"
}
```
`status` ∈ `ok` | `empty` | `insufficient_evidence`. The UI highlights `path` and links every `source_url`.

### System prompt rules
- Only state facts present in tool evidence; cite evidence IDs per claim.
- If evidence is missing or conflicting → say "insufficient evidence" and show what was found.
- Never invent people, versions, URLs, or stages.
- Translate Kubernetes jargon for the user (SIG = team, KEP = design decision, OWNERS = ownership map).

### Tools (curated, parameterized Cypher)
| Tool | Tier | Returns |
|---|---|---|
| `drift_findings(sig=None, min_conf=0.8)` | 1 | Stale-stage findings with evidence |
| `explain_finding(finding_id)` | 1 | Full trace: claim, gate history, KEP, sources |
| `gate_timeline(gate)` | 1 | Stage history per release |
| `doc_claims(page_or_section)` | 1 | Labels on a page and their linked gates + confidence |
| `owners_of(path, as_of=None)` | 1 | Approvers/reviewers (with inheritance) |
| `decision_lineage(kep_or_gate)` | 1–2 | KEP, replaced KEPs, SIG, approvers, PRs |
| `suggest_reviewers(path_or_finding)` | 2 | Owners + active reviewers, minus inactive |
| `experts_for(path)` | 2 | Recency-weighted review counts |
| `what_changed(entity, from_rel, to_rel)` | 2 | Edges opened/closed between releases |
| `orphaned_components(months=12)` | 2 | Paths whose owners are all inactive |
| `search_text(query)` | 2–3 | Vector search → expand along edges |
| `cypher_fallback(question)` | 3 | Read-only, schema-validated generated Cypher |

Draft fix (`draft_doc_fix(finding_id)`): LLM rewrites only the shortcode line / sentence, given the evidence;
output is a unified diff + the evidence list + suggested reviewers.

---

## 9. API (FastAPI)

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | DB connectivity |
| POST | `/ask` | `{question, session_id?}` → answer + evidence + path |
| GET | `/findings` | List drift findings (filters: sig, kind, min_conf) |
| GET | `/findings/{id}` | Full trace for one finding |
| POST | `/findings/{id}/fix` | Draft fix diff + reviewers |
| GET | `/entity/{label}/{key}` | Node + neighbors (for the graph view) |
| GET | `/about-data` | Sources, commit SHAs, snapshot date, counts |

---

## 10. Repo layout (proposed)

```
lineage/
├── CLAUDE.md  DECISIONS.md  README.md  .env.example  docker-compose.yml
├── data/raw/            # git-ignored clones + GitHub API cache
├── snapshot/            # normalized JSONL committed to git (small)
│   ├── manifest.json    # repo SHAs, snapshot date, counts
│   └── *.jsonl
├── lineage/
│   ├── ingest/          # sigs.py, owners.py, keps.py, gates.py, docs_labels.py, github_prs.py
│   ├── link/            # label → gate linking + confidence
│   ├── graph/           # schema.py (indexes/constraints), loader.py, queries.py
│   ├── agent/           # loop.py, tools.py, prompts.py, llm.py (provider wrapper)
│   ├── detect/          # drift.py, ownership.py
│   └── api/             # main.py (FastAPI), models.py
├── eval/                # drift_cases.jsonl, gold_questions.jsonl, baseline_rag.py, score.py
├── tests/               # parser + query tests (pytest)
└── web/                 # Vite + React + Cytoscape.js
```

Pipeline: `ingest` (raw → snapshot JSONL) → `link` (adds CLAIMS_STAGE with confidence) → `graph/loader`
(snapshot → FalkorDB) → `detect` → `api`. Each stage runnable alone: `python -m lineage.ingest.gates` etc.
Loader must be **idempotent** (MERGE, not CREATE) so it can be rerun safely.

---

## 11. Deployment

- One small server runs `docker-compose`: FalkorDB (persistent volume) + FastAPI. Frontend on Vercel.
  Check https://docs.falkordb.com/operations/docker for the correct persistence volume path/options.
- On deploy: run the loader against the snapshot → graph rebuilt in one command. Crash = one-command recovery.
- Config via env vars: `FALKORDB_HOST`, `FALKORDB_PORT`, `FALKORDB_PASSWORD`, `LLM_API_KEY`, `LLM_MODEL`,
  `EMBED_MODEL`, `GITHUB_TOKEN`. Never commit secrets; keep `.env.example` updated.
- Set a FalkorDB password in production.
- Deploy a rough version by end of Oct 16. Keep it running after Oct 18 (judges may open it days later).

---

## 12. Evaluation

**A. Drift detector (two-stage):** on `eval/drift_cases.jsonl`
- Linking: precision/recall of label → gate links; count unresolved.
- Detection (on verified links): true/false positives and negatives; correct labels must stay silent.

**B. Q&A:** 30 hand-verified questions (6 per category: ownership, expertise, time travel, decision lineage,
drift, multi-hop) in `eval/gold_questions.jsonl`, each with the verifying source.
- Systems: vector-RAG baseline (same docs/text, same LLM, no graph) vs. Lineage.
- Metrics: answer accuracy, source correctness, refusal quality on unanswerable questions, median latency.
- Report honestly, whatever the numbers are. Chart goes in README and demo.

---

## 13. Build tiers and timeline

| Tier | Contents | Done when |
|---|---|---|
| **1 Core** | Ingest gates, docs labels, sigs.yaml, OWNERS, kep.yaml → snapshot → graph; label linking with confidence; drift detector; Tier-1 tools; hero workflow via CLI/API | Resource Quotas finding found, traced, explained, fix drafted, with sources |
| **2 Complete** | PR/review ingestion (3 SIGs); expertise + reviewer routing; time travel; graph-path UI; eval; deployment | Deployed, demo flow works, eval chart in README |
| **3 Beyond** | Per-user memory + multi-agent (Track 2), MCP server, hybrid search, more SIGs, algorithms, cypher fallback | Only if 1–2 stay green |

| Day | Goal | Checkpoint |
|---|---|---|
| Oct 15 | Clone, inspect, data inventory, trace seed by hand, schema, first loaders (gates + docs labels), start GitHub fetch | CLI shows the Resource Quotas finding with its graph path |
| Oct 16 | sigs.yaml/OWNERS/KEP ingestion, OWNERS history, agent loop + Tier-1 tools, API, minimal UI | Rough version deployed end to end |
| Oct 17 | Reviewer routing, expertise, time travel, draft fix, eval, UI polish; Tier 3 only if green | Full demo flow works on deployed version |
| Oct 18 | Freeze 2 PM; README, schema diagram, queries, AI disclosure; video by 6 PM; blog; submit by 9 PM | Submitted |

Never cut: provenance, time-aware edges, drift detector, eval chart.
GitHub PR fetch: start ASAP (rate limits make it slow); scope = sig-node, sig-apps, sig-scheduling (consider sig-storage,
since the demo case is storage-related), last ~2 years.

---

## 14. Demo script (~3 min)
1. "Treat Kubernetes as a 3,000-person company." Show the graph briefly.
2. Hero: Resource Quotas says alpha → Lineage traces gate history → stable since v1.25 → path highlighted, sources clickable.
3. Draft fix diff + suggested reviewers with reasons.
4. Show a correct label staying silent (no false positive).
5. Time travel / ownership truth quick question.
6. Eval chart: Lineage vs. vector RAG. Close: "Remove FalkorDB and nothing works — every answer is a traversal."

---

## 15. Glossary (translate for judges)
- **SIG** = team. **KEP** = design decision document. **OWNERS** = ownership map for a directory.
- **Feature gate** = on/off switch for a feature, with a maturity stage (alpha → beta → stable/GA, then removed).
- **feature-state label** = the "Beta since vX" badge on a docs page.

---

## 16. How to work with Nan (important)

- **Teach while building.** Before writing a module, explain the concept in a few sentences (e.g. what a Cypher MERGE does,
  why bitemporal edges, how OWNERS inheritance works). After writing, summarize what the code does and why.
- **Small steps.** One module at a time, runnable and tested before moving on. No giant multi-file dumps.
- **Never invent data, sources, versions, or API behavior.** If unsure about FalkorDB or Kubernetes specifics, check the
  real files / docs first and say what you checked.
- **Be direct.** Flag problems, risks, and bad ideas honestly; keep plans minimal and concrete.
- **Ask before** big refactors, schema changes, adding dependencies, or deleting files.
- **Log decisions:** append one line per technical choice to `DECISIONS.md` (`date — decision — why`).
  This feeds the README, blog post, and AI-use disclosure.
- Keep commits small with clear messages. Raw data never goes in git.
- Python style: type hints, small pure functions for parsing, `pathlib`, logging (not print) in pipeline code.

---

## 17. Immediate next steps (start here)
1. Clone the four repos into `data/raw/` (blobless for kubernetes and website). Record SHAs.
2. Count labels: `grep -rn "feature-state" content/en/docs` in website; split into hard-coded vs. `feature_gate_name`.
3. Count gate files; check how many have `removed: true`.
4. Write a short data inventory (`docs/data_inventory.md`): entities, connecting identifiers, source of truth, gaps.
5. Trace the Resource Quotas example by hand end to end (claim → gate → history → KEP → SIG → owners → website OWNERS).
6. Verify GPT's negative controls against their gate files; fill `eval/drift_cases.jsonl`.
7. Then design the schema together, log it in DECISIONS.md, and write the first loader (gates + docs labels).
