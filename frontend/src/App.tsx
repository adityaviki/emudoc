import { useQuery } from '@tanstack/react-query'
import { Moon, Sun } from 'lucide-react'
import { useState } from 'react'
import { Link, Route, Routes } from 'react-router'
import { api } from './api.ts'
import { IconButton } from './components/ui.tsx'
import { AskTab } from './pages/AskTab.tsx'
import { DocumentPage } from './pages/DocumentPage.tsx'
import { DocumentsTab } from './pages/DocumentsTab.tsx'
import { ProjectLayout } from './pages/ProjectLayout.tsx'
import { ProjectsPage } from './pages/ProjectsPage.tsx'
import { SearchTab } from './pages/SearchTab.tsx'

function App() {
  return (
    <div className="flex min-h-screen flex-col">
      <header className="border-b border-line bg-surface">
        <div className="mx-auto flex h-12 max-w-7xl items-center justify-between px-6">
          <Link to="/" className="flex items-center gap-2 font-semibold tracking-tight">
            <img src="/favicon.svg" alt="" className="size-5" />
            Paperless
          </Link>
          <div className="flex items-center gap-4">
            <ServiceStatus />
            <ThemeToggle />
          </div>
        </div>
      </header>
      <main className="mx-auto w-full max-w-7xl flex-1 px-6 py-6">
        <Routes>
          <Route index element={<ProjectsPage />} />
          <Route path="projects/:projectId" element={<ProjectLayout />}>
            <Route index element={<DocumentsTab />} />
            <Route path="search" element={<SearchTab />} />
            <Route path="ask" element={<AskTab />} />
            <Route path="ask/:chatId" element={<AskTab />} />
            <Route path="documents/:documentId" element={<DocumentPage />} />
          </Route>
          <Route path="*" element={<p className="py-16 text-center text-muted">Page not found.</p>} />
        </Routes>
      </main>
    </div>
  )
}

/** Switches between the default light theme and dark, remembering the choice in this browser. */
function ThemeToggle() {
  const [dark, setDark] = useState(() => document.documentElement.dataset.theme === 'dark')

  function toggle() {
    const next = !dark
    setDark(next)
    if (next) document.documentElement.dataset.theme = 'dark'
    else delete document.documentElement.dataset.theme
    try {
      localStorage.setItem('theme', next ? 'dark' : 'light')
    } catch {
      // Storage can be blocked; the theme still applies for this visit.
    }
  }

  return (
    <IconButton label={dark ? 'Switch to light theme' : 'Switch to dark theme'} onClick={toggle}>
      {dark ? <Sun className="size-4" /> : <Moon className="size-4" />}
    </IconButton>
  )
}

/** Shows whether the two optional services, the AI model and embeddings, are usable. */
function ServiceStatus() {
  const { data } = useQuery({ queryKey: ['health'], queryFn: api.health, refetchInterval: 60_000 })
  if (!data) return null
  const items = [
    {
      label: 'AI model',
      ok: data.llm.available,
      detail: data.llm.available
        ? `Answers with ${data.llm.agent_model}, indexes with ${data.llm.index_model}`
        : `${data.llm.error ?? 'Not configured.'} Then restart the backend to get answers, summaries and facts.`,
    },
    {
      label: 'Embeddings',
      ok: data.embeddings.ok,
      detail: data.embeddings.ok
        ? `Semantic search with ${data.embeddings.model}`
        : `${data.embeddings.error}. Search falls back to keywords.`,
    },
  ]
  return (
    <div className="flex items-center gap-4 text-xs text-muted">
      {items.map((item) => (
        <span key={item.label} title={item.detail} className="flex cursor-help items-center gap-1.5">
          <span className={`size-2 rounded-full ${item.ok ? 'bg-good' : 'bg-warn'}`} />
          {item.label}
          {!item.ok && <span className="text-warn">off</span>}
        </span>
      ))}
    </div>
  )
}

export default App
