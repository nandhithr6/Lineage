# Explain it: a plain-language walkthrough (read this before judging)

The hackathon rules say you must be able to explain the graph model, the queries, the architecture and your decisions. This page is written so you can. Read it once, then try to say each section out loud without looking. If a line does not make sense to you, ask before the video, not after.

## 1. What Lineage is, in two sentences

Kubernetes documentation has hand-typed badges ("Alpha since v1.8"). Lineage puts the docs, the feature history, the design decisions, the pull requests and the owners into one FalkorDB graph, follows the connections to check each badge against the verified history, and shows the proof.

## 2. The graph in plain words

A graph is **things** (nodes) and **relationships** between them (edges). Our things: docs pages and sections, feature gates (on/off switches with a stage), releases, design documents (KEPs), pull requests, people, teams, code folders. Our relationships: *a section HAS a claim; a claim is ABOUT a gate; a gate has a STAGE_IN each release; a KEP INTRODUCES a gate; a person is APPROVER_OF a folder.*

The hero answer is one **path** through it:
`docs section → claim ("alpha since v1.8") → feature gate → its stage in release 1.25 (stable) → the KEP that decided it → the team that owns it.`

Why a graph and not a table: the answer is a chain of relationships across five kinds of data. In a graph, following the chain *is* the query.

**Every edge records its proof**: `source_url` (a GitHub link pinned to a commit), how it was found (`extracted_by`), `confidence`, and when we learned it. Things that change over time (ownership) also have `valid_from` / `valid_to`, and we never delete, so we can ask "who owned this in release 1.30?".

## 3. Three queries, line by line

**a) Stage of a feature gate in a release**
```cypher
MATCH (g:FeatureGate {name: $g})-[e:STAGE_IN]->(r:Release {num: $n})
RETURN e.stage, e.source_url
```
Find the gate with this name, follow its `STAGE_IN` edge to the release with this number, return the stage and the link to the file that says so. `$g` and `$n` are parameters: user input is never pasted into the query text.

**b) Who owns a folder (and who did in 1.30)**
```cypher
MATCH (start:CodePath {id: $start})
MATCH p = (start)-[:CHILD_OF*0..]->(anc:CodePath)            // walk UP the folder tree, any number of steps
WHERE none(n IN nodes(p)[0..length(p)] WHERE n.no_parent_owners)  // stop at a folder that says "do not inherit"
WITH anc, length(p) AS hops
MATCH (o)-[r:APPROVER_OF|REVIEWER_OF|EMERITUS_APPROVER_OF]->(anc)
WHERE r.valid_from <= $as_of AND (r.valid_to IS NULL OR r.valid_to > $as_of)   // true at that release
RETURN ...
```
Kubernetes OWNERS files apply to a folder and everything below it, unless a folder says otherwise. So the owners of `pkg/controller/job` are the owners listed on it *and on its parents*. `CHILD_OF*0..` is a variable-length walk up the tree; the `valid_from/valid_to` filter turns it into time travel.

**c) Who do many authors depend on (a graph algorithm)**
```cypher
CALL algo.pageRank('Person', 'REVIEWED_BY') YIELD node, score
WITH node, score ORDER BY score DESC LIMIT $limit
RETURN node.handle, score
```
PageRank is the idea behind early Google: something important is something that important things point to. Here an edge goes from an author to the people who reviewed them, so a high score means many different authors rely on that reviewer. FalkorDB runs it **inside the database**. It measures reliance, not skill or ownership.

## 4. Why FalkorDB is central (the "decoration" test)

- Ownership, the trace, the drift detector, decision lineage and time travel are **traversals** in FalkorDB.
- Central reviewers use FalkorDB's **PageRank**; "how do these two things connect" uses its **shortest-path** algorithm; free-text search uses its **vector index**.
- Every agent tool is one fixed read-only query, so an answer's evidence is a query result, never model output.
- Remove FalkorDB and every page on the site stops working, because there is nothing left to ask.

## 5. Architecture in one breath

Public repos → **ingest** (parsers write small JSONL files, committed) → **link** (decides which gate a badge is about, with a confidence) → **loader** (writes everything to FalkorDB with `MERGE`, so it can be rerun safely) → **detector** (writes `Finding` nodes) → **FastAPI** → **React** UI, and the same tools as an **MCP** server. The agent is a hand-written loop (no framework) that picks from the fixed tools.

## 6. Decisions you should be able to justify

| Decision | Why |
|---|---|
| Hand-written agent, no LangChain/LlamaIndex | We wanted to explain every line and control provenance. |
| Never trust the model for facts | It chooses tools and phrases; a validator rejects any name, version or link not in the evidence rows. |
| A link is "confident" only with two independent kinds of evidence | A single text match produced false findings in our first run. |
| The "since-version" rule | A badge "since v1.8" cannot be about a gate whose first stage is 1.15; we rule it out, and only ever to remove candidates. |
| Ownership history sampled at release tags 1.30 to 1.37 and now | A deliberate scope choice, stated as a limit: `valid_from` means "first sampled release where seen". |
| Free hosting (Render) plus a one-command local setup | Docker Spaces were paid; the local setup is the guaranteed baseline. |
| Embedding signal for linking left off | Our human check found only 3 of its 4 new links correct. |
| Independent held-out questions, run once | Our own draft questions were revised after seeing runs, so they flatter the system. |

## 7. Questions a judge may ask, and honest short answers

- **Is the graph just decoration?** No: see section 4; every page is a query.
- **How do you know it does not hallucinate?** Evidence comes from query results; a validator rejects names not in the evidence; every claim cites an evidence id with a pinned link. It can still be *wrong about a link*, so uncertain links are shown as "possible", never as findings.
- **How good is it?** On questions written independently and run once: Lineage 8 of 15, plain retrieval 5 and 6 (model-judged, not fully human-checked); it answered two it should have refused. We then fixed the general gaps that exposed (a per-pull-request tool, an alias-members tool, renamed-KEP links and a few evidence and prompt rules) and re-ran the same questions: 14 of 15. That second number is a practice score on a test we had seen, so we report it separately. On our own draft questions 97%, but that set was revised after seeing runs.
- **What is weak?** Most badges name no gate, so most cannot be checked; recall is unknown; only three teams' pull requests are loaded; history is sampled; "chats" are only review events.
- **What did you do and what did the AI do?** The AI wrote most of the code under your direction; you set the scope, made the decisions logged in `DECISIONS.md`, checked links, findings and answers by hand, wrote the held-out questions and deployed it. (Say your own part in your own words.)
- **Why Track 1 as well as Track 3?** The same graph that proves a docs badge wrong lets an agent answer questions that need several hops by choosing tools: for example it followed a feature gate to its design decision, then asked the graph who is in an OWNERS alias (two tool calls, 27 ms and 8 ms), and answered with evidence ids. The tools are graph queries and algorithms (traversals, PageRank, shortest paths, vector search), they are served over MCP, and the agent's action is a drafted fix that a person reviews.
- **Where does the data come from?** Public Kubernetes repositories and the public GitHub API; emails removed; licenses in the README.
