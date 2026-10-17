import { useRef, useState } from 'react'
import { api } from './api.js'
import { markdownBlocks, splitCitations } from './graph.js'

// Saved answers exist for these exact questions (they work without an LLM key or quota); anything else goes to the model.
const SAMPLES = ['Why is the Resource Quotas page stale?', "Is the 'stable since v1.27' label in the CronJob time zones section correct?", 'Who owned pkg/controller/job in 1.30 and who owns it now?', 'Who officially owns pkg/volume and who actually reviews it?', 'What design decision introduced LocalStorageCapacityIsolation?', 'Which docs pages look out of date?', 'What is the stage of the LocalStorageCapacityIsolatio gate?', 'Who will approve pkg/controller/job in release 1.45?']

const OUTCOME = {
  answered_with_path: ['Answered with a path', 'sev-minor', 'The answer rests on evidence found by following the graph.'],
  answered_from_memory: ['Answered from memory', 'sev-review', 'A note saved earlier; the graph is re-checked below.'],
  not_in_graph: ['Not in the graph', 'sev-review', 'The names exist, but nothing in the graph connects them to the question. That is not the same as "nothing is affected".'],
  unknown_entity: ['Unknown entity', 'sev-finding', 'The graph does not know a name in the question, so no lookup was run for it.'],
}

function OutcomeBadge({ outcome, memory }) {
  const o = OUTCOME[outcome]
  const learned = outcome === 'answered_from_memory' && memory?.[0]?.learned_at ? ` (learned ${memory[0].learned_at.slice(0, 10)})` : ''
  return o ? <span className={`badge ${o[1]}`} title={o[2]}>{o[0]}{learned}</span> : null
}

function MemoryCard({ notes, onForget }) {
  if (!notes?.length) return null
  return (
    <div className="memory">
      {notes.map((n) => (
        <div key={n.id} className="note">
          <strong>From memory</strong> <small>(learned {n.learned_at.slice(0, 10)} when you asked: “{n.question}”)</small>
          <p>{n.text}</p>
          <p className={`small reverify ${n.reverified.status}`}>Re-verified against the graph just now: <strong>{n.reverified.status}</strong>. {n.reverified.detail}.
            {n.sources.map((u, i) => <a key={i} className="src" href={u} target="_blank" rel="noreferrer"> source {i + 1} ↗</a>)}</p>
          <button className="btn small" onClick={() => onForget(n.id)}>Forget this note</button>
        </div>))}
    </div>
  )
}

function TracePanel({ trace, open }) {
  if (!trace?.length) return null
  return (
    <details open={open}>
      <summary>Trace: {trace.length} tool call{trace.length > 1 ? 's' : ''}</summary>
      <table className="trace-table">
        <thead><tr><th>tool</th><th>arguments</th><th>ms</th><th>rows</th><th>outcome</th></tr></thead>
        <tbody>{trace.map((t, j) => (
          <tr key={j}><td><code>{t.tool}</code></td><td><code>{JSON.stringify(t.params)}</code></td><td>{t.duration_ms ?? ''}</td><td>{t.evidence}</td>
            <td>{t.outcome ? t.outcome.replace(/_/g, ' ') : t.status}{(t.unresolved || []).map((u) => <div key={u.argument} className="small">unknown {u.kind} “{u.given}”{u.did_you_mean?.length ? `; did you mean: ${u.did_you_mean.join(', ')}?` : ''}</div>)}</td></tr>))}</tbody>
      </table>
    </details>
  )
}

function RetrievalBox({ rag }) {
  if (rag.error) return <div className="col rag"><h4>Plain retrieval</h4><p className="muted">{rag.error}</p></div>
  const known = new Set(rag.evidence.map((c) => c.id))
  return (
    <div className="col rag">
      <h4>Plain retrieval <small>({rag.retriever === 'tfidf' ? 'keyword (TF-IDF) search over the same text: this build has no embedding model' : 'vector search over the same text'}, no graph, no checks)</small></h4>
      {rag.cached && <span className="badge sev-minor" title={`Saved ${rag.cached_at}`}>Saved answer</span>}
      <p className="answer">{splitCitations(rag.answer).map((p, i) => p.cite ? <span key={i} className={known.has(p.cite) ? 'cite static' : 'cite unknown'}>{p.cite.replace('e', 'p')}</span> : <span key={i}>{p.text}</span>)}</p>
      <details open><summary>The 5 passages it retrieved</summary>
        <ol className="passages">{rag.evidence.map((c) => <li key={c.id}><small>p{c.id.slice(1)} · similarity {c.props.similarity}</small> <small>{c.from}</small>
          <div className="excerpt">{String(c.props.text).slice(0, 220)}…</div>{c.source_url && <a className="src" href={c.source_url} target="_blank" rel="noreferrer">source ↗</a>}</li>)}</ol></details>
      <p className="small muted">It never checked that the names in its answer exist, and it cannot follow a relationship between two passages.</p>
    </div>
  )
}

function Rich({ text, known, onCite }) {
  const blocks = markdownBlocks(text)
  const cell = (tok, k) => (tok.code ? <code key={k}>{tok.text}</code> : tok.bold ? <strong key={k}>{tok.text}</strong> : <span key={k}>{tok.text}</span>)
  const inline = (tokens, bk) => tokens.flatMap((tok, ti) => splitCitations(tok.text).map((p, pi) => (p.cite
    ? <button key={`${bk}-${ti}-${pi}`} className={known.has(p.cite) ? 'cite' : 'cite unknown'} onClick={() => onCite(p.cite)} aria-label={`Show evidence ${p.cite}`}>{p.cite}</button>
    : cell({ ...tok, text: p.text }, `${bk}-${ti}-${pi}`))))
  const items = []
  let list = []
  const flush = (k) => { if (list.length) { items.push(<ul key={`ul${k}`}>{list}</ul>); list = [] } }
  blocks.forEach((b, i) => { if (b.type === 'li') list.push(<li key={i}>{inline(b.tokens, i)}</li>); else { flush(i); items.push(<p key={i}>{inline(b.tokens, i)}</p>) } })
  flush('end')
  return <>{items}</>
}

function Answer({ msg, onCite }) {
  const known = new Set(msg.citations.map((c) => c.id))
  return <div className="answer"><Rich text={msg.text} known={known} onCite={onCite} /></div>
}

export default function ChatPage({ llmConfigured }) {
  const [msgs, setMsgs] = useState([])
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  const [open, setOpen] = useState(null)
  const [contrast, setContrast] = useState(false)
  const session = useRef(`s-${Math.random().toString(36).slice(2, 10)}`)

  async function send(text) {
    const question = (text ?? q).trim()
    if (question.length < 3 || busy) return
    setQ(''); setBusy(true); setOpen(null)
    setMsgs((m) => [...m, { role: 'user', text: question }])
    try {
      const [r, rag] = await Promise.all([api.ask(question, session.current, 'graph'), contrast ? api.ask(question, session.current, 'retrieval').catch((e) => ({ error: e.message })) : null])
      setMsgs((m) => [...m, { role: 'assistant', text: r.answer, status: r.status, citations: r.evidence, cited: r.citations.map((x) => x.id), path: r.path, trace: r.tool_trace, cached: r.cached, cachedAt: r.cached_at, outcome: r.outcome, validation: r.validation, rag, answerId: r.answer_id, canSave: r.can_save, memory: r.memory, saved: null }])
    } catch (e) {
      setMsgs((m) => [...m, { role: 'assistant', text: e.message + (e.status === 429 || e.status === 503 ? ' The example questions above have saved answers that still work.' : ''), status: e.status === 503 ? 'unconfigured' : 'error', citations: [], path: [], trace: [] }])
    } finally { setBusy(false) }
  }
  async function save(m, i) {
    try {
      const n = await api.saveNote(m.answerId, session.current)
      setMsgs((all) => all.map((x, j) => (j === i ? { ...x, saved: `Saved to memory (about ${n.about.slice(0, 4).join(', ')}). Ask about it again in this session and it will be shown first, re-checked against the graph.` } : x)))
    } catch (e) { setMsgs((all) => all.map((x, j) => (j === i ? { ...x, saved: `Not saved: ${e.message}` } : x))) }
  }
  async function forget(id, i) {
    try { await api.forgetNote(id, session.current); setMsgs((all) => all.map((x, j) => (j === i ? { ...x, memory: (x.memory || []).filter((n) => n.id !== id) } : x))) } catch { /* already gone */ }
  }
  const last = [...msgs].reverse().find((m) => m.role === 'assistant')
  const evidence = open && last?.citations.find((c) => c.id === open)

  return (
    <div className="split chat">
      <section className="pane">
        {llmConfigured === false && <div className="panel note" role="status">No language model is configured here, so only the example questions below (which have saved answers with their evidence) and the rest of the app work. For new questions add <code>GEMINI_API_KEY</code> (or <code>GROQ_API_KEY</code>) to <code>.env</code> and restart the API.</div>}
        {msgs.length === 0 && <div className="panel muted"><h2>Ask with proof</h2><p>Answers come only from graph queries. Every claim is followed by an evidence chip like <span className="cite">e3</span>; click it to see the source.</p>
          <div className="samples">{SAMPLES.map((s) => <button key={s} className="chip" onClick={() => send(s)}>{s}</button>)}</div></div>}
        <ol className="messages" aria-live="polite">{msgs.map((m, i) => (
          <li key={i} className={`msg ${m.role} ${m.status || ''}`}>
            {m.role === 'user' ? <p>{m.text}</p> : <>
              <div className={m.rag ? 'contrast' : undefined}><div className="col">
              {m.status === 'insufficient_evidence' && <span className="badge sev-review">Insufficient evidence</span>}
              {m.status === 'uncited' && <span className="badge sev-review">No citations: treat with caution</span>}
              <OutcomeBadge outcome={m.outcome} memory={m.memory} />
              {m.validation && m.validation.accepted === false && <span className="badge sev-minor" title="The model's draft named something the evidence does not contain, so it was withheld.">Draft withheld: not verifiable</span>}
              {m.cached && <span className="badge sev-minor" title={`Saved ${m.cachedAt}`}>Saved answer: same evidence as when it was generated</span>}
              <MemoryCard notes={m.memory} onForget={(id) => forget(id, i)} />
              <Answer msg={m} onCite={setOpen} />
              {m.canSave && !m.saved && <button className="btn small" onClick={() => save(m, i)} title="Only validated answers can be remembered; the note is private to this session and is re-checked against the graph before it is used again.">Remember this answer</button>}
              {m.saved && <p className="small muted">{m.saved}</p>}
              {m.validation && m.validation.accepted === false && <details className="small"><summary>Why the model's wording was withheld</summary><p>It named {m.validation.rejected.map((r) => `${r.kind} “${r.term}”`).join(', ')}, which is not in the evidence. The rows above come straight from the graph.</p><p className="muted">Draft: {m.validation.draft}</p></details>}
              {m.path?.length > 0 && <p className="path">{m.path.join('  →  ')}</p>}
              <TracePanel trace={m.trace} open={m.status === 'insufficient_evidence'} />
              {m.status === 'insufficient_evidence' && (!m.trace || m.trace.length === 0) && <p className="small muted">No lookup was made: nothing in the question matched a tool Lineage has.</p>}
              </div>{m.rag && <RetrievalBox rag={m.rag} />}</div>
            </>}
          </li>))}
          {busy && <li className="msg assistant muted">Looking things up…</li>}
        </ol>
        <form className="ask" onSubmit={(e) => { e.preventDefault(); send() }}>
          <label htmlFor="q" className="sr">Your question</label>
          <input id="q" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Ask about stale docs, owners or design decisions…" maxLength={1000} />
          <button className="btn" disabled={busy || q.trim().length < 3}>Ask</button>
          <label className="toggle"><input type="checkbox" checked={contrast} onChange={(e) => setContrast(e.target.checked)} /> Answer with plain retrieval too</label>
        </form>
      </section>
      <aside className="pane evidence" aria-label="Evidence">
        <h3>Evidence</h3>
        {!last?.citations.length ? <p className="muted">Evidence for the latest answer appears here.</p> : (
          <ul>{last.citations.filter((c) => !last.cited.length || last.cited.includes(c.id)).map((c) => (
            <li key={c.id} className={c.id === open ? 'ev open' : 'ev'}><button className="cite" onClick={() => setOpen(c.id)}>{c.id}</button>
              <div><strong>{c.type}</strong> <small>{c.from}{c.to ? ` → ${c.to}` : ''}</small>
                <div className="props">{Object.entries(c.props || {}).filter(([, v]) => v != null && v !== '').slice(0, 6).map(([k, v]) => <span key={k}>{k}: {String(typeof v === 'object' ? JSON.stringify(v) : v).slice(0, 80)}</span>)}</div>
                {c.source_url ? <a className="src" href={c.source_url} target="_blank" rel="noreferrer">open source ↗</a> : <small className="muted">derived from the graph</small>}</div></li>))}</ul>)}
        {evidence && <p className="note">Highlighted: {evidence.id}</p>}
      </aside>
    </div>
  )
}
