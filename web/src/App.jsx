import { useEffect, useState } from 'react'
import { api } from './api.js'
import HomePage from './HomePage.jsx'
import FindingsPage from './FindingsPage.jsx'
import ChatPage from './ChatPage.jsx'
import AboutPage from './AboutPage.jsx'
import ExplorePage from './ExplorePage.jsx'
import InsightsPage from './InsightsPage.jsx'
import ChangesPage from './ChangesPage.jsx'

const parse = () => {
  const [pathPart, query = ''] = window.location.hash.replace(/^#/, '').split('?')
  const [, page = 'home', id] = pathPart.split('/')
  return { page, id: id ? decodeURIComponent(id) : null, params: new URLSearchParams(query) }
}

export default function App() {
  const [route, setRoute] = useState(parse)
  const [health, setHealth] = useState(null)
  useEffect(() => {
    const on = () => setRoute(parse())
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])
  const [waking, setWaking] = useState(false)
  useEffect(() => {
    // A free host sleeps when idle and takes about a minute to wake: keep asking instead of showing a dead page.
    let alive = true, tries = 0
    const check = () => api.health().then((h) => { if (alive) { setHealth(h); setWaking(false) } })
      .catch((e) => { if (!alive) return; setWaking(true); setHealth({ status: 'degraded', detail: e.message }); if (++tries < 40) setTimeout(check, 4000) })
    check()
    return () => { alive = false }
  }, [])
  const tab = (key, label) => <a href={`#/${key}`} className={route.page === key ? 'tab active' : 'tab'} aria-current={route.page === key ? 'page' : undefined}>{label}</a>
  return (
    <div className="app">
      <header className="top">
        <div className="brand"><span className="logo" aria-hidden="true">L</span><div><strong>Lineage</strong><small>knows when its knowledge is out of date</small></div></div>
        <nav aria-label="Main">{tab('home', 'Home')}{tab('findings', 'Findings')}{tab('explore', 'Explore')}{tab('changes', 'What changed')}{tab('insights', 'Insights')}{tab('ask', 'Ask')}{tab('about', 'About the data')}</nav>
        <div className={`status ${health?.status === 'ok' ? 'ok' : 'bad'}`} title={health?.detail || ''}>
          {health ? (health.status === 'ok' ? 'API connected' : 'API unavailable') : 'connecting…'}
        </div>
      </header>
      {waking && <div className="panel note" role="status">The server is waking up (a free host sleeps when idle and needs about a minute). This page retries by itself.</div>}
      <main>
        {route.page === 'ask' ? <ChatPage llmConfigured={health?.llm_configured} />
          : route.page === 'explore' ? <ExplorePage params={route.params} />
          : route.page === 'changes' ? <ChangesPage />
          : route.page === 'insights' ? <InsightsPage />
          : route.page === 'about' ? <AboutPage />
          : route.page === 'findings' ? <FindingsPage selectedId={route.id} />
          : <HomePage />}
      </main>
      <footer>Built on public Kubernetes project data. Not an official Kubernetes or CNCF tool. Lineage only drafts; it never posts to Kubernetes.</footer>
    </div>
  )
}
