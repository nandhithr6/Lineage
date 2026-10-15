// Pure helpers (no React, no DOM) so they can be unit-tested with `node --test`.

export const TYPE_COLORS = {
  DocPage: '#6b7280', DocSection: '#8b95a5', Claim: '#d97706', FeatureGate: '#2563eb', Release: '#0d9488',
  KEP: '#7c3aed', SIG: '#be185d', Person: '#475569', Team: '#0f766e', CodePath: '#64748b', Finding: '#dc2626',
}

export const shorten = (s, n = 34) => (s && s.length > n ? s.slice(0, n - 1) + '…' : s || '')

export const releaseNum = (version) => {
  const [major, minor] = String(version).split('.')
  return Number(major) * 100 + Number(minor)
}

const STEP_X = 200
const STEP_Y = 120
const node = (type, key, label, extra = {}) => ({ id: `${type}:${key}`, type, label: shorten(label), entity: { label: type, key: String(key) }, ...extra })
const at = (n, col, row) => ({ ...n, position: { x: col * STEP_X, y: row * STEP_Y } })
const edge = (a, b, type) => ({ id: `${a.id}>${type}>${b.id}`, source: a.id, target: b.id, type, onPath: true })

/** Build the finding's graph from the /findings/{id} sections. Edges are the REAL relations, not "consecutive path items". */
export function buildTraceGraph(sections) {
  if (!sections || !sections.claim || !sections.gate) return { nodes: [], edges: [] }
  const { page, claim, gate, kep, finding } = sections
  const lastStage = gate.history?.length ? gate.history[gate.history.length - 1] : null
  const nodes = []
  const edges = []
  const pageN = at(node('DocPage', page.path, page.title || page.path), 0, 0)
  const sectionN = at(node('DocSection', claim.id.replace(/:\d+$/, ''), claim.heading || 'section'), 1, 0)
  const claimN = at(node('Claim', claim.id, `${claim.stage}${claim.since ? ' since v' + claim.since : ''}`), 2, 0)
  const gateN = at(node('FeatureGate', gate.name, gate.name, { removed: gate.removed }), 3, 0)
  nodes.push(pageN, sectionN, claimN, gateN)
  edges.push(edge(pageN, sectionN, 'HAS_SECTION'), edge(sectionN, claimN, 'HAS_CLAIM'), edge(claimN, gateN, 'ABOUT'))
  if (lastStage) {
    const rel = at(node('Release', releaseNum(lastStage.to), `${lastStage.to} ${lastStage.stage}`), 4, 0)
    nodes.push(rel)
    edges.push(edge(gateN, rel, 'STAGE_IN'))
  }
  if (kep) {
    const kepN = at(node('KEP', kep.id, kep.title), 3, 1)
    nodes.push(kepN)
    edges.push(edge(kepN, gateN, 'INTRODUCES'))
    if (kep.sig) {
      const sigN = at(node('SIG', kep.sig, kep.sig), 4, 1)
      nodes.push(sigN)
      edges.push(edge(kepN, sigN, 'OWNED_BY'))
    }
  }
  return { nodes, edges, highlight: finding?.kind }
}

/** Merge neighbors returned by GET /entity/... into an existing graph (new nodes/edges are off the finding's path). */
export function mergeEntity(graph, entity) {
  const have = new Set(graph.nodes.map((n) => n.id))
  const haveE = new Set(graph.edges.map((e) => e.id))
  const nodes = [...graph.nodes]
  const edges = [...graph.edges]
  for (const n of entity.neighbors) {
    if (!have.has(n.id)) {
      have.add(n.id)
      const key = n.id.slice(n.label.length + 1)
      nodes.push({ id: n.id, type: n.label, label: shorten(n.props.title || n.props.name || n.props.heading || n.props.version || key), entity: { label: n.label, key } })
    }
  }
  for (const e of entity.edges) {
    const id = `${e.source}>${e.type}>${e.target}`
    if (!haveE.has(id) && have.has(e.source) && have.has(e.target)) {
      haveE.add(id)
      edges.push({ id, source: e.source, target: e.target, type: e.type, onPath: false })
    }
  }
  return { ...graph, nodes, edges }
}

/** Turn "text [e1] more [e3, e4]" into [{text}, {cite:'e1'}, ...] so every cited id becomes a clickable chip (single or grouped brackets). */
export function splitCitations(text) {
  const out = []
  for (const part of String(text || '').split(/(\[\s*e\d+(?:\s*[,;]\s*e\d+)*\s*\])/g)) {
    if (!part) continue
    const ids = part.match(/^\[\s*(e\d+(?:\s*[,;]\s*e\d+)*)\s*\]$/)
    if (ids) for (const id of ids[1].match(/e\d+/g)) out.push({ cite: id })
    else out.push({ text: part })
  }
  return out
}

/** Classify unified-diff lines for coloring. */
export function diffLines(diff) {
  return String(diff || '').split('\n').map((l) => ({
    text: l,
    kind: l.startsWith('+++') || l.startsWith('---') ? 'meta' : l.startsWith('@@') ? 'hunk' : l.startsWith('+') ? 'add' : l.startsWith('-') ? 'del' : 'ctx',
  }))
}

export const SEVERITY_LABEL = { finding: 'Finding', minor: 'Minor', review: 'Needs a look' }
export const KIND_LABEL = {
  stale_stage: 'Stale stage', wrong_since_version: 'Wrong since-version', documents_removed_feature: 'Documents a removed feature',
  link_ambiguous: 'Link is ambiguous', possible_drift: 'Possible drift (link unconfirmed)',
}


/** A centred graph for one entity and its neighbours from GET /entity/...; the neighbours sit on a ring (deterministic, no overlap). */
export function entityGraph(entity, radius = 190) {
  const c = entity.node
  const key = c.id.slice(c.label.length + 1)
  const centre = { id: c.id, type: c.label, label: shorten(c.props.title || c.props.name || c.props.heading || c.props.version || key), entity: { label: c.label, key }, position: { x: 0, y: 0 } }
  const n = Math.max(entity.neighbors.length, 1)
  const base = Math.max(radius, 12 * n)  // more neighbours -> bigger circle; above 14 they alternate between an inner and an outer ring
  const nodes = [centre, ...entity.neighbors.map((m, i) => {
    const k = m.id.slice(m.label.length + 1)
    const angle = (2 * Math.PI * i) / n - Math.PI / 2
    const r = n > 14 && i % 2 ? base * 0.62 : base
    return { id: m.id, type: m.label, label: shorten(m.props.title || m.props.name || m.props.heading || m.props.version || k, 26), entity: { label: m.label, key: k },
      position: { x: r * Math.cos(angle) * 1.5, y: r * Math.sin(angle) } }
  })]
  const ids = new Set(nodes.map((x) => x.id))
  const edges = entity.edges.filter((e) => ids.has(e.source) && ids.has(e.target)).map((e) => ({ id: `${e.source}>${e.type}>${e.target}`, source: e.source, target: e.target, type: e.type, onPath: false }))
  return { nodes, edges, truncated: entity.truncated }
}

/** People listed more than once (via several folders or teams) collapse to one entry that remembers where they were declared. */
export function distinctPeople(rows) {
  const by = new Map()
  for (const r of rows) {
    const p = by.get(r.handle) || { handle: r.handle, role: r.role, places: new Set(), teams: new Set(), source_url: r.source_url }
    p.places.add(r.declared_at || '/')
    if (r.via_team) p.teams.add(r.via_team)
    by.set(r.handle, p)
  }
  return [...by.values()].map((p) => ({ ...p, places: [...p.places], teams: [...p.teams] }))
}
