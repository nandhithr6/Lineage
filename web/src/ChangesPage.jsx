import { useEffect, useState } from 'react'
import { api } from './api.js'
import PageIntro from './PageIntro.jsx'

const short = (sha) => (sha || '').slice(0, 8)

function FindingRow({ f, extra }) {
  return (
    <li>
      <strong>{f.page_title}</strong>: {f.section || 'page-level label'} <small>({f.claimed_stage} since v{f.claimed_since} · gate {f.gate})</small>
      <br /><small>{extra}</small>
    </li>
  )
}

export default function ChangesPage() {
  const [snaps, setSnaps] = useState(null)
  const [since, setSince] = useState('latest')
  const [d, setD] = useState(null)
  const [err, setErr] = useState(null)
  useEffect(() => { api.snapshots().then(setSnaps).catch((e) => setErr(e.message)) }, [])
  useEffect(() => { setD(null); setErr(null); api.changesSince(since).then(setD).catch((e) => setErr(e.message)) }, [since])
  const s = d?.summary
  return (
    <section className="page-card">
      <PageIntro title="What changed" what="A company brain must notice when the world moves. Lineage keeps earlier snapshots of the Kubernetes sources and compares them with the current one: which stale labels were fixed, which new ones appeared, which features changed stage and which owners changed." read="'Still open' means the label was already wrong at the earlier snapshot and nobody has fixed it. 'Moved' means the same label now sits on a different page, which is not a fix." tryIt="Compare with the oldest snapshot to see how long the Resource Quotas label has been out of date." />
      {snaps && (
        <p>Current snapshot <strong>{snaps.current.snapshot_date}</strong> ({snaps.current.age_days} days old).{' '}
          <label>Compare with{' '}
            <select value={since} onChange={(e) => setSince(e.target.value)}>
              <option value="latest">the newest earlier snapshot</option>
              {snaps.previous.map((p) => <option key={p.id} value={p.id}>{p.snapshot_date} ({p.age_days} days ago)</option>)}
            </select>
          </label></p>)}
      {err && <p role="alert" className="error">{err}</p>}
      {!d && !err && <p>Loading…</p>}
      {d && (<>
        <p className="small">From <strong>{d.from.snapshot_date}</strong> ({d.from.sources.map((x) => `${x.repo.split('/')[1]} ${short(x.commit)}`).join(', ')}) to <strong>{d.to.snapshot_date}</strong>.</p>
        <ul className="stats">
          {[['findings_resolved', 'stale labels fixed'], ['findings_new', 'new stale labels'], ['findings_moved', 'moved (not a fix)'], ['findings_persisting', 'still open'], ['gates_changed', 'features changed stage'],
            ['labels_changed', 'docs labels edited'], ['ownership_gained', 'ownership roles gained'], ['ownership_lost', 'ownership roles lost']].map(([k, label]) => <li key={k}><strong>{s[k]}</strong><span>{label}</span></li>)}
        </ul>
        <h3>Stale labels that are still open</h3>
        {d.findings.persisting.filter((f) => f.severity === 'finding').length === 0 ? <p>None.</p> : (
          <ul>{d.findings.persisting.filter((f) => f.severity === 'finding').map((f) => <FindingRow key={f.key} f={f} extra={`open since at least ${f.first_seen}: nobody has fixed this label in the docs`} />)}</ul>)}
        <h3>Fixed (resolved)</h3>
        {d.findings.resolved.length === 0 ? <p>No stale label was fixed in this period.</p> : <ul>{d.findings.resolved.map((f) => <FindingRow key={f.key} f={f} extra={`${f.severity === 'finding' ? 'Finding' : 'Review item'} resolved: ${f.why}`} />)}</ul>}
        <h3>New</h3>
        {d.findings.new.length === 0 ? <p>No new stale label appeared in this period.</p> : <ul>{d.findings.new.map((f) => <FindingRow key={f.key} f={f} extra={`${f.severity === 'finding' ? 'Finding' : 'Review item'} new: ${f.why}`} />)}</ul>}
        {d.findings.moved.length > 0 && (<><h3>Moved to another page (not a fix; the label is still wrong there)</h3>
          <ul>{d.findings.moved.map((m) => <li key={m.gate}>{m.gate} ({m.label}): {m.from_page} → {m.to_page}<br /><small>{m.why}</small></li>)}</ul></>)}
        <h3>Features that changed stage</h3>
        <ul>{d.gates.slice(0, 25).map((g) => <li key={g.gate}><code>{g.gate}</code>: {g.change}</li>)}</ul>
        {d.gates.length > 25 && <p className="small">…and {d.gates.length - 25} more.</p>}
        <h3>Ownership roles</h3>
        <p className="small">{d.ownership.note}</p>
        <ul>{d.ownership.gained.map((o, i) => <li key={'g' + i}>+ {o.who} became {o.role.replace('_OF', '').toLowerCase()} of <code>{o.path || '/'}</code> ({o.repo})</li>)}
          {d.ownership.lost.map((o, i) => <li key={'l' + i}>− {o.who} no longer {o.role.replace('_OF', '').toLowerCase()} of <code>{o.path || '/'}</code> ({o.repo})</li>)}</ul>
        <ul className="notes">{d.caveats.map((c) => <li key={c}><small>{c}</small></li>)}</ul>
      </>)}
    </section>
  )
}
