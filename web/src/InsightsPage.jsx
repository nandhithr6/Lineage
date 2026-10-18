import { useEffect, useState } from 'react'
import { api } from './api.js'
import PageIntro from './PageIntro.jsx'

const pct = (x) => `${Math.round(x * 100)}%`

export default function InsightsPage() {
  const [months, setMonths] = useState(12)
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  useEffect(() => {
    setData(null); setError(null)
    api.insights(months).then(setData).catch((e) => setError(e.message))
  }, [months])
  return (
    <section className="page-card">
      <PageIntro title="Ownership truth" what="Kubernetes keeps a file (called OWNERS) that lists who is supposed to approve changes in each folder of the code. The review history shows who actually does the work. This page compares the two, so you can see where the official list and reality have drifted apart." read="A full orange bar means every listed approver for that folder has gone quiet. 'Orphaned' folders have other people working there but no active listed approver. The central-reviewers table uses FalkorDB's PageRank algorithm: it shows who many different authors depend on, which is not the same as skill or ownership." tryIt="Click a folder name to open its owners in Explore." />
      <label>Activity window{' '}
        <select value={months} onChange={(e) => setMonths(Number(e.target.value))}>
          {[6, 12, 24].map((m) => <option key={m} value={m}>{m} months</option>)}
        </select>
      </label>
      {error && <p role="alert" className="error">{error}</p>}
      {!data && !error && <p>Loading…</p>}
      {data && (<>
        <h3>Folders where listed owners went quiet</h3>
        <p className="muted small">How to read it: of the people the OWNERS file lists as approvers, how many have done no review or pull-request work in the window. A full bar means every listed approver went quiet.</p>
        <table>
          <thead><tr><th>Folder</th><th>Listed approvers</th><th>Active</th><th>Quiet</th><th>Share quiet</th><th>Other active people</th></tr></thead>
          <tbody>{data.ownership_gaps.map((r) => (
            <tr key={r.path}>
              <td><a href={`#/explore?path=${encodeURIComponent(r.path)}`}>{r.path}</a>{r.no_activity_data && <small> (no activity data)</small>}</td>
              <td>{r.listed_approvers}</td><td>{r.active_approvers}</td><td>{r.inactive_approvers} ({pct(r.inactive_share)})</td><td><span className="bar warn" title={pct(r.inactive_share)}><span style={{ width: pct(r.inactive_share) }} /></span></td><td>{r.other_active_people}</td>
            </tr>))}</tbody>
        </table>
        <h3>Orphaned: other people work there, but no listed approver is active</h3>
        {data.orphaned.length === 0 ? <p>None found.</p> : (
          <ul>{data.orphaned.map((o) => <li key={o.path}><code>{o.path}</code>: {o.listed_approvers} listed, none active; active but not listed: {o.active_but_not_listed.join(', ') || 'nobody recorded'}</li>)}</ul>)}
        <details><summary>Dormant: {data.dormant.length} folders with listed approvers but no activity at all (often stable code, not a risk)</summary>
          <ul>{data.dormant.map((d) => <li key={d.path}><code>{d.path}</code>: {d.listed_approvers} listed</li>)}</ul></details>
        <h3>Most central reviewers (FalkorDB PageRank)</h3>
        <p className="muted small">How to read it: PageRank scores position in the review network, so someone many different authors depend on ranks high. It measures reliance, not skill or ownership.</p>
        {data.key_reviewers.length === 0 ? <p>No review network loaded yet.</p> : (
          <table>
            <thead><tr><th>#</th><th>Person</th><th>Authors relying on them</th><th></th><th>Reviews</th><th>Listed approver?</th></tr></thead>
            <tbody>{data.key_reviewers.map((k) => (
              <tr key={k.handle}><td>{k.rank}</td><td>{k.handle}</td><td>{k.authors_served}</td><td><span className="bar" title={`${k.authors_served} authors`}><span style={{ width: `${Math.round(100 * k.authors_served / Math.max(...data.key_reviewers.map((x) => x.authors_served), 1))}%` }} /></span></td><td>{k.reviews}</td><td>{k.listed_approver ? 'yes' : 'no'}</td></tr>))}</tbody>
          </table>)}
        <ul className="notes">{data.notes.map((n) => <li key={n}><small>{n}</small></li>)}</ul>
      </>)}
    </section>
  )
}
