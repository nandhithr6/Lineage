import test from 'node:test'
import assert from 'node:assert/strict'
import { buildTraceGraph, diffLines, distinctPeople, entityGraph, inlineTokens, markdownBlocks, mergeEntity, releaseNum, splitCitations } from './graph.js'

const sections = {
  page: { path: 'content/en/docs/concepts/policy/resource-quotas.md', title: 'Resource Quotas' },
  claim: { id: 'content/en/docs/concepts/policy/resource-quotas.md#quota-for-local-ephemeral-storage:1', heading: 'Quota for local ephemeral storage', stage: 'alpha', since: '1.8' },
  gate: { name: 'LocalStorageCapacityIsolation', removed: true, history: [{ stage: 'alpha', from: '1.7', to: '1.9' }, { stage: 'stable', from: '1.25', to: '1.26' }] },
  kep: { id: 'sig-storage/361-local-ephemeral-storage-isolation', title: 'Local Ephemeral Storage Capacity Isolation', sig: 'sig-storage' },
  finding: { kind: 'stale_stage' },
}

test('releaseNum', () => { assert.equal(releaseNum('1.26'), 126); assert.equal(releaseNum('1.9'), 109) })

test('trace graph uses real relations, not a chain', () => {
  const g = buildTraceGraph(sections)
  assert.equal(g.nodes.length, 7)
  const rel = (t) => g.edges.filter((e) => e.type === t).map((e) => [e.source.split(':')[0], e.target.split(':')[0]])
  assert.deepEqual(rel('INTRODUCES'), [['KEP', 'FeatureGate']])
  assert.deepEqual(rel('STAGE_IN'), [['FeatureGate', 'Release']])
  assert.ok(!g.edges.some((e) => e.source.startsWith('Release') || (e.source.startsWith('KEP') && e.target.startsWith('Release'))))
  assert.ok(g.nodes.find((n) => n.id === 'Release:126'))  // matches the API's /entity/Release/126
  assert.equal(g.nodes.find((n) => n.type === 'DocSection').id, 'DocSection:content/en/docs/concepts/policy/resource-quotas.md#quota-for-local-ephemeral-storage')
})

test('trace graph is laid out left to right with no overlapping positions', () => {
  const g = buildTraceGraph(sections)
  const spots = g.nodes.map((n) => `${n.position.x},${n.position.y}`)
  assert.equal(new Set(spots).size, g.nodes.length)
  const x = (type) => g.nodes.find((n) => n.type === type).position.x
  assert.ok(x('DocPage') < x('DocSection') && x('DocSection') < x('Claim') && x('Claim') < x('FeatureGate') && x('FeatureGate') < x('Release'))
})

test('missing evidence gives an empty graph, not a crash', () => {
  assert.deepEqual(buildTraceGraph(null).nodes, [])
  assert.equal(buildTraceGraph({ ...sections, kep: null }).nodes.length, 5)
})

test('mergeEntity adds only new nodes and off-path edges', () => {
  const g = buildTraceGraph(sections)
  const entity = {
    neighbors: [{ id: 'Person:msau42', label: 'Person', props: {} }, { id: 'FeatureGate:LocalStorageCapacityIsolation', label: 'FeatureGate', props: {} }],
    edges: [{ source: 'Person:msau42', target: 'FeatureGate:LocalStorageCapacityIsolation', type: 'X' }],
  }
  const m = mergeEntity(g, entity)
  assert.equal(m.nodes.length, g.nodes.length + 1)
  assert.equal(m.edges.at(-1).onPath, false)
  assert.equal(mergeEntity(m, entity).nodes.length, m.nodes.length)  // idempotent
})

test('splitCitations', () => {
  assert.deepEqual(splitCitations('Stable [e3] then [e12].'), [{ text: 'Stable ' }, { cite: 'e3' }, { text: ' then ' }, { cite: 'e12' }, { text: '.' }])
})

test('diffLines classifies lines', () => {
  const kinds = diffLines('--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new\n same').map((l) => l.kind)
  assert.deepEqual(kinds, ['meta', 'meta', 'hunk', 'del', 'add', 'ctx'])
})

test('entityGraph centres the entity and keeps only edges between shown nodes', () => {
  const e = { node: { id: 'FeatureGate:G', label: 'FeatureGate', props: { name: 'G' } },
    neighbors: [{ id: 'Release:125', label: 'Release', props: { version: '1.25' } }, { id: 'KEP:k/1', label: 'KEP', props: { title: 'A KEP' } }],
    edges: [{ source: 'FeatureGate:G', target: 'Release:125', type: 'STAGE_IN' }, { source: 'KEP:k/1', target: 'FeatureGate:G', type: 'INTRODUCES' }, { source: 'Person:x', target: 'KEP:k/1', type: 'AUTHORED' }] }
  const g = entityGraph(e)
  assert.deepEqual(g.nodes[0].position, { x: 0, y: 0 })
  assert.equal(g.nodes.length, 3)
  assert.equal(g.edges.length, 2)  // the edge to an unshown Person is dropped
  assert.equal(new Set(g.nodes.map((n) => `${n.position.x},${n.position.y}`)).size, 3)
  assert.equal(g.nodes[1].entity.key, '125')
})

test('distinctPeople collapses repeated listings and remembers where', () => {
  const rows = [{ handle: 'janetkuo', role: 'APPROVER_OF', declared_at: 'pkg', via_team: null, source_url: 'u1' },
    { handle: 'janetkuo', role: 'APPROVER_OF', declared_at: 'pkg/controller/job', via_team: 'kubernetes:sig-apps-approvers', source_url: 'u2' },
    { handle: 'mimowo', role: 'APPROVER_OF', declared_at: 'pkg/controller/job', via_team: null, source_url: 'u3' }]
  const d = distinctPeople(rows)
  assert.equal(d.length, 2)
  const j = d.find((p) => p.handle === 'janetkuo')
  assert.deepEqual(j.places, ['pkg', 'pkg/controller/job'])
  assert.deepEqual(j.teams, ['kubernetes:sig-apps-approvers'])
})

test('entityGraph spreads many neighbours over two rings so labels do not collide', () => {
  const neighbors = Array.from({ length: 24 }, (_, i) => ({ id: `KEP:k${i}`, label: 'KEP', props: { title: `kep ${i}` } }))
  const g = entityGraph({ node: { id: 'SIG:s', label: 'SIG', props: { name: 's' } }, neighbors, edges: [] })
  const radii = new Set(g.nodes.slice(1).map((n) => Math.round(Math.hypot(n.position.x / 1.5, n.position.y))))
  assert.equal(radii.size, 2)
})

test('splitCitations turns grouped citations into one chip per id', () => {
  assert.deepEqual(splitCitations('A [e7, e8] and [e9].'), [{ text: 'A ' }, { cite: 'e7' }, { cite: 'e8' }, { text: ' and ' }, { cite: 'e9' }, { text: '.' }])
  assert.deepEqual(splitCitations('no cite [1, 2]'), [{ text: 'no cite [1, 2]' }])
})

test('markdown: bold, code and inline bullets become tokens, never HTML', () => {
  assert.deepEqual(inlineTokens('a **b** `c` d'), [{ text: 'a ' }, { text: 'b', bold: true }, { text: ' ' }, { text: 'c', code: true }, { text: ' d' }])
  assert.deepEqual(inlineTokens('<b>x</b> ** not bold'), [{ text: '<b>x</b> ** not bold' }])  // markup stays text
  const blocks = markdownBlocks('There are two pages: * **About cgroup v2** (url) is stale [e3]. * **Device Plugins** too [e4].\nDone.')
  assert.deepEqual(blocks.map((b) => b.type), ['p', 'li', 'li', 'p'])
  assert.equal(blocks[1].tokens[0].bold, true)
  assert.deepEqual(markdownBlocks('- one\n- two').map((b) => b.type), ['li', 'li'])
  assert.deepEqual(markdownBlocks(''), [])
  assert.equal(splitCitations(blocks[1].tokens.map((t) => t.text).join('')).filter((p) => p.cite).length, 1)  // citations survive inside a bullet
})
