import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, ChevronDown, WifiOff, X } from 'lucide-react'
import { useContext, useEffect, useRef, useState } from 'react'
import { Link, Navigate, Outlet, Route, Routes, useLocation, useMatch, useNavigate } from 'react-router'
import { AccountPage } from './account/AccountPage.tsx'
import { ConfirmEmailPage } from './account/ConfirmEmailPage.tsx'
import { DeletionPending } from './account/DeletionPending.tsx'
import { InvitationPage } from './account/InvitationPage.tsx'
import { NoWorkspace } from './account/NoWorkspace.tsx'
import { NotificationBell } from './account/NotificationBell.tsx'
import { TermsPending } from './account/TermsPending.tsx'
import { LoginPage } from './account/LoginPage.tsx'
import { MfaPending } from './account/MfaPending.tsx'
import { ForgotPasswordPage, ResetPasswordPage } from './account/PasswordPages.tsx'
import { SetupPage } from './account/SetupPage.tsx'
import { FinishSignupPage, SignupPage } from './account/SignupPage.tsx'
import { UnsubscribePage } from './account/UnsubscribePage.tsx'
import { UserMenu } from './account/UserMenu.tsx'
import { VerifyPage } from './account/VerifyPage.tsx'
import { AuthLayout } from './account/shared.tsx'
import { api, auth, handleForbidden, handleUnauthorized } from './api.ts'
import type { Me } from './api.ts'
import { ME_KEY, MeContext, forgetUser, loginPath, useMe, useMeQuery } from './auth.ts'
import { describeConnection } from './connection.ts'
import { ErrorBoundary } from './components/ErrorBoundary.tsx'
import { Floating, Menu } from './components/Menu.tsx'
import type { MenuItem } from './components/Menu.tsx'
import { ThemeToggle } from './components/ThemeToggle.tsx'
import { Button, IconButton, Spinner } from './components/ui.tsx'
import { useHelp, useLocalStorage, useOnline, useStatus } from './hooks.ts'
import { HelpMenu } from './help/HelpMenu.tsx'
import { AuditPage } from './organization/AuditPage.tsx'
import { OrganizationPage } from './organization/OrganizationPage.tsx'
import { UsagePage } from './organization/UsagePage.tsx'
import { DuplicatesPage } from './pages/DuplicatesPage.tsx'
import { ProjectsPage } from './pages/ProjectsPage.tsx'
import { SearchPage } from './pages/SearchPage.tsx'
import { LegalPage } from './pages/LegalPage.tsx'
import { SubprocessorsPage } from './pages/SubprocessorsPage.tsx'
import { SharedPage } from './shared/SharedPage.tsx'
import { Workspace } from './pages/Workspace.tsx'
import { describeServices } from './status.ts'

/**
 * Asks who is signed in before showing anything. The sign-in pages render on their own; every
 * other page sits in the shell, which sends a visitor without a session to sign in first.
 */
function App() {
  const me = useMeQuery()
  const queryClient = useQueryClient()

  // A session the server stopped accepting: forget the user and what was fetched for them, and
  // the shell leaves for sign-in.
  useEffect(() => {
    handleUnauthorized(() => forgetUser(queryClient))
    return () => handleUnauthorized(null)
  }, [queryClient])

  // A request refused as not the account's to make: its role or standing may have changed since
  // the page asked, so ask again, and the pages offer only what it may do now.
  useEffect(() => {
    handleForbidden(() => void queryClient.invalidateQueries({ queryKey: ME_KEY }))
    return () => handleForbidden(null)
  }, [queryClient])

  if (me.isPending) {
    return (
      <div className="flex h-dvh items-center justify-center">
        <Spinner className="size-5" />
      </div>
    )
  }
  // A failure to ask again keeps the answer before: only the first one has nothing to show.
  if (me.isError && me.data === undefined) {
    return (
      <div className="flex h-dvh items-center justify-center px-6">
        <div className="max-w-sm text-center">
          <p className="font-medium">Emudoc cannot reach its server.</p>
          <p className="mt-1 text-[13px] text-muted">{me.error.message}</p>
          <Button className="mt-4" onClick={() => me.refetch()}>
            Try again
          </Button>
          <StatusPageNote />
        </div>
      </div>
    )
  }

  return (
    <MeContext value={me.data ?? null}>
      <Routes>
        <Route element={<AuthLayout />}>
          <Route path="login" element={<LoginPage />} />
          <Route path="setup" element={<SetupPage />} />
          <Route path="signup" element={<SignupPage />} />
          <Route path="finish-signup" element={<FinishSignupPage />} />
          <Route path="forgot-password" element={<ForgotPasswordPage />} />
          <Route path="reset-password" element={<ResetPasswordPage />} />
          <Route path="verify" element={<VerifyPage />} />
          <Route path="confirm-email" element={<ConfirmEmailPage />} />
          <Route path="invitation" element={<InvitationPage />} />
          <Route path="unsubscribe" element={<UnsubscribePage />} />
          <Route path="subprocessors" element={<SubprocessorsPage />} />
          <Route path="terms" element={<LegalPage document="terms" />} />
          <Route path="privacy" element={<LegalPage document="privacy" />} />
        </Route>
        {/* A share link's page, for whoever holds the link, with a frame of its own. */}
        <Route path="shared" element={<SharedPage />} />
        <Route element={<Shell />}>
          <Route index element={<ProjectsPage />} />
          <Route path="duplicates" element={<DuplicatesPage />} />
          <Route path="search" element={<SearchPage />} />
          <Route path="account" element={<AccountPage />} />
          <Route path="organization" element={<OrganizationPage />} />
          <Route path="organization/usage" element={<UsagePage />} />
          <Route path="organization/audit" element={<AuditPage />} />
          <Route path="projects/:projectId/*" element={<Workspace />} />
          <Route path="*" element={<p className="py-16 text-center text-muted">Page not found.</p>} />
        </Route>
      </Routes>
    </MeContext>
  )
}

/**
 * The header and the page below it, for a signed-in member only; one an admin removed from the
 * workspace sees only that it has none, an account set to be deleted only the way to keep it, one
 * that has not accepted the current terms only the way to accept them, and one whose workspace
 * requires two-factor sign-in that it lacks only the way to set it up, since the server refuses
 * each everything else.
 */
function Shell() {
  const me = useContext(MeContext)
  const location = useLocation()
  if (!me) return <Navigate to={loginPath(location.pathname + location.search)} replace />
  if (me.organization === null) return <NoWorkspace me={me} />
  if (me.deletion_due_at) return <DeletionPending me={me} />
  if (me.terms_due) return <TermsPending me={me} />
  if (me.mfa_due) return <MfaPending me={me} />

  return (
    <div className="flex h-dvh flex-col overflow-hidden">
      <header className="shrink-0 border-b border-line bg-surface">
        <div className="flex h-12 items-center gap-2 px-4 sm:gap-3">
          <div className="flex min-w-0 shrink items-center gap-1.5">
            <Link to="/" className="flex shrink-0 items-center gap-2 rounded-md px-1 text-[15px] font-semibold tracking-tight">
              <img src="/favicon.svg" alt="" className="size-5" />
              {/* A narrow screen keeps the mark, and room for the project's switcher; the link keeps its name. */}
              <span className="sr-only sm:not-sr-only">Emudoc</span>
            </Link>
            {/* The workspace's name shows from a small screen up, and its crumb with it. */}
            <Crumb className="hidden sm:inline" />
            <WorkspaceName />
            <ProjectSwitcher />
          </div>
          {/* The project's search is its library's; every project's, the projects page's. */}
          <div className="flex-1" />
          <div className="flex shrink-0 items-center gap-1 sm:gap-3">
            <ServiceStatus />
            <HelpMenu />
            <NotificationBell />
            <ThemeToggle />
            <UserMenu />
          </div>
        </div>
        <VerifyBanner me={me} />
      </header>
      <main className="min-h-0 flex-1">
        {/* A page that breaks leaves the header, and with it the help menu and the way elsewhere. */}
        <ErrorBoundary resetKey={location.pathname}>
          <Outlet />
        </ErrorBoundary>
      </main>
    </div>
  )
}

/**
 * Where the service says whether it is up, under the screen that cannot reach the server:
 * `GET /api/help` reads no database, so it answers while the database is down.
 */
function StatusPageNote() {
  const help = useHelp()
  if (!help.data?.status_page) return null
  return (
    <p className="mt-4 text-[13px] text-muted">
      See whether Emudoc is down on{' '}
      <a href={help.data.status_page} target="_blank" rel="noreferrer" className="text-ink underline">
        its status page
      </a>
      .
    </p>
  )
}

function Crumb({ className = '' }: { className?: string }) {
  return (
    <span className={`text-line select-none ${className}`} aria-hidden>
      <svg width="12" height="20" viewBox="0 0 12 20" fill="none" stroke="currentColor" strokeWidth="1.5">
        <path d="M9 2 3 18" />
      </svg>
    </span>
  )
}

/** The workspace's name, which opens its page. */
function WorkspaceName() {
  const me = useMe()
  return (
    <Link
      to="/organization"
      title="Your workspace"
      data-tour="organization"
      className="hidden h-7 min-w-0 items-center rounded-md px-2 text-[13px] text-muted transition hover:bg-sunken hover:text-ink sm:flex"
    >
      <span className="truncate">{me.organization.name}</span>
    </Link>
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
      <Crumb />
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

/** Asks a new account to confirm its address, once; the account page keeps showing the state. */
function VerifyBanner({ me }: { me: Me }) {
  const [dismissed, setDismissed] = useLocalStorage(
    `verify-dismissed:${me.id}`,
    false,
    (value): value is boolean => typeof value === 'boolean',
  )
  const resend = useMutation({ mutationFn: auth.resendVerification })
  if (me.email_verified || dismissed) return null

  return (
    <div className="flex items-center justify-center gap-3 border-t border-line bg-accent-soft px-4 py-1.5 text-[13px]">
      <span className="min-w-0 truncate">
        Confirm your email address: a link went to <span className="font-medium">{me.email}</span>.
      </span>
      {resend.isSuccess ? (
        <span className="shrink-0 text-muted">Sent again.</span>
      ) : (
        <button
          type="button"
          onClick={() => resend.mutate()}
          disabled={resend.isPending}
          className="shrink-0 font-medium text-accent hover:underline disabled:opacity-50"
        >
          Resend
        </button>
      )}
      {resend.isError && <span className="shrink-0 text-bad">{resend.error.message}</span>}
      <IconButton label="Dismiss" onClick={() => setDismissed(true)}>
        <X className="size-3.5" />
      </IconButton>
    </div>
  )
}

/**
 * Shows that the background work is not running, while no worker runs, and in its place while
 * the browser is offline that it is, at every width.
 */
function ServiceStatus() {
  const { data } = useStatus()
  const connection = describeConnection(useOnline())
  if (connection) {
    return (
      <span
        role="status"
        title={connection.detail}
        className="flex h-7 cursor-help items-center gap-1.5 rounded-md px-2 text-xs text-warn transition hover:bg-sunken"
      >
        <WifiOff className="size-3.5" />
        {connection.label}
      </span>
    )
  }
  if (!data) return null
  return (
    <div className="hidden items-center gap-1 text-xs text-muted sm:flex">
      {describeServices(data).map((item) => (
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
