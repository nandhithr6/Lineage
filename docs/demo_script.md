# Demo script (about 3 minutes), DRAFT

**On showing the graph.** The whole graph (6,902 nodes, 30,263 edges) is an impressive hairball for two seconds and unreadable after that. Use it once as the opening shot, then only focused slices: the UI's finding graph and Explore page, or the eight queries in [demo_queries.md](demo_queries.md) in the FalkorDB browser.

Items marked **[needs LLM key]** only work once `LLM_BASE_URL`, `LLM_API_KEY` and `LLM_MODEL` are set. Everything else works from the snapshot.
Rehearse once against the deployed or local stack and cut anything that fails; do not demo a step you have not run.

1. **The setup (15 s).** "We treated the Kubernetes project as a 3,000-person company: an org chart, ownership maps, design decisions, release history and
   documentation that quietly goes out of date. Lineage is a company brain that knows when its knowledge is stale and proves every answer."
   *Show:* the Findings page, then the **About the data** page briefly (public data, commit-pinned sources, licenses).
2. **The hero (60 s).** Open the **Resource Quotas** finding.
   - "The docs page says this feature is alpha since v1.8." Point at the claim and its permalink.
   - "Lineage links the label to a feature gate. Here a human verified the link, and the page says so." (be upfront about the manual link)
   - The gate timeline: alpha, beta, **stable 1.25**, removed. "So the label is stale."
   - The design decision (KEP 361), the owning team (sig-storage) and the graph path with its labelled edges.
3. **Repair (25 s).** Click **Draft the corrected label**: the one-line diff and the explanation. "Lineage only drafts; it never posts to Kubernetes."
   Then the reviewers in two groups: docs approvers who can merge, domain experts who can check the facts, each with the reason and source.
4. **Silence on correct labels (20 s).** "A good detector must not cry wolf." **[needs LLM key]** Ask: *Is the 'stable since v1.27' label in the CronJob time zones section correct?*
   Show the evidence chips. Without a key: open the findings list and note that the CronJob page is absent from it.
5. **Time travel and ownership (25 s).** Open **Explore**: pick `controller/job`, set *As of* 1.37 and *Compare with* 1.30. Red and green lines show who gained or lost a role (alculquicondor became an emeritus approver; mimowo and atiratree became approvers), each with a source link. No LLM key needed. Then open the `sig-storage` entity graph: a small, readable slice.
6. **Honesty (20 s).** Show the evaluation chart and say what it is: retrieval only, 36 draft questions, graph tools beat top-5 text retrieval most on multi-hop
   questions. Say what it is not: not an LLM accuracy result. Show the drift numbers: 2 confirmed findings, 8 items needing a human look, link precision 88% on an
   AI-assisted audit.
7. **Close (10 s).** "Remove FalkorDB and nothing works: every answer is a traversal, and every fact carries the source it came from."

Do not claim: PR or reviewer-activity expertise (not loaded), live deployment (unless it is), or recall (unknown).
