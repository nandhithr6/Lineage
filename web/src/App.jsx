import { useEffect, useState } from 'react'
import { api } from './api.js'
import FindingsPage from './FindingsPage.jsx'
import ChatPage from './ChatPage.jsx'
import AboutPage from './AboutPage.jsx'
import ExplorePage from './ExplorePage.jsx'

const parse = () => {
  const [pathPart, query = ''] = window.location.hash.replace(/^#/, '').split('?')
  const [, page = 'findings', id] = pathPart.split('/')
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
  useEffect(() => { api.health().then(setHealth).catch((e) => setHealth({ status: 'degraded', detail: e.message })) }, [])
  const tab = (key, label) => <a href={`#/${key}`} className={route.page === key ? 'tab active' : 'tab'} aria-current={route.page === key ? 'page' : undefined}>{label}</a>
  return (
    <div className="app">
      <header className="top">
        <div className="brand"><span className="logo" aria-hidden="true">L</span><div><strong>Lineage</strong><small>knows when its knowledge is out of date</small></div></div>
        <nav aria-label="Main">{tab('findings', 'Findings')}{tab('explore', 'Explore')}{tab('ask', 'Ask')}{tab('about', 'About the data')}</nav>
        <div className={`status ${health?.status === 'ok' ? 'ok' : 'bad'}`} title={health?.detail || ''}>
          {health ? (health.status === 'ok' ? 'API connected' : 'API unavailable') : 'connecting…'}
        </div>
      </header>
      <main>
        {route.page === 'ask' ? <ChatPage llmConfigured={health?.llm_configured} />
          : route.page === 'explore' ? <ExplorePage params={route.params} />
          : route.page === 'about' ? <AboutPage />
          : <FindingsPage selectedId={route.id} />}
      </main>
      <footer>Built on public Kubernetes project data. Not an official Kubernetes or CNCF tool. Lineage only drafts; it never posts to Kubernetes.</footer>
    </div>
  )
}
