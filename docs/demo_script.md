# Demo script (about 3 minutes)

Rehearse once against the deployed stack and cut anything that fails; do not demo a step you have not run. Every step below works with NO language-model key (the six Ask questions
and the two refusal questions have saved answers with their evidence); only a question you type yourself needs a key.

1. **Setup (15 s).** "We treated the Kubernetes project as a 3,000-person company: an org chart, ownership maps, design decisions, release history and documentation that quietly goes out of date.
   Lineage is a company brain that knows when its knowledge is stale and proves every answer." *Show:* the Findings page (2 findings, 4 review items), then About the data (public data, commit-pinned sources, snapshot age).
2. **Hero (60 s).** Open **Resource Quotas**. Side by side: "the docs say alpha since v1.8" / "the gate's verified history: stable since 1.25, now removed". Point at the pinned links on both sides.
   "Which gate is this about? Nobody told Lineage. The linker found it by itself (84%), and it ruled out the sibling gate that did not exist yet in v1.8; that rule is why a link without the gate's name in the text is still trusted." Then the design decision (KEP 361), the team and the graph path.
   Open **Volume Populators** for the full chain: finding -> gate -> design decision -> **the pull request that promoted it to GA (#129770)** with its method and confidence.
3. **Repair (25 s).** *Draft the corrected label*: a one-line diff. "Lineage only drafts; it never posts to Kubernetes." Then the reviewers, each with the reason and source, and for the domain experts **how they connect to the gate**
   (a path computed inside FalkorDB, e.g. tech lead of sig-storage <- owns design decision 361 -> the gate).
4. **Proof vs plain retrieval (30 s).** Ask page: tick **Answer with plain retrieval too**, click "Why is the Resource Quotas page stale?". Two columns: the graph answer with its evidence chips and trace table (tool, arguments, ms, rows, outcome),
   and the plain-retrieval answer from the same text and the same model, with the five passages it saw. "Retrieval can find similar text; it cannot follow a relationship, and nothing checks its answer."
5. **It knows what it does not know (25 s).** Click "What is the stage of the LocalStorageCapacityIsolatio gate?": outcome **Unknown entity**, "did you mean LocalStorageCapacityIsolation", no query ran.
   Click "Who will approve pkg/controller/job in release 1.45?": unknown entity again, because that release does not exist in the graph. "`Not in the graph` is a different outcome: the names exist but nothing connects them, which is not the same as nothing being affected."
   If asked about the validator: it rejects any gate, person, version, KEP number, path or link in an answer that is not in the evidence rows and falls back to the rows themselves.
6. **Time and ownership (20 s).** Explore: `controller/job`, as of 1.37 compared with 1.30 (who gained or lost a role, each with a source). Then **Insights**: folders whose listed approvers went quiet while others kept working (`mount-utils`), and the central reviewers from FalkorDB PageRank.
7. **Keeping knowledge current (15 s).** **What changed**: compare with the snapshot of a year ago: the two findings are still open (open since at least 2025-10-16), one section moved (not a fix), 147 feature gates changed stage.
8. **Memory (optional, 10 s; needs a key unless you saved a note first).** Ask a graph question, click *Remember this answer*, ask about the same gate again: the note appears first with `answered_from_memory (learned <date>)` and "re-verified against the graph just now: unchanged".
9. **Honesty and reach (15 s).** The evaluation chart and what it is not (36 draft questions, one run); the held-out numbers with their caveats. "Any MCP client can use the same eleven read-only tools and gets the same evidence ids." Close: "Remove FalkorDB and nothing works: every answer is a traversal, and every fact carries its source."

Do not claim: recall (unknown), a live deployment unless it is one, or that the retrieval baseline is weak in general (it is the same model on the same text; it lacks the graph and the checks).
