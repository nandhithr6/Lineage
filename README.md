# Lineage

A company brain that knows when its knowledge is out of date, and proves every answer.
Built on public Kubernetes project data. Lineage is an independent hackathon project (FalkorDB Graph Hacks);
it is **not** an official Kubernetes or CNCF tool and is not endorsed by them. It is read-only: it never posts to
Kubernetes repositories, and doc fixes are drafts shown only in this app.

> Work in progress. Setup instructions, the graph data model and the key Cypher queries will be added here.

## Data sources and licenses

All data is public, needs no login, and is read-only. Each fact in the graph carries a commit-pinned link to its source.

| Source | What we use | License |
|---|---|---|
| [kubernetes/website](https://github.com/kubernetes/website) | Feature gate pages, docs pages with `feature-state` labels, release schedule | CC BY 4.0 (attribution required) |
| [kubernetes/enhancements](https://github.com/kubernetes/enhancements) | KEPs (`kep.yaml`, README) | Apache 2.0 |
| [kubernetes/community](https://github.com/kubernetes/community) | `sigs.yaml` org chart | Apache 2.0 |
| [kubernetes/kubernetes](https://github.com/kubernetes/kubernetes) | `OWNERS` files, `OWNERS_ALIASES` | Apache 2.0 |
| GitHub API (public repos) | Pull requests and reviews | Public data, used within API rate limits |

Licenses were read from the `LICENSE` file of each cloned repository. Copyright belongs to The Kubernetes Authors
and the respective contributors. Snapshot commit SHAs are recorded in [docs/data_inventory.md](docs/data_inventory.md).

**Personal data:** only public GitHub handles and display names are used. Email addresses and employer fields from
`sigs.yaml` are dropped at ingestion.

## Manually verified links (disclosure)

Lineage links hard-coded docs labels to feature gates automatically, with a confidence score. One link was verified
by hand by the project owner on 2026-10-15 because the linker could not separate two sibling gates
(`LocalStorageCapacityIsolation` and `LocalStorageCapacityIsolationFSQuotaMonitoring`, both scored 0.79):
the Resource Quotas section "Quota for local ephemeral storage". It is stored with `method: manual`, keeps the linker's original
score on the edge for the audit trail, and is listed with its reasoning and sources in
[snapshot/verified_links.jsonl](snapshot/verified_links.jsonl). All other links are automatic, and a finding needs an
*anchored* link (the gate is named next to "feature gate" in the section, or the link was verified by hand). Everything else
is reported as "possible drift" for a human to confirm.
