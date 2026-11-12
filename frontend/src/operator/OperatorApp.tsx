// The operator's area: who opened it and its views, each what a maintenance command prints
// (backend/app/api/operator.py), from which an operator also turns an organisation's feature
// flags on or off, queues failed documents again and switches a model provider off or on.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft } from 'lucide-react'
import { NavLink, Navigate, Route, Routes, useLocation } from 'react-router'
import { ApiError } from '../api.ts'
import { ErrorBoundary } from '../components/ErrorBoundary.tsx'
import { Chip, Loading, Notice } from '../components/ui.tsx'
import { formatDateTime } from '../format.ts'
import { operatorApi } from './api.ts'
import type { Operator } from './api.ts'
import { CostsPage } from './Costs.tsx'
import { FlagsPage } from './Flags.tsx'
import { JobsPage } from './Jobs.tsx'
import { OrganizationPage, OrganizationsPage } from './Organizations.tsx'
import { ServicesPage } from './Services.tsx'
import { UsersPage } from './Users.tsx'
import { release } from './words.ts'

const VIEWS = [
  { path: 'organizations', label: 'Organisations' },
  { path: 'users', label: 'Users' },
  { path: 'costs', label: 'Costs' },
  { path: 'jobs', label: 'Jobs' },
  { path: 'services', label: 'Services' },
  { path: 'flags', label: 'Flags' },
]

export function OperatorApp() {
  const location = useLocation()
  const me = useQuery({ queryKey: ['operator', 'me'], queryFn: operatorApi.me, staleTime: Infinity })
  if (me.isPending) return <Loading />
  if (me.isError) return <Refused error={me.error} />
  return (
    <div className="min-h-dvh bg-page text-ink">
      <Header operator={me.data} />
      {me.data.viewing && <Viewing operator={me.data} />}
      <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
        {/* A view that breaks leaves the header, from which another opens. */}
        <ErrorBoundary resetKey={location.pathname}>
          <Routes>
            <Route path="/operator" element={<Navigate to="/operator/organizations" replace />} />
            <Route path="/operator/organizations" element={<OrganizationsPage currency={me.data.currency} />} />
            <Route path="/operator/organizations/:id" element={<OrganizationPage currency={me.data.currency} />} />
            <Route path="/operator/users" element={<UsersPage />} />
            <Route path="/operator/costs" element={<CostsPage />} />
            <Route path="/operator/jobs" element={<JobsPage />} />
            <Route path="/operator/services" element={<ServicesPage />} />
            <Route path="/operator/flags" element={<FlagsPage />} />
            <Route path="*" element={<Notice tone="info">The operator's area has no such page.</Notice>} />
          </Routes>
        </ErrorBoundary>
      </main>
    </div>
  )
}

function Header({ operator }: { operator: Operator }) {
  return (
    <header className="border-b border-line bg-surface">
      <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-5 gap-y-2 px-4 py-2.5 sm:px-6">
        <div className="flex items-center gap-2">
          <span className="font-semibold">Emudoc</span>
          <Chip tone="accent">operator</Chip>
        </div>
        <nav aria-label="Views" className="flex flex-wrap gap-1 text-[13px]">
          {VIEWS.map((view) => (
            <NavLink
              key={view.path}
              to={`/operator/${view.path}`}
              className={({ isActive }) =>
                `rounded-md px-2.5 py-1 transition ${isActive ? 'bg-sunken font-medium text-ink' : 'text-muted hover:text-ink'}`
              }
            >
              {view.label}
            </NavLink>
          ))}
        </nav>
        <div className="ml-auto flex items-center gap-3 text-xs text-muted">
          <span>
            {operator.name || operator.email}, {operator.environment}, version {release(operator.version)}
          </span>
          <a href="/" className="inline-flex items-center gap-1 hover:text-ink">
            <ArrowLeft className="size-3.5" />
            Emudoc
          </a>
        </div>
      </div>
    </header>
  )
}

/** The member's account the operator's session views as support: the app shows it until the view ends, here or there. */
function Viewing({ operator }: { operator: Operator }) {
  const queryClient = useQueryClient()
  const end = useMutation({
    mutationFn: operatorApi.endView,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['operator', 'me'] }),
  })
  const viewing = operator.viewing!
  return (
    <div className="border-b border-line bg-warn/5 text-[13px] text-warn">
      <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-3 px-4 py-1.5 sm:px-6">
        <span>
          Your session views {viewing.name || viewing.email}'s account in {viewing.organization} as support until {formatDateTime(viewing.ends_at)}: the
          app shows it, read only.
        </span>
        <a href="/" className="font-medium text-accent hover:underline">
          Open it
        </a>
        <button type="button" onClick={() => end.mutate()} disabled={end.isPending} className="font-medium text-accent hover:underline">
          End the view
        </button>
      </div>
    </div>
  )
}

/** What the area says when it will not open: two-factor sign-in first, or nothing at all for anyone it does not know. */
function Refused({ error }: { error: Error }) {
  const forbidden = error instanceof ApiError && error.status === 403
  return (
    <div className="flex min-h-dvh items-center justify-center bg-page px-6">
      <div className="max-w-sm text-center text-[13px]">
        <p className="font-medium">{forbidden ? 'The operator’s area is not open to you yet' : 'There is nothing here'}</p>
        <p className="mt-1 text-muted">{forbidden ? error.message : 'Sign in to Emudoc and go to its start page.'}</p>
        <a href={forbidden ? '/account' : '/'} className="mt-3 inline-block text-accent hover:underline">
          {forbidden ? 'Your account' : 'Emudoc'}
        </a>
      </div>
    </div>
  )
}
