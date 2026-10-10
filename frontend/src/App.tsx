import { useQuery } from '@tanstack/react-query'
import { Check, ChevronDown, Moon, Sun } from 'lucide-react'
import { useRef, useState } from 'react'
import { Link, Route, Routes, useMatch, useNavigate } from 'react-router'
import { api } from './api.ts'
import { Floating, Menu } from './components/Menu.tsx'
import type { MenuItem } from './components/Menu.tsx'
import { IconButton } from './components/ui.tsx'
import { ProjectsPage } from './pages/ProjectsPage.tsx'
import { Workspace } from './pages/Workspace.tsx'

function App() {
  return (
    <div className="flex h-dvh flex-col overflow-hidden">
      <header className="shrink-0 border-b border-line bg-surface">
        <div className="flex h-12 items-center justify-between gap-4 px-4">
          <div className="flex min-w-0 items-center gap-1.5">
            <Link to="/" className="flex shrink-0 items-center gap-2 rounded-md px-1 text-[15px] font-semibold tracking-tight">
              <img src="/favicon.svg" alt="" className="size-5" />
              Emudoc
            </Link>
            <ProjectSwitcher />
          </div>
          <div className="flex shrink-0 items-center gap-3">
            <ServiceStatus />
            <ThemeToggle />
          </div>
        </div>
      </header>
      <main className="min-h-0 flex-1">
        <Routes>
          <Route index element={<ProjectsPage />} />
          <Route path="projects/:projectId/*" element={<Workspace />} />
          <Route path="*" element={<p className="py-16 text-center text-muted">Page not found.</p>} />
        </Routes>
      </main>
    </div>
  )
}

/** Inside a project: its name, and a menu to move to another project. */
function ProjectSwitcher() {
  const match = useMatch('/projects/:projectId/*')
  const navigate = useNavigate()
  const projects = useQuery({ queryKey: ['projects'], queryFn: api.projects, enabled: match !== null })
  // Where the menu opens: set from the button's position when it is clicked.
  const [anchor, setAnchor] = useState<DOMRect | null>(null)
  const button = useRef<HTMLButtonElement>(null)
  if (!match) return null

  const currentId = Number(match.params.projectId)
  const current = projects.data?.find((project) => project.id === currentId)
  const items: MenuItem[] = [
    ...(projects.data ?? []).map(
      (project): MenuItem => ({
        label: project.name,
        icon: project.id === currentId ? Check : undefined,
        onSelect: () => navigate(`/projects/${project.id}`),
      }),
    ),
    'separator',
    { label: 'All projects', onSelect: () => navigate('/') },
  ]

  return (
    <>
      <span className="text-line select-none" aria-hidden>
        <svg width="12" height="20" viewBox="0 0 12 20" fill="none" stroke="currentColor" strokeWidth="1.5">
          <path d="M9 2 3 18" />
        </svg>
      </span>
      <button
        ref={button}
        type="button"
        aria-haspopup="menu"
        aria-expanded={anchor !== null}
        onClick={(event) => setAnchor(anchor ? null : event.currentTarget.getBoundingClientRect())}
        className="flex h-7 min-w-0 items-center gap-1 rounded-md px-2 text-[13px] font-medium transition hover:bg-sunken"
      >
        <span className="truncate">{current?.name ?? '…'}</span>
        <ChevronDown className="size-3.5 shrink-0 text-muted" />
      </button>
      {anchor && (
        <Floating anchor={anchor} onClose={() => setAnchor(null)} ignore={button}>
          <Menu items={items} onClose={() => setAnchor(null)} />
        </Floating>
      )}
    </>
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
    <div className="hidden items-center gap-1 text-xs text-muted sm:flex">
      {items.map((item) => (
        <span
          key={item.label}
          title={item.detail}
          className="flex h-7 cursor-help items-center gap-1.5 rounded-md px-2 transition hover:bg-sunken hover:text-ink"
        >
          <span className={`size-1.5 rounded-full ${item.ok ? 'bg-good' : 'bg-warn'}`} />
          {item.label}
          {!item.ok && <span className="text-warn">off</span>}
        </span>
      ))}
    </div>
  )
}

export default App
