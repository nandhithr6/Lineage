import { useRef, useState } from 'react'
import { api } from './api.js'
import { splitCitations } from './graph.js'

const SAMPLES = ['Which docs pages look out of date?', 'Why is the Resource Quotas page stale?', 'Who owned pkg/controller/job in 1.30 and who owns it now?', 'What design decision introduced LocalStorageCapacityIsolation?']

function Answer({ msg, onCite }) {
  const known = new Set(msg.citations.map((c) => c.id))
  return (
    <p className="answer">{splitCitations(msg.text).map((p, i) => p.cite
      ? <button key={i} className={known.has(p.cite) ? 'cite' : 'cite unknown'} onClick={() => onCite(p.cite)} aria-label={`Show evidence ${p.cite}`}>{p.cite}</button>
      : <span key={i}>{p.text}</span>)}</p>
  )
}

export default function ChatPage({ llmConfigured }) {
  const [msgs, setMsgs] = useState([])
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  const [open, setOpen] = useState(null)
  const session = useRef(`s-${Math.random().toString(36).slice(2, 10)}`)

  async function send(text) {
    const question = (text ?? q).trim()
    if (question.length < 3 || busy) return
    setQ(''); setBusy(true); setOpen(null)
    setMsgs((m) => [...m, { role: 'user', text: question }])
    try {
      const r = await api.ask(question, session.current)
      setMsgs((m) => [...m, { role: 'assistant', text: r.answer, status: r.status, citations: r.evidence, cited: r.citations.map((x) => x.id), path: r.path, trace: r.tool_trace }])
    } catch (e) {
      setMsgs((m) => [...m, { role: 'assistant', text: e.message, status: e.status === 503 ? 'unconfigured' : 'error', citations: [], path: [], trace: [] }])
    } finally { setBusy(false) }
  }
  const last = [...msgs].reverse().find((m) => m.role === 'assistant')
  const evidence = open && last?.citations.find((c) => c.id === open)

  return (
    <div className="split chat">
      <section className="pane">
        {llmConfigured === false && <div className="panel note" role="status">Chat needs an LLM key. Add <code>LLM_BASE_URL</code>, <code>LLM_API_KEY</code> and <code>LLM_MODEL</code> to <code>.env</code> and restart the API. Findings and the graph work without it.</div>}
        {msgs.length === 0 && <div className="panel muted"><h2>Ask with proof</h2><p>Answers come only from graph queries. Every claim is followed by an evidence chip like <span className="cite">e3</span>; click it to see the source.</p>
          <div className="samples">{SAMPLES.map((s) => <button key={s} className="chip" onClick={() => send(s)}>{s}</button>)}</div></div>}
        <ol className="messages" aria-live="polite">{msgs.map((m, i) => (
          <li key={i} className={`msg ${m.role} ${m.status || ''}`}>
            {m.role === 'user' ? <p>{m.text}</p> : <>
              {m.status === 'insufficient_evidence' && <span className="badge sev-review">Insufficient evidence</span>}
              {m.status === 'uncited' && <span className="badge sev-review">No citations: treat with caution</span>}
              <Answer msg={m} onCite={setOpen} />
              {m.path?.length > 0 && <p className="path">{m.path.join('  →  ')}</p>}
              {m.trace?.length > 0 && <details><summary>{m.trace.length} lookup{m.trace.length > 1 ? 's' : ''} used</summary><ul>{m.trace.map((t, j) => <li key={j}><code>{t.tool}({Object.values(t.params).join(', ')})</code> → {t.evidence} items ({t.status})</li>)}</ul></details>}
            </>}
          </li>))}
          {busy && <li className="msg assistant muted">Looking things up…</li>}
        </ol>
        <form className="ask" onSubmit={(e) => { e.preventDefault(); send() }}>
          <label htmlFor="q" className="sr">Your question</label>
          <input id="q" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Ask about stale docs, owners or design decisions…" maxLength={1000} />
          <button className="btn" disabled={busy || q.trim().length < 3}>Ask</button>
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
