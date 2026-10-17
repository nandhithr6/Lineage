# Cypher queries the product depends on

Generated from the code by `python -m lineage.graph.export_queries`; do not edit by hand.

**Graph algorithms:** traversals (pattern matching, variable-length paths, aggregation) plus FalkorDB's `algo.pageRank` over the review network. The HNSW vector index over `Chunk.embedding` (768 dims, cosine) serves free-text search and the vector-RAG baseline.

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
RETURN c.id, c.state, c.since, c.drift_eligible, g.name, g.removed, a.link_confidence, a.method, a.signals, a.eliminated
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
    f.evidence = r.evidence, f.message = r.message, f.anchored_by = r.anchored_by, f.active = true,
    f.detected_at = coalesce(f.detected_at, $now)
MERGE (f)-[e1:FOR_CLAIM]->(c)
MERGE (f)-[e2:FOR_GATE]->(g)
MERGE (f)-[e3:AS_OF]->(x)
SET e1.recorded_at = coalesce(e1.recorded_at, $now), e1.extracted_by = 'detector', e1.detector_version = $ver,
    e2.recorded_at = coalesce(e2.recorded_at, $now), e2.extracted_by = 'detector', e2.detector_version = $ver,
    e3.recorded_at = coalesce(e3.recorded_at, $now), e3.extracted_by = 'detector', e3.detector_version = $ver
```

## Graph algorithm: PageRank over the review network

`algo.pageRank(label, relationship-type)` runs inside FalkorDB on the derived `REVIEWED_BY` edges (author to reviewer). Ranks position in the review network, not skill.

```cypher
CALL algo.pageRank('Person', 'REVIEWED_BY') YIELD node, score
WITH node, score ORDER BY score DESC LIMIT $limit
RETURN node.handle, score
```

## Vector index: create it and load a chunk

HNSW cosine index over 768-dimension embeddings (BAAI/bge-base-en-v1.5, computed locally); `vecf32` stores the vector on the node. `CHUNK_OF` edges link a chunk to the node it is about.

```cypher
UNWIND $rows AS r
MERGE (c:Chunk {id: r.id})
SET c.text = r.text, c.kind = r.kind, c.source_url = r.source_url, c.line_start = r.line_start, c.line_end = r.line_end, c.parent = r.parent, c.model = r.model,
    c.embedding = vecf32(r.vec), c.recorded_at = coalesce(c.recorded_at, $now)
```

## Vector search: the nearest chunks to a question

FalkorDB's score is a cosine distance (0 = identical); the product reports 1 - distance. Vector queries cannot be combined with property filters, so hits are expanded along edges afterwards (search_text).

```cypher
CALL db.idx.vector.queryNodes('Chunk', 'embedding', $k, vecf32($v)) YIELD node, score
RETURN node.id, node.text, node.source_url, node.kind, node.line_start, node.line_end, node.parent, score
ORDER BY score
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

`method` is `gate_name`, `linker` or `manual`; the since-version rule's eliminated candidates are stored on the edge.

```cypher
MATCH (c:Claim {id: $c})-[a:ABOUT]->(g:FeatureGate {name: $g})
RETURN a.method, a.link_confidence, a.linker_confidence, a.signals, a.verified_by, a.verified_on, a.reason, a.source_url, a.eliminated
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
