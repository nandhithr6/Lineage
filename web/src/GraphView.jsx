import { useEffect, useRef } from 'react'
import cytoscape from 'cytoscape'
import { TYPE_COLORS } from './graph.js'

/** Draws the finding's graph. The finding's own path is bold; tap any node to pull its neighbors in from the API. */
export default function GraphView({ graph, onExpand, reveal = null, hint = "Bold blue = this finding's path. Tap a node to expand it." }) {
  const el = useRef(null)
  const cy = useRef(null)
  const expand = useRef(onExpand)
  expand.current = onExpand

  useEffect(() => {
    const dark = false  // the app is light-themed
    cy.current = cytoscape({
      container: el.current,
      layout: { name: 'preset' },
      wheelSensitivity: 0.25,
      style: [
        { selector: 'node', style: {
          'background-color': (n) => TYPE_COLORS[n.data('type')] || '#64748b', label: 'data(label)', color: dark ? '#e5e7eb' : '#1f2937',
          'font-size': 11, 'text-valign': 'bottom', 'text-margin-y': 5, 'text-wrap': 'wrap', 'text-max-width': 120, width: 26, height: 26 } },
        { selector: 'node[?removed]', style: { 'border-width': 3, 'border-style': 'dashed', 'border-color': '#dc2626' } },
        { selector: 'edge', style: { width: 1.5, 'line-color': dark ? '#475569' : '#cbd5e1', 'target-arrow-color': dark ? '#475569' : '#cbd5e1',
          'target-arrow-shape': 'triangle', 'curve-style': 'bezier', label: (e) => (e.cy().edges().length > 24 ? '' : e.data('type')), 'font-size': 9, color: dark ? '#9ca3af' : '#6b7280',
          'text-rotation': 'autorotate', 'text-background-color': dark ? '#111827' : '#ffffff', 'text-background-opacity': 0.85, 'text-background-padding': 2 } },
        { selector: 'edge[?onPath]', style: { width: 3.5, 'line-color': '#2563eb', 'target-arrow-color': '#2563eb', color: '#2563eb' } },
        { selector: '.compact edge', style: { label: '' } },
        { selector: '.faded', style: { opacity: 0.07 } },
        { selector: 'node.now', style: { 'border-width': 4, 'border-color': '#f59e0b' } },
        { selector: 'node:selected', style: { 'border-width': 3, 'border-color': '#f59e0b' } },
      ],
    })
    cy.current.on('tap', 'node', (evt) => expand.current?.(evt.target.data('entity'), evt.target.id()))
    return () => cy.current?.destroy()
  }, [])

  useEffect(() => {
    const c = cy.current
    if (!c) return
    const existing = new Set(c.elements().map((e) => e.id()))
    const incoming = [...graph.nodes.map((n) => ({ group: 'nodes', data: n })), ...graph.edges.map((e) => ({ group: 'edges', data: e }))]
    const fresh = incoming.filter((x) => !existing.has(x.data.id))
    if (!fresh.length) return
    // New neighbors (no position yet) are placed in a ring around the existing node they connect to.
    const placed = new Map(c.nodes().map((n) => [n.id(), n.position()]))
    const ring = new Map()
    for (const x of fresh.filter((f) => f.group === 'edges')) {
      const [a, b] = [x.data.source, x.data.target]
      const anchor = placed.has(a) ? a : placed.has(b) ? b : null
      const other = anchor === a ? b : a
      if (anchor && !placed.has(other)) ring.set(other, anchor)
    }
    const perAnchor = new Map()
    for (const [id, anchor] of ring) perAnchor.set(anchor, [...(perAnchor.get(anchor) || []), id])
    for (const x of fresh.filter((f) => f.group === 'nodes' && !f.data.position)) {
      const anchor = ring.get(x.data.id)
      const sibs = perAnchor.get(anchor) || []
      const k = sibs.indexOf(x.data.id)
      const p = placed.get(anchor) || { x: 0, y: 0 }
      const angle = (2 * Math.PI * k) / Math.max(sibs.length, 1) - Math.PI / 2
      x.position = { x: p.x + 150 * Math.cos(angle), y: p.y + 150 * Math.sin(angle) + 20 }
    }
    c.add(fresh.map((x) => ({ ...x, position: x.position || x.data.position })))
    c.fit(undefined, 30)
    c.style().update()
  }, [graph])

  useEffect(() => {  // "replay the trace": only the first `reveal` nodes of the path are in the story so far; the newest one is ringed
    const c = cy.current
    if (!c) return
    c.elements().removeClass('faded now')
    if (reveal == null) return
    const shown = new Set(graph.nodes.slice(0, reveal).map((n) => n.id))
    c.nodes().forEach((n) => { if (!shown.has(n.id())) n.addClass('faded') })
    c.edges().forEach((e) => { if (!shown.has(e.source().id()) || !shown.has(e.target().id())) e.addClass('faded') })
    const newest = graph.nodes[reveal - 1]
    if (newest) c.getElementById(newest.id).addClass('now')
  }, [reveal, graph])

  return (
    <figure className="graph">
      <div ref={el} className="graph-canvas" role="img" aria-label={`Graph of ${graph.nodes.length} connected items. Tap a node to expand it.`} />
      <figcaption>
        {Object.entries(TYPE_COLORS).filter(([t]) => graph.nodes.some((n) => n.type === t)).map(([t, c]) => <span key={t} className="legend"><i style={{ background: c }} />{t}</span>)}
        <span className="hint">{hint}</span>
      </figcaption>
    </figure>
  )
}
