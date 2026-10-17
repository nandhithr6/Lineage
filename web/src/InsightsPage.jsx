import { useEffect, useState } from 'react'
import { api } from './api.js'

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
    <section>
      <h2>Ownership truth</h2>
      <p>The OWNERS file says who <em>should</em> approve changes. The review history shows who actually does. This page compares the two.</p>
      <label>Activity window{' '}
        <select value={months} onChange={(e) => setMonths(Number(e.target.value))}>
          {[6, 12, 24].map((m) => <option key={m} value={m}>{m} months</option>)}
        </select>
      </label>
      {error && <p role="alert" className="error">{error}</p>}
      {!data && !error && <p>Loading (the first load walks every folder and can take ~10 s)…</p>}
      {data && (<>
        <h3>Folders where listed owners went quiet</h3>
        <table>
          <thead><tr><th>Folder</th><th>Listed approvers</th><th>Active</th><th>Quiet</th><th>Other active people</th></tr></thead>
          <tbody>{data.ownership_gaps.map((r) => (
            <tr key={r.path}>
              <td><a href={`#/explore?path=${encodeURIComponent(r.path)}`}>{r.path}</a>{r.no_activity_data && <small> (no activity data)</small>}</td>
              <td>{r.listed_approvers}</td><td>{r.active_approvers}</td><td>{r.inactive_approvers} ({pct(r.inactive_share)})</td><td>{r.other_active_people}</td>
            </tr>))}</tbody>
        </table>
        <h3>Orphaned: other people work there, but no listed approver is active</h3>
        {data.orphaned.length === 0 ? <p>None found.</p> : (
          <ul>{data.orphaned.map((o) => <li key={o.path}><code>{o.path}</code>: {o.listed_approvers} listed, none active; active but not listed: {o.active_but_not_listed.join(', ') || 'nobody recorded'}</li>)}</ul>)}
        <details><summary>Dormant: {data.dormant.length} folders with listed approvers but no activity at all (often stable code, not a risk)</summary>
          <ul>{data.dormant.map((d) => <li key={d.path}><code>{d.path}</code>: {d.listed_approvers} listed</li>)}</ul></details>
        <h3>Most central reviewers (FalkorDB PageRank)</h3>
        {data.key_reviewers.length === 0 ? <p>No review network loaded yet.</p> : (
          <table>
            <thead><tr><th>#</th><th>Person</th><th>Authors relying on them</th><th>Reviews</th><th>Listed approver?</th></tr></thead>
            <tbody>{data.key_reviewers.map((k) => (
              <tr key={k.handle}><td>{k.rank}</td><td>{k.handle}</td><td>{k.authors_served}</td><td>{k.reviews}</td><td>{k.listed_approver ? 'yes' : 'no'}</td></tr>))}</tbody>
          </table>)}
        <ul className="notes">{data.notes.map((n) => <li key={n}><small>{n}</small></li>)}</ul>
      </>)}
    </section>
  )
}
