# Data inventory (snapshot taken 2026-10-15)

Raw clones live in `data/raw/` (git-ignored). Cloned with the global git config bypassed
(`GIT_CONFIG_GLOBAL=/dev/null`) because `~/.gitconfig` rewrites https GitHub URLs to SSH.

| Repo | Clone | HEAD SHA | Last commit |
|---|---|---|---|
| community | full | 53c338ecba4a2a9240f6654cca1d6a9dac25c785 | 2026-10-14T05:42:55Z |
| enhancements | full | 6ebe0a94771723b3f27cb4a757f0cc22f29e38a2 | 2026-10-14T16:14:09Z |
| website | blobless | e91eff3b59c0bf813193202a4f193d37beb7efd7 | 2026-10-15T06:28:18Z |
| kubernetes | blobless | cbfc0564b409b82c6ec0cf76e2d15860dd13b31b | 2026-10-15T05:24:10Z |

## Counts (all measured, not estimated)

**Feature gates** (`website/.../feature-gates/*.md`): 490 files, of which `index.md` is an overview page, so **489 gates**.
`removed`: true on 232, explicitly false on 2, absent on 256. (Exact, from `lineage.ingest.gates`.)
Each has YAML front matter `stages: [{stage, defaultValue, fromVersion, toVersion?, locked?}]`.
Source-data oddities (kept, flagged as `issues`, never silently fixed): a `deprecated` stage exists; a stage can repeat with a
different `defaultValue`; `removed: true` does not give the removal release (e.g. DynamicProvisioningScheduling); 4 gates have an
open-ended stage followed by another stage (end inferred as the release before); 6 gates list two different stages for one release
(stage change inside a release, e.g. PodHostIPs beta to 1.30 and stable from 1.30) and some versions are patch-level ("1.35.4").
Derived: 3,999 (gate, release, stage) rows, 12 of them flagged `overlap`. Current release 1.37 (released 2026-08-26, data/releases/schedule.yaml).

**feature-state labels** (exact, from `lineage.ingest.docs_labels`): **453 real labels on 207 pages**.
- 265 use `feature_gate_name=` (look up the gate, never stale, skipped for drift)
- 188 hard-coded (the drift candidates): stable 136, beta 29, deprecated 12, alpha 11. 3 have no `for_k8s_version`.
- The earlier line-grep total of 457 was wrong: it counted 3 Hugo-escaped examples (`{{</* ... */>}}`) on the shortcode style guide
  page, and the extra form was a spacing variant. Shortcodes inside code fences are also skipped.
- Section anchors are an approximation of Hugo's heading ids; check before using them in links.

**sigs.yaml**: top-level groups `sigs` (24), `workinggroups` (10), `committees` (3), `gb_rep` (4), `usergroups` (0).
SIG keys: dir, name, mission_statement, charter_link, label, leadership{chairs, tech_leads}, meetings, contact, subprojects.
529 subproject `owners:` URLs (OWNERS files, many in other repos like kubernetes/cel-admission-webhook).
Note: `emeritus_leads` was NOT present on the first SIG; check across all SIGs.

**KEPs**: 648 `kep.yaml` files (2 fail to parse). 391 name at least one feature gate.
- `status` is messy: implemented 295, implementable 275, provisional 51, withdrawn 11, replaced 5, rejected 3,
  deferred 1, plus typos/variants (`imlpemented`, `implemented (alpha)`, `removed`, `superseded`, a raw template string).
  The loader must normalise and log these.
- Field coverage: title/kep-number/authors/owning-sig/status/approvers ~646; stage 608; milestone 510;
  see-also 406; replaces 254; superseded-by 52 (not in CLAUDE.md, worth modelling).

## Linking findings

- Gates named in KEPs: 449 distinct. Overlap with the 490 gate files: **336**. 154 gates have no KEP via kep.yaml;
  113 KEP gate names have no gate file (renamed, other component, or typo). 13 gates map to more than one KEP.
- **Seed gate `LocalStorageCapacityIsolation` is NOT in any kep.yaml `feature-gates`.** Its KEP is
  `sig-storage/361-local-ephemeral-storage-isolation`; the gate name appears only in that KEP's README (and in sig-node KEP 1029).
  So KEP->gate needs more than one method (kep.yaml field, README mention) with different confidence.
- Negative controls checked against gate files (stage and from-version match the claimed label):
  NamespaceDefaultLabelName stable 1.22, CronJobTimeZone stable 1.27, PodPriority stable 1.14, IdentifyPodOS stable 1.25,
  BlockVolume stable 1.18, VolumeSnapshotDataSource stable 1.20, ConfigurableFSGroupPolicy stable 1.23. All confirmed.
  Ambiguous pair confirmed different gates: BoundServiceAccountTokenVolume stable 1.22 (label said 1.20).
  Not yet checked: Ingress API and CRD version deprecation (no single gate).
- Seed label confirmed: `resource-quotas.md:194` `{{< feature-state for_k8s_version="v1.8" state="alpha" >}}`.
  The same page has other labels (line 528 stable v1.24, 583 stable v1.17, 765 uses feature_gate_name).

## Code ownership (kubernetes/kubernetes)
586 OWNERS files; 8 use `filters:`; 58 set `no_parent_owners: true`. Root `OWNERS_ALIASES` exists (aliases like
`sig-api-machinery-approvers` -> user lists). Latest tag seen: v1.38.0-alpha.2 (docs site is v1.37).

## Docs ownership (for routing fixes)
- `website/content/en/OWNERS`: reviewers `sig-docs-en-reviews`, approvers `sig-docs-en-owners` + `sig-docs-website-owners`,
  `no_parent_owners: true`. These are **aliases** resolved via `website/OWNERS_ALIASES`.
- `website/OWNERS` has `emeritus_approvers` with commented-out entries (comments are meaningful: disabled assignments).
- There is no OWNERS under `content/en/docs/concepts/policy/`; inheritance up to `content/en/` applies.

## KEPs, SIGs (parsed, 2026-10-15)
648 KEPs (all parsed; 2 had invalid dates, now read as strings). 393 list a gate in kep.yaml, 56 more only mention one in the README.
Status after normalisation: implemented 296, implementable 277, provisional 51, withdrawn 11, replaced 5, rejected 3, deferred 1, unmapped 4.
37 groups in sigs.yaml (24 SIGs, 10 working groups, 3 committees), 298 leader entries, 231 distinct people, 241 subprojects, 531 OWNERS URLs.
Graph: 750 INTRODUCES edges (112 candidate rows name a gate with no gate file). `email` and `company` never leave the parser.

## Gaps / risks
- OWNERS history not yet measured (kubernetes has 142,151 commits; blobless, so old file contents fetch on demand).
- OWNERS entries can be team aliases, not people. Need OWNERS_ALIASES resolution in both repos.
- Gate stage ranges are per release; "currently stable" gates have no `toVersion`? (to verify on a live gate).
