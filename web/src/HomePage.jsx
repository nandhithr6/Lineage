import { useEffect, useState } from 'react'
import { api } from './api.js'

const fmt = (n) => (n == null ? '–' : n.toLocaleString('en-US'))

const STEPS = [
  ['1', 'Find', 'Spot a docs page whose "Beta since v1.x" badge no longer matches reality, by asking, or from the drift detector, which compares every badge with the verified history.'],
  ['2', 'Trace', 'Follow the graph: docs section, feature gate, its stage in every release, the design decision (KEP) behind it, the pull requests, the owners.'],
  ['3', 'Explain', 'Show what the page claims next to what is verified, with every source linked at the exact commit it was read from.'],
  ['4', 'Repair', 'Draft the corrected line and suggest who should review it. Lineage only drafts; it never posts to Kubernetes.'],
]
const WHY = [
  ['Every answer is a traversal', 'Answers are built by walking relationships in FalkorDB, not by guessing from similar text. Take the graph away and nothing works.'],
  ['Every fact carries its proof', 'Each relationship records where it came from (a link pinned to a commit), how it was found and how sure we are. Answers cite evidence, and an answer that names something not in the evidence is thrown out.'],
  ['Knowledge has a date', 'Facts that change, such as who owns a folder or which stage a feature is in, keep when they were true. Nothing is deleted, so you can ask what was true in release 1.30.'],
]
const PAGES = [
  ['findings', 'Findings', 'The stale docs labels, each traced to its proof, with a drafted fix and reviewers.'],
  ['ask', 'Ask', 'Questions in plain English. Compare the graph answer with plain text retrieval.'],
  ['explore', 'Explore', 'The whole graph as a map, who owned a folder and when, and any entity with its neighbours.'],
  ['insights', 'Insights', 'Official versus actual ownership: folders whose listed approvers went quiet.'],
  ['changes', 'What changed', 'Compare with an earlier snapshot: stale labels fixed, new ones, features that changed stage.'],
  ['about', 'About the data', 'Sources, commit hashes, counts, how the detector works and what it cannot do.'],
]

export default function HomePage() {
  const [about, setAbout] = useState(null)
  const [list, setList] = useState(null)
  useEffect(() => { api.about().then(setAbout).catch(() => {}); api.findings({}).then(setList).catch(() => {}) }, [])
  const c = about?.counts || {}
  return (
    <div className="home">
      <section className="hero">
        <span className="eyebrow">Built for the FalkorDB Graph Hacks · Company Brain</span>
        <h1>A company brain that knows when its knowledge is out of date, and proves every answer.</h1>
        <p className="lede">We treated the Kubernetes open-source project as a company of about 3,000 people: an org chart, ownership maps, design decisions, release history, and documentation that quietly drifts. Lineage reads all of it into one graph and checks the docs against the facts.</p>
        <div className="cta">
          <a className="btn" href="#/findings">See the stale docs</a>
          <a className="btn ghost" href="#/ask">Ask a question</a>
          <a className="btn ghost" href="#/explore">Explore the graph</a>
        </div>
      </section>

      <ul className="stats big" aria-label="What is in the graph">
        <li><strong>{fmt(list?.counts?.stale_stage)}</strong><span>docs claims proven out of date</span></li>
        <li><strong>{fmt(about?.detector?.['claims checked'])}</strong><span>docs badges checked</span></li>
        <li><strong>{fmt(c.KEP)}</strong><span>design decisions (KEPs)</span></li>
        <li><strong>{fmt(c.PR)}</strong><span>pull requests</span></li>
        <li><strong>{fmt(c.Person)}</strong><span>people</span></li>
      </ul>

      <section className="split-2">
        <div>
          <h2>The problem</h2>
          <p>Kubernetes docs carry badges such as <em>“Alpha since v1.8”</em>. They are typed by hand, so when a feature graduates the badge is easy to forget. Readers then trust a label that stopped being true years ago, and nobody is told.</p>
          <p className="muted">Finding these means reading release history, design documents and docs pages together: a job for a graph, because the answer is a path through relationships, not a single document.</p>
        </div>
        <aside className="example" aria-label="A real example">
          <span className="badge sev-finding">Real example from the data</span>
          <h3>Resource Quotas, “Quota for local ephemeral storage”</h3>
          <div className="vs">
            <div className="claimed"><small>The docs say</small><strong>Alpha since v1.8</strong></div>
            <span aria-hidden="true">→</span>
            <div className="verified"><small>Verified history</small><strong>Stable since 1.25, gate since removed</strong></div>
          </div>
          <p className="small muted">Lineage was never told which feature the badge is about. It worked that out from the page and the design decision, and ruled out the look-alike gates that did not exist yet in v1.8.</p>
          <a href="#/findings">Open the full trace →</a>
        </aside>
      </section>

      <section>
        <h2>How it works</h2>
        <ol className="steps">{STEPS.map(([n, t, d]) => <li key={n}><span className="num">{n}</span><h3>{t}</h3><p>{d}</p></li>)}</ol>
      </section>

      <section>
        <h2>Why a graph</h2>
        <div className="cards">{WHY.map(([t, d]) => <article key={t} className="card"><h3>{t}</h3><p>{d}</p></article>)}</div>
      </section>

      <section>
        <h2>Where to go</h2>
        <div className="cards three">{PAGES.map(([k, t, d]) => <a key={k} className="card link" href={`#/${k}`}><h3>{t} <span aria-hidden="true">→</span></h3><p>{d}</p></a>)}</div>
      </section>

      <section className="honest">
        <h2>What it is not</h2>
        <ul>
          <li>It is <strong>not an official Kubernetes tool</strong>. It reads public data only: the Kubernetes repositories on GitHub. No private or personal data, and email addresses are removed.</li>
          <li>It <strong>drafts and never posts</strong>. A person reviews every suggested fix.</li>
          <li>A link between a badge and a feature is inferred, so each one has a confidence. Low-confidence links are shown as “possible”, never as findings.</li>
        </ul>
        <details className="glossary"><summary>New to Kubernetes? Four words</summary>
          <dl><dt>SIG</dt><dd>a Special Interest Group: the team that owns an area (for example sig-storage).</dd>
            <dt>KEP</dt><dd>a Kubernetes Enhancement Proposal: the design-decision document behind a feature.</dd>
            <dt>Feature gate</dt><dd>an on/off switch for a feature, with a maturity stage: alpha, beta, then stable, and finally removed.</dd>
            <dt>OWNERS</dt><dd>a file that lists who may review and approve changes in a folder.</dd></dl></details>
      </section>
    </div>
  )
}
