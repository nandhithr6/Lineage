# Graph schema proposal (DRAFT, not decided)

Built from what the data actually contains (see `data_inventory.md`). Each choice has a reason and an open question.

## Tier 1 nodes

| Node | Key | Source | Notes |
|---|---|---|---|
| `Release` | `num` (1.25 -> 125) | gate stage ranges, `data/releases` in website | sortable int; date from website `data/releases` (verify) |
| `FeatureGate` | `name` | gate .md files (490) | `removed`, `description`, `links[]` |
| `DocPage` | repo path | website | `url`, `title` |
| `DocSection` | path + heading anchor | website | one per hard-coded label (190), `text`, `links[]`, `embedding` later |
| `KEP` | `kep-number` | enhancements (648) | `title`, normalised `status`, `raw_status`, `stage`, `latest_milestone` |
| `SIG` | `dir` | sigs.yaml (24) | also working groups / committees as `group_type` property, not separate labels |
| `Person` | lowercased handle | sigs.yaml, kep.yaml, OWNERS | `name` optional. No emails |
| `CodePath` | `repo` + `path` | OWNERS locations | a directory; has a `PARENT` edge to its parent dir |
| `Team` (alias) | alias name | OWNERS_ALIASES | e.g. `sig-docs-en-owners`; has `MEMBER_OF` from Person |

## Edges

Every edge: `valid_from`, `valid_to` (null = still true), `recorded_at`, `source_url` (commit-pinned permalink),
`extracted_by` (structured | llm | inferred), `confidence`.

| Edge | From -> To | Notes |
|---|---|---|
| `HAS_SECTION` | DocPage -> DocSection | |
| `CLAIMS_STAGE` | DocSection -> FeatureGate | `claimed_stage`, `claimed_since`, `shortcode`, `link_method`, `link_confidence`; written only by the linker |
| `STAGE_IN` | FeatureGate -> Release | see decision 1 |
| `INTRODUCES` | KEP -> FeatureGate | `link_method`: `kep_yaml` (0.95) / `readme_mention` (lower) / `title_match` (lowest). Seed gate needs `readme_mention` |
| `OWNED_BY` | KEP -> SIG | `participating` as a second edge type `PARTICIPATES` |
| `REPLACES`, `SUPERSEDED_BY`, `SEE_ALSO` | KEP -> KEP | `superseded-by` is in 52 KEPs; CLAUDE.md lacks it |
| `AUTHORED`, `APPROVED_KEP` | Person -> KEP | |
| `CHAIRS`, `TECH_LEAD_OF` | Person -> SIG | emeritus: to verify across all SIGs |
| `PARENT` | CodePath -> CodePath | enables inheritance by traversal |
| `OWNS` | SIG -> CodePath | from the 529 subproject OWNERS URLs |
| `APPROVER_OF`, `REVIEWER_OF`, `EMERITUS_APPROVER_OF` | Person or Team -> CodePath | declared at the dir holding the OWNERS file |
| `MEMBER_OF` | Person -> Team | from OWNERS_ALIASES |

Deferred to Tier 2: `PR`, `AUTHORED`/`REVIEWED`/`APPROVED`, `TOUCHES`, `IMPLEMENTS`.

## Decisions I need from you

1. **STAGE_IN shape.** Recommend **one edge per release** (derived from the from/to ranges). ~490 gates x a handful of releases
   is only a few thousand edges, and "stage as of release R" becomes a plain equality match instead of range logic.
   Cost: derived edges, so `source_url` points at the gate file line that holds the range.
   Open: what a stage with no `toVersion` means (needs a live gate to confirm), and how to mark the removal release.
2. **OWNERS inheritance.** Recommend **compute by traversal**: store each OWNERS file at its own directory, link directories with
   `PARENT`, and walk up with a variable-length match until `no_parent_owners: true` stops it. This keeps the answer a graph
   traversal (FalkorDB central) and stays faithful to how OWNERS really works. Caveat: `filters` regex OWNERS need path-level handling
   and are the hard part. Check FalkorDB variable-length path support before committing.
3. **OWNERS history ("time travel").** Recommend snapshotting at **release tags** (valid_from = release date), not every commit.
   Per-commit history of ~thousands of OWNERS files is expensive and rarely needed. Needs the kubernetes clone to finish.
4. **Docs reviewers.** Website OWNERS point at team aliases. Model `Team` nodes so "who reviews a docs fix" is
   `Team -> CodePath(content/en)` plus the feature SIG's owners.
5. **Linker output.** Keep unlinked / low-confidence labels as `DocSection` nodes with no `CLAIMS_STAGE` edge (or with confidence
   below threshold) so counts "labels found / confidently linked / unresolved" come straight from Cypher.

## Not yet verifiable
- FalkorDB variable-length paths, `valid_to IS NULL` handling and vector index syntax: confirm against the docs when Docker is up.
- kubernetes/kubernetes OWNERS counts: clone still in progress.
