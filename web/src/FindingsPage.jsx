import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './api.js'
import GraphView from './GraphView.jsx'
import { KIND_LABEL, SEVERITY_LABEL, buildTraceGraph, diffLines, mergeEntity, traceSteps } from './graph.js'

const Badge = ({ severity, kind }) => <span className={`badge sev-${severity}`}>{KIND_LABEL[kind] || SEVERITY_LABEL[severity]}</span>
const Src = ({ url, children = 'source' }) => (url ? <a className="src" href={url} target="_blank" rel="noreferrer">{children} ↗</a> : null)

function Confidence({ value, method }) {
  const pct = Math.round((value || 0) * 100)
  return (
    <span className="conf" title={`link confidence ${value}`}>
      <span className="bar"><span style={{ width: `${pct}%` }} /></span>{pct}% <em>{method === 'manual' ? 'verified by hand' : method === 'gate_name' ? 'gate named in the label' : 'inferred'}</em>
    </span>
  )
}

function Timeline({ history }) {
  return (
    <ol className="timeline" aria-label="Stage history">
      {history.map((h, i) => (
        <li key={i} className={`stage st-${h.stage}`}><strong>{h.stage}</strong><span>{h.from === h.to ? h.from : `${h.from} → ${h.to}`}</span>{h.overlap && <small>overlap in source</small>}</li>
      ))}
    </ol>
  )
}

function SideBySide({ s }) {
  const claimed = s.claim.stage
  const last = s.gate.history[s.gate.history.length - 1]
  const verified = last ? last.stage : 'unknown'
  const claimedRows = s.gate.history.filter((h) => h.stage === claimed)
  const entered = claimedRows[0]?.from
  return (
    <section className="sbs" aria-label="Claimed versus verified">
      <div className="sbs-col claimed">
        <h3>The docs say</h3>
        <p className="big"><span className={`stage-chip st-${claimed}`}>{claimed}</span>{s.claim.since ? ` since v${s.claim.since}` : ''}</p>
        <code className="shortcode">{s.claim.shortcode}</code>
        <p><Src url={s.claim.source_url}>docs page at the pinned commit</Src></p>
      </div>
      <div className="sbs-col verified">
        <h3>Verified history of {s.gate.name}</h3>
        <p className="big"><span className={`stage-chip st-${last?.stage}`}>{verified}</span>{s.gate.removed ? ' · gate since removed, feature locked on' : ''}</p>
        <Timeline history={s.gate.history} />
        <p className="small">{claimedRows.length ? <>The gate entered <strong>{claimed}</strong> at v{entered}{s.claim.since && entered !== s.claim.since ? <> (the label says v{s.claim.since})</> : null}.</> : <>The gate never had a {claimed} stage in the sampled history.</>} <Src url={s.gate.source_url}>gate file at the pinned commit</Src></p>
      </div>
    </section>
  )
}

function Diff({ diff }) {
  return <pre className="diff" aria-label="Proposed change">{diffLines(diff).filter((l) => l.text).map((l, i) => <div key={i} className={`d-${l.kind}`}>{l.text}</div>)}</pre>
}

function TracePlayer({ steps, step, setStep }) {
  const timer = useRef(null)
  const stop = () => { if (timer.current) { clearInterval(timer.current); timer.current = null } }
  useEffect(() => stop, [])
  if (!steps.length) return null
  const play = () => {
    stop(); setStep(1); let i = 1
    timer.current = setInterval(() => { i += 1; setStep(i); if (i >= steps.length) stop() }, 1600)
  }
  const current = step == null ? null : steps[step - 1]
  return (
    <div className="player" aria-live="polite">
      <div className="player-bar">
        <button className="btn small" onClick={play}>▶ Replay the trace</button>
        {step != null && <button className="btn small ghost" onClick={() => { stop(); setStep(null) }}>Show everything</button>}
        <span className="muted small">{step == null ? 'Watch how Lineage got from the badge to the proof, one step at a time.' : `Step ${step} of ${steps.length}`}</span>
      </div>
      {current && <p className="player-caption" key={step}><strong>{current.title}.</strong> {current.text}</p>}
    </div>
  )
}

function Detail({ id, replay }) {
  const [d, setD] = useState(null)
  const [err, setErr] = useState(null)
  const [graph, setGraph] = useState({ nodes: [], edges: [] })
  const [fix, setFix] = useState(null)
  const [fixing, setFixing] = useState(false)
  const [step, setStep] = useState(replay)  // ?replay=N in the address opens the trace already at step N

  useEffect(() => {
    let live = true
    setD(null); setErr(null); setFix(null); setStep(replay)
    api.finding(id).then((x) => { if (live) { setD(x); setGraph(buildTraceGraph(x.sections)) } }).catch((e) => live && setErr(e.message))
    return () => { live = false }
  }, [id])

  const expand = useCallback(async (entity) => {
    if (!entity) return
    try { const e = await api.entity(entity.label, entity.key); setGraph((g) => mergeEntity(g, e)) } catch { /* node has no neighbors endpoint */ }
  }, [])

  if (err) return <div className="panel error" role="alert">{err}</div>
  if (!d) return <div className="panel muted">Loading the evidence trace…</div>
  const s = d.sections
  const canFix = s.finding.severity !== 'review'
  return (
    <article className="detail">
      <header>
        <Badge severity={s.finding.severity} kind={s.finding.kind} />
        <h2>{s.page.title}</h2>
        <p className="lead">{s.finding.message}</p>
        <a href={s.page.url} target="_blank" rel="noreferrer" className="src">Open the live docs page ↗</a>
      </header>

      <SideBySide s={s} />

      <section><h3>1 · The claim on the page</h3>
        <p>Section <strong>“{s.claim.heading}”</strong>, line {s.claim.line}</p>
        <code className="shortcode">{s.claim.shortcode}</code>
        <p>It says <strong>{s.claim.stage}{s.claim.since ? ` since v${s.claim.since}` : ''}</strong>. <Src url={s.claim.source_url}>pinned source</Src></p>
      </section>

      <section><h3>2 · Which feature gate it is about</h3>
        {s.link ? <>
          <p><strong>{s.gate.name}</strong> <Confidence value={s.link.link_confidence} method={s.link.method} /></p>
          {s.link.method === 'manual' && <p className="note">Verified by hand by {s.link.verified_by} on {s.link.verified_on}. The automatic linker alone scored {s.link.linker_confidence}.<br /><small>{s.link.reason}</small></p>}
          {(s.link.eliminated || []).length > 0 && <p className="note">Ruled out: {s.link.eliminated.map((e) => e.reason).join('; ')}.</p>}
          {s.finding.anchored_by === 'elimination' && <p className="note"><strong>Trusted without a hand check:</strong> the rule above removed rivals, and this gate is the clear winner (score at least 0.8, next candidate below 0.5).</p>}
          {s.link.method === 'linker' && s.link.signals && <p className="note">Evidence for the link: {Object.entries(s.link.signals).map(([k, v]) => `${k} ${typeof v === 'number' ? v.toFixed(2) : v}`).join(' · ')}</p>}
        </> : <p className="muted">Insufficient evidence: no link recorded.</p>}
      </section>

      <section><h3>3 · What the gate’s history says {s.gate.removed && <small className="pill">gate removed</small>}</h3>
        <Timeline history={s.gate.history} />
        <Src url={s.gate.source_url}>gate file</Src>
      </section>

      <section><h3>4 · The decision behind it</h3>
        {s.kep ? <>
          <p><strong>{s.kep.title}</strong> <small>({s.kep.id})</small></p>
          <p>Status {s.kep.status} · stage {s.kep.stage} · milestone {s.kep.milestone} · team <strong>{s.kep.sig}</strong> · linked by {s.kep.method} ({s.kep.confidence})</p>
          <Src url={s.kep.introduces_url}>design document</Src>
          {(s.kep.others || []).length > 0 && <p className="small">Also related: {s.kep.others.map((o) => o.id).join(', ')}</p>}
          <h4>Pull requests that implement it</h4>
          {(s.kep.prs || []).length === 0 ? <p className="muted small">No pull request in the loaded data (sig-storage, sig-node, sig-apps, last two years) links to this design document.</p> : (
            <ul className="prs">{s.kep.prs.map((x) => (
              <li key={x.number}><a href={x.url} target="_blank" rel="noreferrer">#{x.number}</a> {x.title} <small>merged {String(x.merged_at || '').slice(0, 10)} · inferred from {x.method.replace(/_/g, ' ')} ({Math.round(x.confidence * 100)}%): “{x.evidence}”</small>
                {x.issues.length > 0 && <ul>{x.issues.map((i) => <li key={i.id}>closes ticket <a href={i.url} target="_blank" rel="noreferrer">{i.id}</a> ({i.state.toLowerCase()}): {i.title}</li>)}</ul>}
              </li>))}</ul>)}
          <p className="small muted">{s.kep.prs_strong} pull request(s) link here at 70% or more; {s.kep.prs_weak} more only mention its feature gate. A mention is evidence of relation, not proof that the change implements the design.</p>
        </> : <p className="muted">Insufficient evidence: no design decision linked to this gate.</p>}
      </section>

      <section><h3>5 · Graph path</h3>
        <TracePlayer steps={traceSteps(s, graph)} step={step} setStep={setStep} />
        <GraphView key={d.key} graph={graph} onExpand={expand} reveal={step} />
      </section>

      <section><h3>6 · Suggested reviewers</h3>
        {['docs approver', 'domain expert'].map((g) => {
          const people = s.reviewers.filter((r) => r.group === g)
          return people.length ? (
            <div key={g}><h4>{g === 'docs approver' ? 'Docs approvers: can merge the fix' : 'Domain experts: can check the facts'}</h4>
              <ul className="reviewers">{people.map((r) => (
                <li key={r.handle}><a href={`https://github.com/${r.handle}`} target="_blank" rel="noreferrer">@{r.handle}</a> <small>score {r.score}</small>
                  <ul>{r.reasons.filter((x) => x.category !== 'connection').slice(0, 3).map((x, i) => <li key={i}>{x.reason} <Src url={x.source_url} /></li>)}
                    {r.reasons.filter((x) => x.category === 'connection').map((x, i) => <li key={'c' + i} className="connection">{x.reason} <Src url={x.source_url} /></li>)}</ul></li>))}</ul></div>
          ) : null
        })}
        <p className="muted small">Scores are Lineage’s own heuristic: each kind of role counts once. Emeritus approvers are never suggested.</p>
      </section>

      <section><h3>7 · Draft the fix</h3>
        {canFix ? <button className="btn" disabled={fixing} onClick={async () => { setFixing(true); try { setFix(await api.draftFix(id)) } catch (e) { setFix({ status: 'error', note: e.message }) } finally { setFixing(false) } }}>
          {fixing ? 'Drafting…' : 'Draft the corrected label'}</button>
          : <p className="note">This is a review item: a person should confirm the link first, so no change is drafted.</p>}
        {fix && (fix.status === 'ok' ? <><Diff diff={fix.diff} /><p>{fix.explanation}</p><p className="muted small">{fix.note}</p></> : <p className="note" role="status">{fix.note}</p>)}
      </section>
    </article>
  )
}

export default function FindingsPage({ selectedId, replay }) {
  const [list, setList] = useState(null)
  const [err, setErr] = useState(null)
  const [severity, setSeverity] = useState('')
  const [det, setDet] = useState(null)
  useEffect(() => { api.about().then((a) => setDet(a.detector)).catch(() => setDet(null)) }, [])
  useEffect(() => { setList(null); api.findings({ severity }).then(setList).catch((e) => setErr(e.message)) }, [severity])
  return (
    <div className="split">
      <aside className="list">
        <div className="filters">
          <label>Show <select value={severity} onChange={(e) => setSeverity(e.target.value)}>
            <option value="">everything</option><option value="finding">findings</option><option value="minor">minor</option><option value="review">needs a look</option></select></label>
          {list && <span className="muted">{list.total} items</span>}
        </div>
        {err && <div className="panel error" role="alert">{err}</div>}
        {!list && !err && <div className="panel muted">Loading…</div>}
        {list?.findings.length === 0 && <div className="panel muted">Nothing matches.</div>}
        <ul>{list?.findings.map((f) => (
          <li key={f.id}><a href={`#/findings/${f.id}`} className={f.id === selectedId ? 'item active' : 'item'}>
            <Badge severity={f.severity} kind={f.kind} /><strong>{f.page_title}</strong>
            <span>{f.section}</span><small>{f.claimed_stage}{f.claimed_since ? ` since v${f.claimed_since}` : ''} · gate {f.gate}</small></a></li>))}</ul>
      </aside>
      <section className="pane">{selectedId ? <Detail id={selectedId} replay={replay} /> : <div className="panel muted intro">
        <h2>Which docs are out of date?</h2>
        <p>Lineage compares the maturity badges on Kubernetes docs pages (“alpha since v1.8”) with the verified history of the feature each one is about, and proves every claim with a link to its source.</p>
        {list && <ul className="stats" aria-label="What Lineage checked">
          <li><strong>{list.counts.stale_stage ?? 0}</strong><span>docs claims that are out of date</span></li>
          <li><strong>{list.counts.possible_drift ?? 0}</strong><span>to review by a person</span></li>
          {det && <li><strong>{det['claims checked']}</strong><span>badges checked</span></li>}
          {det && <li><strong>{det.confident}</strong><span>linked to a feature with confidence</span></li>}</ul>}
        <details className="glossary"><summary>New to Kubernetes? Four words</summary>
          <dl><dt>SIG</dt><dd>a Special Interest Group: the team that owns an area (for example sig-storage).</dd>
            <dt>KEP</dt><dd>a Kubernetes Enhancement Proposal: the design-decision document behind a feature.</dd>
            <dt>Feature gate</dt><dd>an on/off switch for a feature, with a maturity stage: alpha, beta, then stable, and finally removed.</dd>
            <dt>OWNERS</dt><dd>a file that lists who may review and approve changes in a folder.</dd></dl></details>
        <p><span className="badge sev-finding">Stale stage</span> the link between the badge and its feature is confident, and the stage is proven wrong. <span className="badge sev-review">Possible drift</span> the link is not confident enough, so a person should check before anyone changes the docs.</p>
        <p>Pick an item on the left to see the trace: the claim, the feature gate it is linked to, the gate’s history, the design decision, the team, suggested reviewers and a drafted fix.</p></div>}</section>
    </div>
  )
}
