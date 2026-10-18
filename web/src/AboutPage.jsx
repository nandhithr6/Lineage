import { useEffect, useState } from 'react'
import { api } from './api.js'
import PageIntro from './PageIntro.jsx'

export default function AboutPage() {
  const [a, setA] = useState(null)
  const [err, setErr] = useState(null)
  useEffect(() => { api.about().then(setA).catch((e) => setErr(e.message)) }, [])
  if (err) return <div className="panel error" role="alert">{err}</div>
  if (!a) return <div className="panel muted">Loading…</div>
  const d = a.detector
  return (
    <div className="about">
      <PageIntro title="About the data" what="Where every fact in Lineage comes from, how fresh it is, and what the checker can and cannot do. Nothing here is private: it is all public Kubernetes project data." read="Each source repository is listed with the exact commit that was read, so any fact can be traced back to a file at that commit. The counts show how much the detector looked at and how much it could not judge." tryIt="Read 'Limits you should know about' at the bottom: it says plainly what Lineage does not know." />
      <p>Everything here is public Kubernetes project data, read-only. Snapshot date <strong>{a.snapshot_date}</strong>{a.age_days != null && <> ({a.age_days} day{a.age_days === 1 ? '' : 's'} old{a.age_days > 30 ? ': the upstream docs have probably moved on, see What changed' : ''})</>}. Each fact in the graph carries a commit-pinned link to its source.</p>
      <h3>Sources</h3>
      <table><thead><tr><th>Repository</th><th>Commit</th><th>License</th></tr></thead><tbody>
        {a.sources.map((s) => <tr key={s.repo}><td><a href={s.url} target="_blank" rel="noreferrer">{s.repo}</a></td><td><code>{s.commit.slice(0, 10)}</code></td><td>{s.license}</td></tr>)}
      </tbody></table>
      <p className="muted small">Copyright belongs to The Kubernetes Authors. Docs content is CC BY 4.0. Only public GitHub handles and names are used; emails and employers are dropped.</p>
      <h3>What the detector did</h3>
      <ul className="stats">{['claims checked', 'confident', 'possible only', 'unresolved', 'excluded (style guide)'].map((k) => <li key={k}><strong>{d[k] ?? 0}</strong><span>{k}</span></li>)}</ul>
      <p className="small">Findings need a link that is <em>anchored</em> (the gate is named in the section) or the only clear survivor of the since-version rule, or verified by hand. {a.manual_links} link{a.manual_links === 1 ? ' was' : 's were'} verified by hand and {a.manual_links === 1 ? 'is' : 'are'} marked as such.</p>
      <h3>Graph size</h3>
      <ul className="stats">{Object.entries(a.counts).filter(([k]) => /^[A-Z]/.test(k) && !k.includes('_')).map(([k, v]) => <li key={k}><strong>{v.toLocaleString()}</strong><span>{k}</span></li>)}</ul>
      <h3>Limits you should know about</h3>
      <ul>{a.limitations.map((l) => <li key={l}>{l}</li>)}</ul>
      <h3>How this was built</h3>
      <p className="small">Built with AI assistance (Claude Code) and disclosed as such. The team verifies the code, graph model and queries; the data model and decisions are logged in <code>DECISIONS.md</code>. FalkorDB is the only store: every answer is a graph traversal.</p>
    </div>
  )
}
