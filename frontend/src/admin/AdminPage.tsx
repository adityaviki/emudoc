// The Administration page: the workspace's owners and admins read here what the maintenance
// commands print in a shell (backend/app/api/admin.py), the jobs, the services and what the
// models cost, and from it queue failed documents again and switch a model provider off or on.
// Until 2026-11-13 it was the operator's area, a build of its own at /operator (S.3).

import { ArrowLeft } from 'lucide-react'
import { Link, NavLink, Navigate, Route, Routes, useLocation } from 'react-router'
import { isWithin } from '../api.ts'
import { useMe } from '../auth.ts'
import { ErrorBoundary } from '../components/ErrorBoundary.tsx'
import { Notice } from '../components/ui.tsx'
import { CostsView } from './Costs.tsx'
import { JobsView } from './Jobs.tsx'
import { ServicesView } from './Services.tsx'

const VIEWS = [
  { path: 'jobs', label: 'Jobs' },
  { path: 'services', label: 'Services' },
  { path: 'costs', label: 'Costs' },
]

export function AdminPage() {
  const me = useMe()
  const location = useLocation()
  const admin = isWithin('admin', me.role)
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-5xl px-6 py-10">
        <Link to="/organization" className="inline-flex items-center gap-1 text-[13px] text-muted hover:text-ink">
          <ArrowLeft className="size-3.5" />
          {me.organization.name}
        </Link>
        <div className="mt-3 mb-5">
          <h1 className="text-2xl font-semibold tracking-tight">Administration</h1>
          <p className="mt-1 text-[13px] text-muted">
            How the server that runs {me.organization.name} stands: its background work, its models and services, and what the models cost.
          </p>
        </div>
        {!admin ? (
          <Notice tone="info">Only the owners and admins of the workspace open its administration.</Notice>
        ) : (
          <>
            <nav aria-label="Administration" className="mb-6 flex flex-wrap gap-1 border-b border-line pb-2 text-[13px]">
              {VIEWS.map((view) => (
                <NavLink
                  key={view.path}
                  to={`/admin/${view.path}`}
                  className={({ isActive }) =>
                    `rounded-md px-2.5 py-1 transition ${isActive ? 'bg-sunken font-medium text-ink' : 'text-muted hover:text-ink'}`
                  }
                >
                  {view.label}
                </NavLink>
              ))}
            </nav>
            {/* A view that breaks leaves the page's links, from which another opens. */}
            <ErrorBoundary resetKey={location.pathname}>
              <Routes>
                <Route index element={<Navigate to="/admin/jobs" replace />} />
                <Route path="jobs" element={<JobsView />} />
                <Route path="services" element={<ServicesView />} />
                <Route path="costs" element={<CostsView />} />
                <Route path="*" element={<Notice tone="info">The Administration page has no such view.</Notice>} />
              </Routes>
            </ErrorBoundary>
          </>
        )}
      </div>
    </div>
  )
}
