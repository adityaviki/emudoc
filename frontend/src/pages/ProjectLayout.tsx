import { useQuery } from '@tanstack/react-query'
import { Files, MessageSquare, Search, Table2 } from 'lucide-react'
import { Link, NavLink, Outlet, useLocation, useParams } from 'react-router'
import { api } from '../api.ts'
import { Loading, Notice } from '../components/ui.tsx'

const TABS = [
  { to: '.', label: 'Documents', icon: Files, end: true },
  { to: 'search', label: 'Search', icon: Search, end: false },
  { to: 'ask', label: 'Ask', icon: MessageSquare, end: false },
  { to: 'facts', label: 'Facts', icon: Table2, end: false },
]

export function ProjectLayout() {
  const projectId = Number(useParams().projectId)
  const project = useQuery({ queryKey: ['project', projectId], queryFn: () => api.project(projectId) })
  // The document viewer lives under the Documents tab.
  const inViewer = useLocation().pathname.includes('/documents/')

  if (project.isPending) return <Loading />
  if (project.isError) {
    return (
      <Notice tone="bad">
        {project.error.message}. <Link to="/" className="underline">Back to projects</Link>
      </Notice>
    )
  }

  return (
    <div>
      <div className="mb-4">
        <p className="text-xs text-muted">
          <Link to="/" className="hover:text-ink">
            Projects
          </Link>{' '}
          /
        </p>
        <h1 className="text-xl font-semibold">{project.data.name}</h1>
        {project.data.description && <p className="text-sm text-muted">{project.data.description}</p>}
      </div>
      <nav className="mb-5 flex gap-1 overflow-x-auto border-b border-line">
        {TABS.map(({ to, label, icon: Icon, end }) => (
          <NavLink
            key={label}
            to={to}
            end={end}
            className={({ isActive }) =>
              `flex shrink-0 items-center gap-1.5 border-b-2 px-3 py-2 text-sm font-medium transition ${
                isActive || (end && inViewer) ? 'border-accent text-ink' : 'border-transparent text-muted hover:text-ink'
              }`
            }
          >
            <Icon className="size-4" />
            {label}
          </NavLink>
        ))}
      </nav>
      <Outlet context={project.data} />
    </div>
  )
}
