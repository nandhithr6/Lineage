import { useEffect, useState } from 'react'
import { api } from './api.js'
import GraphView from './GraphView.jsx'
import SchemaMap from './SchemaMap.jsx'
import PageIntro from './PageIntro.jsx'
import { distinctPeople, entityGraph, mergeEntity } from './graph.js'

const PATHS = [
  { repo: 'kubernetes', path: 'pkg/controller/job', hint: 'approver changed at 1.32' },
  { repo: 'kubernetes', path: 'pkg/volume', hint: 'sig-storage code' },
  { repo: 'kubernetes', path: 'pkg/kubelet', hint: 'sig-node code' },
  { repo: 'website', path: 'content/en/docs/concepts/policy/resource-quotas.md', hint: 'a docs page (latest only)' },
]
const ENTITIES = [
  { label: 'FeatureGate', key: 'LocalStorageCapacityIsolation', hint: 'a gate and its stage in every release' },
  { label: 'SIG', key: 'sig-storage', hint: 'a team, its leaders and code' },
  { label: 'KEP', key: 'sig-storage/361-local-ephemeral-storage-isolation', hint: 'a design decision' },
  { label: 'CodePath', key: 'kubernetes:pkg/controller/job', hint: 'a folder and its owners over time' },
]
const LABELS = ['FeatureGate', 'SIG', 'KEP', 'CodePath', 'Team', 'Person', 'DocPage', 'Release']
const ROLE = { APPROVER_OF: 'approver', REVIEWER_OF: 'reviewer', EMERITUS_APPROVER_OF: 'emeritus approver' }

function Src({ url }) { return url ? <a className="src" href={url} target="_blank" rel="noreferrer">source ↗</a> : null }

function ActivityPanel({ path }) {
  const [d, setD] = useState(null)
  const [err, setErr] = useState(null)
  useEffect(() => {
    let live = true
    setD(null); setErr(null)
    api.experts(path).then((x) => live && setD(x)).catch((e) => live && setErr(e.message))
    return () => { live = false }
  }, [path])
  if (err) return <p className="muted small">{err}</p>
  if (!d) return <p className="muted small">Looking at recent review activity…</p>
  if (!d.experts.length) return <p className="muted small">No review activity loaded for this folder. {d.coverage_note}</p>
  return (
    <div className="activity"><h3>Who actually reviews it (last {d.months} months, recent work counts more)</h3>
      <ul className="experts">{d.experts.slice(0, 8).map((x) => (
        <li key={x.handle}><a className="person" href={`https://github.com/${x.handle}`} target="_blank" rel="noreferrer">@{x.handle}</a>{' '}
          <small>{x.prs_reviewed} PRs, {x.approvals} approvals, last {x.last_active} · {x.listed_approver ? 'listed approver' : 'not a listed approver'}</small>{' '}
          <a className="src" href={x.example_pr} target="_blank" rel="noreferrer">example PR ↗</a></li>))}</ul>
      <p className="small">Listed approvers with no activity in {d.months} months: <strong>{d.listed_but_inactive.length}</strong>{d.listed_but_inactive.length ? ` (${d.listed_but_inactive.slice(0, 6).map((h) => '@' + h).join(', ')}${d.listed_but_inactive.length > 6 ? '…' : ''})` : ''}. Other people active here (reviewed or wrote a PR) who are not listed: <strong>{d.active_but_not_listed.length}</strong>{d.active_but_not_listed.length ? ` (${d.active_but_not_listed.slice(0, 6).map((h) => '@' + h).join(', ')}${d.active_but_not_listed.length > 6 ? '…' : ''})` : ''}.</p>
      <p className="muted small">{d.coverage_note}</p></div>
  )
}

function OwnersPanel({ params }) {
  const [repo, setRepo] = useState(params.get('repo') || 'kubernetes')
  const [path, setPath] = useState(params.get('path') || 'pkg/controller/job')
  const [asOf, setAsOf] = useState(params.get('as_of') || 'latest')
  const [cmp, setCmp] = useState(params.get('cmp') || '')
  const [data, setData] = useState(null)
  const [diff, setDiff] = useState(null)
  const [err, setErr] = useState(null)

  useEffect(() => {
    let live = true
    setErr(null); setDiff(null)
    api.owners(repo, path, asOf).then((d) => live && setData(d)).catch((e) => { if (live) { setData(null); setErr(e.message) } })
    if (cmp && repo === 'kubernetes') api.changes(path, cmp, asOf, repo).then((d) => live && setDiff(d)).catch((e) => live && setErr(e.message))
    return () => { live = false }
  }, [repo, path, asOf, cmp])

  const releases = data?.available_releases || ['latest (1.38-dev)']
  const val = (r) => (r.startsWith('latest') ? 'latest' : r)
  const byRole = {}
  for (const o of data?.owners || []) { (byRole[o.role] ||= []).push(o) }
  for (const role of Object.keys(byRole)) byRole[role] = distinctPeople(byRole[role])
  const people = new Set((data?.owners || []).map((o) => o.handle)).size
  return (
    <section className="panel">
      <h2>Who owned this, and when?</h2>
      <p className="muted small">Pick a folder and a release. Teams are resolved to people and inheritance is applied; history is sampled at releases 1.30 to 1.37 and now.</p>
      <div className="chips">{PATHS.map((p) => <button key={p.path} className={p.path === path ? 'chip on' : 'chip'} title={p.hint} onClick={() => { setRepo(p.repo); setPath(p.path); setAsOf('latest'); setCmp('') }}>{p.path.split('/').slice(-2).join('/')}</button>)}</div>
      <div className="row">
        <label>Path <input value={path} onChange={(e) => setPath(e.target.value.trim())} aria-label="Path" /></label>
        <label>As of <select value={val(asOf)} onChange={(e) => setAsOf(e.target.value)} disabled={repo === 'website'}>{releases.map((r) => <option key={r} value={val(r)}>{r}</option>)}</select></label>
        <label>Compare with <select value={cmp} onChange={(e) => setCmp(e.target.value)} disabled={repo === 'website'}><option value="">(none)</option>{releases.map((r) => <option key={r} value={val(r)}>{r}</option>)}</select></label>
      </div>
      {err && <p className="note" role="alert">{err}</p>}
      {diff && <div className="diffbox"><h3>Between {diff.from_release} and {diff.to_release}</h3>
        {diff.added.length + diff.removed.length === 0 ? <p className="muted">No ownership change ({diff.unchanged} roles unchanged).</p> : <ul>
          {diff.removed.map((o, i) => <li key={'r' + i} className="gone">− @{o.handle} <small>{ROLE[o.role]}{o.via_team ? ` via ${o.via_team}` : ''} at {o.declared_at || '/'}</small> <Src url={o.source_url} /></li>)}
          {diff.added.map((o, i) => <li key={'a' + i} className="new">+ @{o.handle} <small>{ROLE[o.role]}{o.via_team ? ` via ${o.via_team}` : ''} at {o.declared_at || '/'}</small> <Src url={o.source_url} /></li>)}</ul>}</div>}
      {data && <div><h3>{people} people as of {data.as_of}</h3>
        {data.note && <p className="muted">{data.note}</p>}
        <p className="muted small">Declared in: {data.declared_at.join(', ') || 'nothing found'}</p>
        {Object.entries(byRole).map(([role, rows]) => <div key={role}><h4>{ROLE[role] || role} ({rows.length})</h4>
          <p className="people">{rows.slice(0, 40).map((o) => <a key={o.handle} className="person" href={`https://github.com/${o.handle}`} target="_blank" rel="noreferrer" title={`${o.teams.length ? 'via ' + o.teams.join(', ') : 'listed directly'}; declared at ${o.places.join(', ')}`}>@{o.handle}</a>)}{rows.length > 40 && <small> +{rows.length - 40} more</small>}</p></div>)}
        {data.owners[0]?.source_url && <p className="small">Example source: <Src url={data.owners[0].source_url} /></p>}
        {repo === 'kubernetes' && asOf === 'latest' && <ActivityPanel path={path} />}</div>}
    </section>
  )
}

const START_LIMIT = 40, MORE_STEP = 30, MAX_LIMIT = 100  // neighbours shown at first / added per click / the API's cap

function EntityPanel({ params }) {
  const [label, setLabel] = useState(params.get('label') || 'FeatureGate')
  const [key, setKey] = useState(params.get('key') || 'LocalStorageCapacityIsolation')
  const [graph, setGraph] = useState(null)
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(false)
  const [limit, setLimit] = useState(START_LIMIT)  // neighbours requested for the centre node; the API allows up to 100
  const [shown, setShown] = useState({ label: null, key: null })

  async function load(l = label, k = key, n = START_LIMIT) {
    setBusy(true); setErr(null); setLimit(n); setShown({ label: l, key: k })
    try {
      const e = await api.entity(l, k, n)
      setGraph({ ...entityGraph(e, Math.max(190, e.neighbors.length * 11)), stamp: `${l}:${k}:${n}` })  // a bigger ring for more neighbours, so labels do not overlap
    } catch (e) { setErr(e.message); setGraph(null) } finally { setBusy(false) }
  }
  useEffect(() => { load(params.get('label') || label, params.get('key') || key) }, [])  // eslint-disable-line react-hooks/exhaustive-deps

  const expand = async (entity) => { if (!entity) return; try { const e = await api.entity(entity.label, entity.key); setGraph((g) => (g ? { ...mergeEntity(g, e), stamp: g.stamp } : g)) } catch { /* no neighbours */ } }
  return (
    <section className="panel">
      <h2>Explore the graph</h2>
      <p className="muted small">One entity and its neighbours, a readable slice instead of the whole graph. Tap a node to expand it.</p>
      <div className="chips">{ENTITIES.map((e) => <button key={e.key} className={e.key === key ? 'chip on' : 'chip'} title={e.hint} onClick={() => { setLabel(e.label); setKey(e.key); load(e.label, e.key) }}>{e.label}: {e.key.split('/').pop().slice(0, 28)}</button>)}</div>
      <form className="row" onSubmit={(e) => { e.preventDefault(); load() }}>
        <label>Type <select value={label} onChange={(e) => setLabel(e.target.value)}>{LABELS.map((l) => <option key={l}>{l}</option>)}</select></label>
        <label>Key <input value={key} onChange={(e) => setKey(e.target.value)} aria-label="Key" /></label>
        <button className="btn" disabled={busy}>{busy ? 'Loading…' : 'Show'}</button>
      </form>
      {err && <p className="note" role="alert">{err}</p>}
      {graph && <><p className="muted small">
        {graph.nodes.length - 1} neighbour{graph.nodes.length === 2 ? '' : 's'} of <code>{shown.key}</code> shown{graph.truncated ? ': there are more. ' : ' (all of them). '}
        {graph.truncated && limit < MAX_LIMIT && <button className="btn small" disabled={busy} onClick={() => load(shown.label, shown.key, Math.min(limit + MORE_STEP, MAX_LIMIT))}>Show {Math.min(MORE_STEP, MAX_LIMIT - limit)} more</button>}{' '}
        {graph.truncated && limit >= MAX_LIMIT && 'One view shows at most 100; tap a neighbour to expand it instead. '}
        {' '}The map above shows the whole graph by type; this view is one entity and its neighbours.</p><GraphView key={graph.stamp} graph={graph} onExpand={expand} hint="Tap a node to pull in its neighbours." /></>}
    </section>
  )
}

export default function ExplorePage({ params }) {
  return <div className="explore"><PageIntro title="Explore the graph" what="This is the graph that everything else is built on. At the top, the whole graph drawn by type, with real counts. Below, two tools: look up who owned a folder in any release, and open any single thing (a feature, a team, a design decision) with everything it is connected to." read="On the map a circle is a kind of thing (bigger means more of them) and an arrow is a kind of relationship (thicker means more of them). In the entity view, each dot is a real node and each line a real relationship; tap a dot to pull in its neighbours." tryIt="Hover Person and PR on the map. Then pick pkg/controller/job and compare release 1.30 with the latest to see who gained or lost approval rights." /><SchemaMap /><OwnersPanel params={params} /><EntityPanel params={params} /></div>
}
