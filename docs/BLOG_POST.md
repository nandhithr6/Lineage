<!--
DRAFT for the hackathon's "best blog post" side prize. Written by Claude from the project's real numbers; the team must read it, change anything that is not true to them, and publish it (dev.to, Hashnode, Medium, anywhere public).
Before publishing: put in the real repo link, live link and video link; add 2 or 3 screenshots from docs/img/.
-->

# A company brain that knows when it is wrong: Lineage, FalkorDB and the Kubernetes docs

Kubernetes documentation has little badges: *Alpha since v1.8*. They are typed by hand. When a feature graduates, someone has to remember to change the badge, and often nobody does. A reader then trusts a label that stopped being true years ago, and nobody is told.

For the FalkorDB Graph Hacks (Track 3, Company Brain) we built **Lineage**, a company brain that notices when its knowledge is out of date and proves every answer. We treated the Kubernetes open-source project as a company of about 3,000 people: it has an org chart, ownership maps, design documents, release history and documentation that drifts. All of it is public.

**Live demo:** *(link)* · **Code:** *(link)* · **Video:** *(link)*

## Why this is a graph problem

To say a badge is wrong, you have to follow a chain: the docs section makes a claim; the claim is about a feature gate (an on/off switch with a maturity stage); the gate has a stage in every release; a design document (a KEP) decided it; pull requests implemented it; a team owns it. No single document holds the answer. It is a path through relationships, which is what a graph database is for.

Everything lives in one FalkorDB graph: about 6,900 nodes and 30,000 relationships (489 feature gates, 648 design documents, 453 docs labels, 3,074 pull requests, 1,225 people). Each relationship carries where it came from (a link pinned to the exact commit), how it was found and how sure we are. Facts that change, such as who owns a folder, also carry the time they were true, and nothing is deleted. That is how you can ask "who owned this folder in release 1.30?"

## The hero example

The Resource Quotas page says a section about local ephemeral storage is *Alpha since v1.8*. Lineage follows the graph and shows what is verified: the feature reached stable in 1.25, and the gate has since been removed. The page and the verified history sit side by side, and every line links to its source.

The interesting part is that a hard-coded badge never names its feature. Lineage has to work out which gate it means, from shared links and text similarity, and it gives every link a confidence. One rule did a lot of work: a badge that says "since v1.8" cannot be about a gate whose first stage starts in 1.15, because that gate did not exist yet. Lineage rules such gates out and keeps the evidence for why. Links it is not sure of are shown as "possible drift" for a person to check, never as findings.

## Every answer is a traversal, and checked

Our agent is a hand-written loop with no framework. It picks from a small set of fixed, read-only queries. The model is never trusted to know facts, so four checks surround it:

1. Names are resolved against the graph first. A gate or release the graph does not know stops the lookup instead of being guessed.
2. Every answer has one of four outcomes, including "unknown entity" and "not in the graph".
3. A validator rejects any gate, person, version, KEP, path or link in the draft that is not in the evidence rows.
4. Each answer shows a trace of the queries it ran, and on a finding you can replay the path from badge to proof step by step.

FalkorDB does the heavy lifting inside the database: variable-length walks up the folder tree for ownership, PageRank over the review network to find who many authors depend on, shortest paths to show how two things connect, and an HNSW vector index for text search. The same tools are exposed as an MCP server, so any AI client can ask Lineage for evidence.

## What we measured, honestly

We wrote 36 draft questions and compared Lineage with plain retrieval (same model, same text, no graph): Lineage got 97%, retrieval 64%. But we revised that draft set after seeing runs, so we did not trust it.

We then ran **15 questions written independently by the team, once**. Lineage got **8 of 15 right; plain keyword retrieval 5 and vector retrieval 6**, all with the same model. The answers were judged by a model against free-text gold answers (not fully human-checked yet). Lineage's misses taught us something real: it answered two questions it should have refused, it said a label looked up from the gate file could go out of date, and two of its misses were gaps in our tools (no per-pull-request lookup, and a missed see-also link). We did not change the system afterwards, so the number describes the system we shipped.

On the link between badges and gates, an AI-assisted audit found 69 of the 73 confident links correct; a person re-checked 21 of them and found 20 correct and 1 wrong. We tried an embedding signal to link more badges, and when we checked its four new links by hand only three were right, so we left it off.

## What we would do next

Ingest the review and issue discussions as text, not only as review events (the "chats" part of a company brain). Cover more teams than the three we loaded. Add the missing per-pull-request and see-also tools. And have people verify every judged answer, not a sample.

## Built with AI, and what that means

We built this with an AI coding assistant (Claude Code) and say so plainly in the repository, including the mistakes it made and how we caught them (every technical choice is a one-line entry in `DECISIONS.md`). The hackathon asks that you can explain what you built: we can walk through the graph model, any query in the README, and why FalkorDB is central, which is the real test of whether the graph is the product or decoration. Take FalkorDB away and every page on the site stops working.
