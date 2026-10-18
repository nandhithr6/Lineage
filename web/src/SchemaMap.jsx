import { useEffect, useRef, useState } from 'react'
import cytoscape from 'cytoscape'
import { api } from './api.js'
import { TYPE_COLORS } from './graph.js'

const fmt = (n) => n.toLocaleString('en-US')

/** The whole graph at the level of types: one circle per kind of thing, one arrow per kind of relationship, sized by how many there are. */
export default function SchemaMap() {
  const el = useRef(null)
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)
  const [info, setInfo] = useState('Hover or tap a circle or an arrow to see its numbers.')
  useEffect(() => { api.schema().then(setData).catch((e) => setErr(e.message)) }, [])

  useEffect(() => {
    if (!data || !el.current) return undefined
    const top = Math.max(...data.nodes.map((n) => n.count))
    const maxEdge = Math.max(...data.edges.map((e) => e.count))
    const cy = cytoscape({
      container: el.current, wheelSensitivity: 0.25, minZoom: 0.4, maxZoom: 2.5,
      elements: [
        ...data.nodes.map((n) => ({ group: 'nodes', data: { id: n.label, label: n.label, count: n.count, size: 34 + 60 * Math.sqrt(n.count / top) } })),
        ...data.edges.map((e, i) => ({ group: 'edges', data: { id: `e${i}`, source: e.source, target: e.target, type: e.type, count: e.count, w: 1 + 5 * Math.sqrt(e.count / maxEdge) } })),
      ],
      style: [
        { selector: 'node', style: { 'background-color': (n) => TYPE_COLORS[n.id()] || '#64748b', width: 'data(size)', height: 'data(size)', label: 'data(label)', 'font-size': 28, 'font-weight': 700, color: '#172033',
          'text-valign': 'bottom', 'text-margin-y': 8, 'text-background-color': '#fbfcff', 'text-background-opacity': 0.9, 'text-background-padding': 2, 'border-width': 2, 'border-color': '#ffffff' } },
        { selector: 'edge', style: { width: 'data(w)', 'line-color': '#aab6cf', 'target-arrow-color': '#aab6cf', 'target-arrow-shape': 'triangle', 'curve-style': 'bezier', 'control-point-step-size': 40, opacity: 0.6 } },
        { selector: '.hot', style: { 'line-color': '#2f5bea', 'target-arrow-color': '#2f5bea', opacity: 1, 'z-index': 9 } },
        { selector: 'node.hot', style: { 'border-color': '#2f5bea', 'border-width': 3 } },
        { selector: '.dim', style: { opacity: 0.15 } },
      ],
      layout: { name: 'circle', avoidOverlap: true, spacingFactor: 1.35, padding: 50, sort: (a, b) => b.data('count') - a.data('count') },
    })
    const focus = (collection) => { cy.elements().removeClass('hot dim'); cy.elements().not(collection).addClass('dim'); collection.addClass('hot') }
    const clear = () => { cy.elements().removeClass('hot dim'); setInfo('Hover or tap a circle or an arrow to see its numbers.') }
    cy.on('mouseover tap', 'node', (ev) => { const n = ev.target; focus(n.closedNeighborhood()); setInfo(`${n.id()}: ${fmt(n.data('count'))} nodes, ${n.connectedEdges().length} kinds of relationship attached`) })
    cy.on('mouseover tap', 'edge', (ev) => { const e = ev.target; focus(e.connectedNodes().union(e)); setInfo(`${e.data('type')}: ${e.source().id()} → ${e.target().id()}, ${fmt(e.data('count'))} relationships`) })
    cy.on('mouseout', 'node, edge', clear)
    return () => cy.destroy()
  }, [data])

  const nodes = data ? data.nodes.reduce((a, n) => a + n.count, 0) : 0
  const edges = data ? data.edges.reduce((a, e) => a + e.count, 0) : 0
  return (
    <section className="panel schema">
      <h2>The whole graph, as a map</h2>
      {err && <p className="note" role="alert">{err}</p>}
      {!data && !err && <p className="muted small">Counting what is in the graph…</p>}
      {data && <>
        <p className="muted small">Everything Lineage knows: <strong>{fmt(nodes)} nodes</strong> and <strong>{fmt(edges)} relationships</strong>, grouped by kind. A circle is a kind of thing (bigger = more of them), an arrow is a kind of relationship (thicker = more of them).
          The individual nodes are not drawn all at once because {fmt(nodes + edges)} elements on one screen is an unreadable hairball and slows every browser down; the panel below opens any one entity with its neighbours instead.</p>
        <div ref={el} className="graph-canvas schema-canvas" role="img" aria-label={`Map of ${data.nodes.length} kinds of node and ${data.edges.length} kinds of relationship`} />
        <p className="schema-info" aria-live="polite">{info}</p></>}
    </section>
  )
}
