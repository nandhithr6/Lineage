const BASE = import.meta.env.VITE_API_URL ?? '/api'

async function request(path, options) {
  let res
  try {
    res = await fetch(BASE + path, options)
  } catch {
    throw new Error('Cannot reach the Lineage API. Is it running (uvicorn lineage.api.main:app)?')
  }
  if (!res.ok) {
    let detail = res.statusText
    try { detail = (await res.json()).detail || detail } catch { /* not JSON */ }
    const err = new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
    err.status = res.status
    throw err
  }
  return res.json()
}

const post = (path, body) => request(path, { method: 'POST', headers: { 'content-type': 'application/json' }, body: body ? JSON.stringify(body) : undefined })

export const api = {
  health: () => request('/health'),
  findings: (params = {}) => request('/findings?' + new URLSearchParams(Object.entries(params).filter(([, v]) => v !== '' && v != null))),
  finding: (id) => request(`/findings/${encodeURIComponent(id)}`),
  draftFix: (id) => post(`/findings/${encodeURIComponent(id)}/fix`),
  ask: (question, sessionId, mode = 'graph') => post('/ask', { question, session_id: sessionId, mode }),
  entity: (label, key, limit = 40) => request(`/entity/${label}/${encodeURIComponent(key)}?limit=${limit}`),
  insights: (months = 12) => request(`/insights?months=${months}`),
  snapshots: () => request('/snapshots'),
  changesSince: (since = 'latest') => request(`/changes-since?since=${encodeURIComponent(since)}`),
  saveNote: (answerId, sessionId) => post('/notes', { answer_id: answerId, session_id: sessionId }),
  forgetNote: (noteId, sessionId) => request(`/notes/${encodeURIComponent(noteId)}?session_id=${encodeURIComponent(sessionId)}`, { method: 'DELETE' }),
  about: () => request('/about-data'),
  schema: () => request('/schema'),
  owners: (repo, path, asOf) => request(`/owners/${repo}/${path.split('/').map(encodeURIComponent).join('/')}?as_of=${encodeURIComponent(asOf || 'latest')}`),
  experts: (path, months = 12) => request(`/experts/kubernetes/${path.split('/').map(encodeURIComponent).join('/')}?months=${months}`),
  changes: (entity, from, to, repo = 'kubernetes') => request('/changes?' + new URLSearchParams({ entity, from, to, repo })),
}
