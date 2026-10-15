# Cypher queries the product depends on

Generated from the code by `python -m lineage.graph.export_queries`; do not edit by hand.

**Graph algorithms:** the product currently uses traversals only (pattern matching, variable-length paths, aggregation). It does not yet call FalkorDB's `algo.*` procedures or the vector index; both are on the roadmap, not in use.

## Ownership: who owns this path? (walk UP the folder tree)

`CHILD_OF*0..` is a variable-length traversal; the `none(...)` condition stops the walk after a folder whose OWNERS says `no_parent_owners: true`. `as_of` is a sampled release number (130 = 1.30), which makes this the time-travel query too.

```cypher
MATCH (start:CodePath {id: $start})
MATCH p = (start)-[:CHILD_OF*0..]->(anc:CodePath)
WHERE none(n IN nodes(p)[0..length(p)] WHERE n.no_parent_owners)
WITH anc, length(p) AS hops
MATCH (o)-[r:APPROVER_OF|REVIEWER_OF|EMERITUS_APPROVER_OF]->(anc)
WHERE r.valid_from <= $as_of AND (r.valid_to IS NULL OR r.valid_to > $as_of)
RETURN anc.id, anc.path, hops, type(r), labels(o)[0], coalesce(o.handle, o.id), r.filter, r.source_url, r.valid_from, r.valid_to
```

## Team members at a point in time

Aliases such as `sig-docs-en-owners` are `Team` nodes; people are resolved by the graph, not copied onto folders.

```cypher
MATCH (p:Person)-[m:MEMBER_OF]->(t:Team {id: $team})
WHERE m.valid_from <= $as_of AND (m.valid_to IS NULL OR m.valid_to > $as_of)
RETURN p.handle, m.source_url
```

## Drift detector input: hard-coded claims and their candidate gates

Each claim can have 0..3 `ABOUT` candidates, each with its own confidence and method.

```cypher
MATCH (c:Claim {kind: 'hardcoded'})
OPTIONAL MATCH (c)-[a:ABOUT]->(g:FeatureGate)
RETURN c.id, c.state, c.since, c.drift_eligible, g.name, g.removed, a.link_confidence, a.method, a.signals
```

## Drift detector input: a gate's stage in every release

One `STAGE_IN` edge per release in the gate's life.

```cypher
MATCH (g:FeatureGate)-[e:STAGE_IN]->(r:Release) WHERE g.name IN $names
RETURN g.name, r.num, r.version, e.stage, e.overlap
```

## Writing a Finding (idempotent)

Findings are keyed by `kind|claim|gate|release|stage`, never by internal IDs. `FOR_CLAIM`, `FOR_GATE` and `AS_OF` point at nodes (edges cannot point at edges).

```cypher
UNWIND $rows AS r
MATCH (c:Claim {id: r.claim_id}), (g:FeatureGate {name: r.gate}), (x:Release {version: r.release})
MERGE (f:Finding {key: r.key})
SET f.kind = r.kind, f.severity = r.severity, f.claimed_stage = r.claimed_stage, f.claimed_since = r.claimed_since,
    f.actual_stages = r.actual_stages, f.entered_version = r.entered_version, f.gate_removed = r.gate_removed,
    f.evidence = r.evidence, f.message = r.message, f.active = true,
    f.detected_at = coalesce(f.detected_at, $now)
MERGE (f)-[:FOR_CLAIM]->(c)
MERGE (f)-[:FOR_GATE]->(g)
MERGE (f)-[:AS_OF]->(x)
```

## Trace: gate stage history

```cypher
MATCH (g:FeatureGate {name: $g})-[e:STAGE_IN]->(r:Release) RETURN r.num, r.version, e.stage, e.overlap, e.source_url ORDER BY r.num
```

## Trace: the design decisions (KEPs) that introduced a gate

```cypher
MATCH (k:KEP)-[i:INTRODUCES]->(g:FeatureGate {name: $g})
OPTIONAL MATCH (k)-[:OWNED_BY]->(s:SIG)
RETURN k.id, k.title, k.status, k.stage, k.latest_milestone, k.source_url, i.method, i.confidence, i.source_url, s.id, s.name, s.source_url
```

## Trace: how a claim was linked to its gate

`method` is `gate_name`, `linker` or `manual`; manual links keep the linker's own score.

```cypher
MATCH (c:Claim {id: $c})-[a:ABOUT]->(g:FeatureGate {name: $g})
RETURN a.method, a.link_confidence, a.linker_confidence, a.signals, a.verified_by, a.verified_on, a.reason, a.source_url
```

## Loader: a stage edge (MERGE makes the load idempotent)

`MERGE` finds the edge or creates it; running the loader twice gives identical counts.

```cypher
UNWIND $rows AS r
MATCH (g:FeatureGate {name: r.gate}), (x:Release {num: r.release_num})
MERGE (g)-[e:STAGE_IN {stage: r.stage}]->(x)
SET e.default_value = r.default_value, e.locked = r.locked, e.overlap = r.overlap,
    e.inferred_end = r.inferred_end, e.source_url = r.source_url,
    e.extracted_by = 'structured', e.confidence = 1.0,
    e.recorded_at = coalesce(e.recorded_at, $now)
```

## Loader: ownership edge with validity interval

`valid_from` and `filter` are part of the edge identity, so one person can hold a role over several separate intervals.

```cypher
UNWIND $rows AS r MATCH (a:Person {handle: r.subject}), (b:CodePath {id: r.dir_id}) MERGE (a)-[e:APPROVER_OF {valid_from: r.valid_from, filter: r.filter}]->(b) SET e.valid_to = r.valid_to, e.source_url = r.source_url, e.extracted_by = 'structured', e.confidence = 1.0, e.recorded_at = coalesce(e.recorded_at, $now)
```

## Agent tools (each runs read-only `ro_query` calls)

`drift_findings`, `explain_finding`, `gate_timeline`, `doc_claims`, `owners_of`, `sig_leaders`, `decision_lineage`: see `lineage/agent/tools.py`. Every tool returns an evidence bundle whose items carry a `source_url`.
