import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, ChevronDown, WifiOff, X } from 'lucide-react'
import { useContext, useEffect, useRef, useState } from 'react'
import { Link, Navigate, Outlet, Route, Routes, useLocation, useMatch, useNavigate } from 'react-router'
import { AccountPage } from './account/AccountPage.tsx'
import { ConfirmEmailPage } from './account/ConfirmEmailPage.tsx'
import { DeletionPending } from './account/DeletionPending.tsx'
import { InvitationPage } from './account/InvitationPage.tsx'
import { NotificationBell } from './account/NotificationBell.tsx'
import { TermsPending } from './account/TermsPending.tsx'
import { LoginPage } from './account/LoginPage.tsx'
import { MfaPending } from './account/MfaPending.tsx'
import { ForgotPasswordPage, ResetPasswordPage } from './account/PasswordPages.tsx'
import { FinishSignupPage, SignupPage } from './account/SignupPage.tsx'
import { UnsubscribePage } from './account/UnsubscribePage.tsx'
import { UserMenu } from './account/UserMenu.tsx'
import { VerifyPage } from './account/VerifyPage.tsx'
import { AuthLayout } from './account/shared.tsx'
import { api, auth, handleForbidden, handlePlanLimit, handleSwitched, handleUnauthorized } from './api.ts'
import type { Me } from './api.ts'
import { ME_KEY, MeContext, forgetUser, loginPath, openInOrganization, startAgainInOrganization, useMeQuery } from './auth.ts'
import { describeExcess } from './billing.ts'
import { describeConnection } from './connection.ts'
import { formatDate } from './format.ts'
import { DELETION_SECTION, deletionWords } from './organizationSettings.ts'
import { ErrorBoundary } from './components/ErrorBoundary.tsx'
import { HeaderSearch } from './components/HeaderSearch.tsx'
import { Floating, Menu } from './components/Menu.tsx'
import type { MenuItem } from './components/Menu.tsx'
import { ThemeToggle } from './components/ThemeToggle.tsx'
import { Button, IconButton, Spinner } from './components/ui.tsx'
import { useHelp, useLocalStorage, useOnline, useStatus } from './hooks.ts'
import { HelpMenu } from './help/HelpMenu.tsx'
import { HelpPage } from './help/HelpPage.tsx'
import { WhatsNew } from './help/WhatsNew.tsx'
import { AuditPage } from './organization/AuditPage.tsx'
import { NewOrganizationPage, OrganizationPage } from './organization/OrganizationPage.tsx'
import { OrganizationSwitcher } from './organization/OrganizationSwitcher.tsx'
import { UsagePage } from './organization/UsagePage.tsx'
import { DuplicatesPage } from './pages/DuplicatesPage.tsx'
import { ProjectsPage } from './pages/ProjectsPage.tsx'
import { SearchPage } from './pages/SearchPage.tsx'
import { LegalPage } from './pages/LegalPage.tsx'
import { SubprocessorsPage } from './pages/SubprocessorsPage.tsx'
import { SharedPage } from './shared/SharedPage.tsx'
import { Workspace } from './pages/Workspace.tsx'
import { linkedOrganization } from './notifications.ts'
import { describeServices } from './status.ts'
import { viewWords } from './support.ts'

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

  // Another tab switched the organisation this one shows: start again in the one it works in.
  useEffect(() => {
    handleSwitched(startAgainInOrganization)
    return () => handleSwitched(null)
  }, [])

  // A request refused as not the account's to make: its role or standing may have changed since
  // the page asked, so ask again, and the pages offer only what it may do now.
  useEffect(() => {
    handleForbidden(() => void queryClient.invalidateQueries({ queryKey: ME_KEY }))
    return () => handleForbidden(null)
  }, [queryClient])

  // A request the plan refused: the organisation may have been lowered under what it holds
  // since the page asked, so ask again, and the banner says it is read-only.
  useEffect(() => {
    handlePlanLimit(() => void queryClient.invalidateQueries({ queryKey: ME_KEY }))
    return () => handlePlanLimit(null)
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
          <Route path="help" element={<HelpPage />} />
          <Route path="help/:slug" element={<HelpPage />} />
          <Route path="whats-new" element={<WhatsNew />} />
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
          <Route path="organizations/new" element={<NewOrganizationPage />} />
          <Route path="projects/:projectId/*" element={<Workspace />} />
          <Route path="*" element={<p className="py-16 text-center text-muted">Page not found.</p>} />
        </Route>
      </Routes>
    </MeContext>
  )
}

/**
 * The header and the page below it, for a signed-in user only; an account set to be deleted
 * sees only the way to keep it, one that has not accepted the current terms only the way to
 * accept them, and one whose organisation requires two-factor sign-in that it lacks only the way
 * to set it up or go elsewhere, since the server refuses each everything else.
 */
function Shell() {
  const me = useContext(MeContext)
  const location = useLocation()
  if (!me) return <Navigate to={loginPath(location.pathname + location.search)} replace />
  if (me.deletion_due_at) return <DeletionPending me={me} />
  if (me.terms_due) return <TermsPending me={me} />
  if (me.mfa_due) return <MfaPending me={me} />
  const linked = linkedOrganization(location.pathname, location.search, location.hash)
  if (linked) return <OpenLinked me={me} id={linked.id} rest={linked.rest} />

  return (
    <div className="flex h-dvh flex-col overflow-hidden">
      <header className="shrink-0 border-b border-line bg-surface">
        <div className="flex h-12 items-center gap-2 px-4 sm:gap-3">
          <div className="flex min-w-0 shrink items-center gap-1.5">
            <Link to="/" className="flex shrink-0 items-center gap-2 rounded-md px-1 text-[15px] font-semibold tracking-tight">
              <img src="/favicon.svg" alt="" className="size-5" />
              {/* A narrow screen keeps the mark, and room for the search; the link keeps its name. */}
              <span className="sr-only sm:not-sr-only">Emudoc</span>
            </Link>
            {/* The organisation's switcher shows from a small screen up, and its crumb with it. */}
            <Crumb className="hidden sm:inline" />
            <OrganizationSwitcher />
            <ProjectSwitcher />
          </div>
          {/* The one search box: the project's in a project, every project's elsewhere. */}
          <div className="flex min-w-24 flex-1 justify-center">
            <HeaderSearch />
          </div>
          <div className="flex shrink-0 items-center gap-1 sm:gap-3">
            <ServiceStatus />
            <HelpMenu />
            <NotificationBell />
            <ThemeToggle />
            <UserMenu />
          </div>
        </div>
        <SupportBanner me={me} />
        <VerifyBanner me={me} />
        <DeletionBanner me={me} />
        <PlanBanner me={me} />
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
 * A link that names the organisation it is about, as a notice's mail does: opened in it, switched
 * to first when the session works in another of the user's, and without the name. One the user is
 * not a member of opens where the session works, which says what is not found there.
 */
function OpenLinked({ me, id, rest }: { me: Me; id: number; rest: string }) {
  const navigate = useNavigate()
  useEffect(() => {
    if (id !== me.organization.id && me.organizations.some((organization) => organization.id === id)) {
      openInOrganization(id, rest, true)
    } else {
      navigate(rest, { replace: true })
    }
  }, [id, rest, me, navigate])
  return (
    <div className="flex h-dvh items-center justify-center">
      <Spinner className="size-5" />
    </div>
  )
}

/**
 * Where the service says whether it is up, under the screen that cannot reach the server: the
 * help reads no database, so it answers while the database is down.
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

/**
 * Says on every page that Emudoc's support views a member's account rather than its member, read only, and ends the view,
 * back to the operator's area: the page there, whatever the end answered, finds the session the operator's own again.
 */
function SupportBanner({ me }: { me: Me }) {
  const words = viewWords(me)
  if (!words) return null
  const end = () =>
    void auth
      .endSupportView()
      .catch(() => undefined)
      .then(() => window.location.assign('/operator'))

  return (
    <div className="flex items-center justify-center gap-3 border-t border-line bg-warn/5 px-4 py-1.5 text-[13px] text-warn">
      <span className="min-w-0">{words}</span>
      <button type="button" onClick={end} className="shrink-0 font-medium text-accent hover:underline">
        End the view
      </button>
    </div>
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
 * Says on every page that the organisation holds more than its plan allows, and so is read-only
 * until it is within it, with the way to its plan; it goes once a deletion or a purchase brings
 * it within.
 */
/** Under the header while an owner set the organisation to be deleted: when, and the way to keep it. */
function DeletionBanner({ me }: { me: Me }) {
  const location = useLocation()
  const words = deletionWords(me.organization, me.role, formatDate(me.organization.deletion_due_at))
  if (!words) return null

  return (
    <div className="flex items-center justify-center gap-3 border-t border-line bg-bad/5 px-4 py-1.5 text-[13px] text-bad">
      <span className="min-w-0">{words}</span>
      {me.role === 'owner' && location.pathname !== '/organization' && (
        <Link to={`/organization#${DELETION_SECTION}`} className="shrink-0 font-medium text-accent hover:underline">
          Keep it
        </Link>
      )}
    </div>
  )
}

function PlanBanner({ me }: { me: Me }) {
  const location = useLocation()
  const past = describeExcess(me.organization)
  if (!past) return null

  return (
    <div className="flex items-center justify-center gap-3 border-t border-line bg-warn/5 px-4 py-1.5 text-[13px] text-warn">
      <span className="min-w-0">{past}</span>
      {location.pathname !== '/organization' && (
        <Link to="/organization" className="shrink-0 font-medium text-accent hover:underline">
          See the plan
        </Link>
      )}
    </div>
  )
}

/**
 * Shows whether the two optional services, the AI model and semantic search, are usable, and in
 * their place while the browser is offline that it is, at every width.
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
