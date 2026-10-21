// The account page: who you are and your address, how you are told notices, the terms you
// accepted, how you sign in, where you are signed in, a copy of your data, and deleting the
// account.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Download, LogOut, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useLocation } from 'react-router'
import {
  ACCOUNT_EXPORT_URL,
  DELETION_GRACE_DAYS,
  PASSWORD_MAX_LENGTH,
  PASSWORD_MIN_LENGTH,
  ROLE_LABELS,
  auth,
} from '../api.ts'
import type { Me, MfaSetup, UserSession } from '../api.ts'
import { ME_KEY, setSignedIn, useMe, useSignOut } from '../auth.ts'
import {
  Button,
  ButtonLink,
  Checkbox,
  Chip,
  IconButton,
  InputField,
  Loading,
  Notice,
  Row,
  Section,
  SelectField,
} from '../components/ui.tsx'
import { NoticeChoices } from '../components/NoticeChoices.tsx'
import { formatDate, formatDateTime } from '../format.ts'
import { useLocalStorage } from '../hooks.ts'
import { browserNoticesKey } from '../notifications.ts'
import { browserTimeZone, timeZoneChoices, zoneLabel } from '../timeZones.ts'
import { ChangeEmailForm } from './ChangeEmail.tsx'
import { DeleteAccountForm } from './DeleteAccount.tsx'
import { PasswordField } from './shared.tsx'
import { RecoveryCodes, SetupStep } from './TwoFactor.tsx'

const PROVIDER_LABELS: Record<string, string> = { google: 'Google', microsoft: 'Microsoft' }

export function AccountPage() {
  const me = useMe()
  const signOut = useSignOut()

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-2xl px-6 py-10">
        <div className="mb-6 flex items-end justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Account</h1>
            <p className="mt-1 text-[13px] text-muted">Who you are, how you sign in, and where you are signed in.</p>
          </div>
          <Button icon={<LogOut className="size-4" />} onClick={signOut}>
            Sign out
          </Button>
        </div>
        <div className="space-y-5">
          <ProfileSection me={me} />
          <EmailSection me={me} />
          <NoticesSection me={me} />
          <PasswordSection />
          <TwoFactorSection me={me} />
          <SessionsSection />
          <DataSection me={me} />
          <DeleteSection me={me} />
        </div>
      </div>
    </div>
  )
}

function ProfileSection({ me }: { me: Me }) {
  return (
    <Section title="Profile">
      <ProfileForm me={me} />
      <dl className="mt-4 divide-y divide-line border-t border-line">
        <Row label="Organisation">
          <Link to="/organization" className="truncate font-medium text-accent hover:underline">
            {me.organization.name}
          </Link>
          <Chip>{ROLE_LABELS[me.role]}</Chip>
          {me.organizations.length > 1 && <span className="text-muted">and {otherOrganizations(me)}</span>}
        </Row>
        {me.terms_accepted_at && (
          <Row label="Terms">
            <span>
              Accepted on {formatDate(me.terms_accepted_at)}:{' '}
              <Link to="/terms" className="font-medium text-accent hover:underline">
                terms of service
              </Link>
              ,{' '}
              <Link to="/privacy" className="font-medium text-accent hover:underline">
                privacy policy
              </Link>
            </span>
          </Row>
        )}
        {me.identities.length > 0 && (
          <Row label="Signs in with">
            {me.identities.map((provider) => (
              <Chip key={provider}>{PROVIDER_LABELS[provider] ?? provider}</Chip>
            ))}
          </Row>
        )}
      </dl>
    </Section>
  )
}

// The zone is the browser's until the member saves one, so a first save takes it.
function ProfileForm({ me }: { me: Me }) {
  const queryClient = useQueryClient()
  const browser = useMemo(() => browserTimeZone(), [])
  const zones = useMemo(() => timeZoneChoices(me.time_zone, browser), [me.time_zone, browser])
  const [name, setName] = useState(me.name)
  const [zone, setZone] = useState(me.time_zone || browser || 'UTC')
  const save = useMutation({
    mutationFn: () => auth.updateProfile({ name: name.trim(), time_zone: zone }),
    onSuccess: (account) => setSignedIn(queryClient, account),
  })
  const changed = name.trim() !== me.name || zone !== me.time_zone

  function submit(event: FormEvent) {
    event.preventDefault()
    if (changed) save.mutate()
  }

  return (
    <form onSubmit={submit} className="max-w-sm space-y-3">
      <InputField label="Name" autoComplete="name" maxLength={200} value={name} onChange={(event) => setName(event.target.value)} />
      <SelectField
        label="Time zone"
        hint={
          me.time_zone
            ? "The dates in the email Emudoc sends you are in this zone; the pages show your browser's."
            : 'Not saved yet, so the dates in the email Emudoc sends you are in UTC.'
        }
        value={zone}
        onChange={(event) => setZone(event.target.value)}
      >
        {zones.map((choice) => (
          <option key={choice} value={choice}>
            {zoneLabel(choice)}
          </option>
        ))}
      </SelectField>
      {save.isError && <Notice tone="bad">{save.error.message}</Notice>}
      <div className="flex items-center gap-3">
        <Button type="submit" variant="primary" disabled={!changed || save.isPending}>
          Save
        </Button>
        {save.isSuccess && !changed && <span className="text-[13px] text-muted">Saved.</span>}
      </div>
    </form>
  )
}

function EmailSection({ me }: { me: Me }) {
  const [changing, setChanging] = useState(false)
  const resend = useMutation({ mutationFn: auth.resendVerification })
  return (
    <Section title="Email address" description="Where Emudoc writes to you, and the address you sign in with.">
      <div className="flex flex-wrap items-center gap-2 text-[13px]">
        <span className="truncate font-medium">{me.email}</span>
        {me.email_verified ? (
          <Chip>Confirmed</Chip>
        ) : (
          <>
            <Chip>Not confirmed</Chip>
            {resend.isSuccess ? (
              <span className="text-muted">A new link is on its way.</span>
            ) : (
              <button
                type="button"
                onClick={() => resend.mutate()}
                disabled={resend.isPending}
                className="font-medium text-accent hover:underline disabled:opacity-50"
              >
                Send the link again
              </button>
            )}
            {resend.isError && <span className="text-bad">{resend.error.message}</span>}
          </>
        )}
      </div>
      <div className="mt-4">
        {changing ? (
          <ChangeEmailForm me={me} onClose={() => setChanging(false)} />
        ) : (
          <Button onClick={() => setChanging(true)}>Change email address…</Button>
        )}
      </div>
    </Section>
  )
}

/**
 * How each kind of notice is told, the news a member may do without: in the notification centre
 * and by email, each chosen apart, and whether this browser shows them as they come. The bell's
 * settings link leads here, by the address's `#notices`.
 */
function NoticesSection({ me }: { me: Me }) {
  const location = useLocation()
  const box = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (location.hash === '#notices') {
      void box.current?.scrollIntoView({ block: 'start' })
    }
  }, [location.hash])
  return (
    <div ref={box} id="notices" className="scroll-mt-6">
      <Section
        title="Notices"
        description="News about your work in Emudoc, under the bell in the header and by email. Until you choose, each kind is told as your organisation tells its members. Email about your account, invitations and billing is sent either way."
      >
        {/* Turning a kind on by email also writes again to an address that bounced. */}
        <NoticeChoices key={JSON.stringify(me.notices)} saved={me.notices} change={(choice) => auth.updateProfile({ notices: [choice] })} />
        <BrowserNotices me={me} />
      </Section>
    </div>
  )
}

// Whether this browser shows the notices as they come while Emudoc is open in a tab: the
// browser's choice, kept in its own storage, with its permission, asked for as the box is ticked.
function BrowserNotices({ me }: { me: Me }) {
  const [on, setOn] = useLocalStorage(browserNoticesKey(me.id), false, (value): value is boolean => typeof value === 'boolean')
  const [blocked, setBlocked] = useState(false)
  if (!('Notification' in window)) return null

  async function choose(value: boolean) {
    if (value && window.Notification.permission !== 'granted' && (await window.Notification.requestPermission()) !== 'granted') {
      setBlocked(true)
      return
    }
    setBlocked(false)
    setOn(value)
  }

  return (
    <div className="mt-4 border-t border-line pt-4">
      <Checkbox
        label="Also show them on this computer as they come"
        checked={on && window.Notification.permission === 'granted'}
        onChange={(event) => void choose(event.target.checked)}
      />
      <p className="mt-1 pl-6.5 text-xs text-muted">
        As your computer's own notifications, while Emudoc is open in a tab in the background. This browser's choice alone.
      </p>
      {blocked && (
        <p className="mt-1 pl-6.5 text-xs text-warn">
          This browser blocks notifications from Emudoc: allow them in its settings for this site, then tick the box again.
        </p>
      )}
    </div>
  )
}

function PasswordSection() {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const change = useMutation({
    mutationFn: () => auth.changePassword({ current_password: current, new_password: next }),
    onSuccess: () => {
      setCurrent('')
      setNext('')
    },
  })

  function submit(event: FormEvent) {
    event.preventDefault()
    if (next.length >= PASSWORD_MIN_LENGTH) change.mutate()
  }

  return (
    <Section title="Password" description="Changing it signs out every other browser and device.">
      <form onSubmit={submit} className="max-w-sm space-y-3">
        <PasswordField
          label="Current password"
          autoComplete="current-password"
          required
          value={current}
          onChange={(event) => setCurrent(event.target.value)}
        />
        <PasswordField
          label="New password"
          autoComplete="new-password"
          required
          minLength={PASSWORD_MIN_LENGTH}
          maxLength={PASSWORD_MAX_LENGTH}
          hint={`At least ${PASSWORD_MIN_LENGTH} characters.`}
          value={next}
          onChange={(event) => setNext(event.target.value)}
        />
        {change.isError && <Notice tone="bad">{change.error.message}</Notice>}
        {change.isSuccess && <Notice tone="info">Your password is changed and your other sessions are signed out.</Notice>}
        <Button type="submit" variant="primary" disabled={!current || !next || change.isPending}>
          Change password
        </Button>
      </form>
    </Section>
  )
}

// Turning two-factor sign-in on goes through a secret, then the recovery codes; off, through a
// confirmation.
type MfaPhase = { kind: 'idle' } | { kind: 'setup'; setup: MfaSetup } | { kind: 'codes'; codes: string[] } | { kind: 'disable' }

function TwoFactorSection({ me }: { me: Me }) {
  const queryClient = useQueryClient()
  const [phase, setPhase] = useState<MfaPhase>({ kind: 'idle' })
  const setup = useMutation({
    mutationFn: auth.setupMfa,
    onSuccess: (result) => setPhase({ kind: 'setup', setup: result }),
  })
  const refresh = () => queryClient.invalidateQueries({ queryKey: ME_KEY })

  return (
    <Section
      title="Two-factor sign-in"
      description="With it on, signing in takes your password and a code from an authenticator app on your phone, so a leaked password alone is not enough."
    >
      {phase.kind === 'setup' && (
        <SetupStep
          setup={phase.setup}
          onCancel={() => setPhase({ kind: 'idle' })}
          onEnabled={(codes) => {
            setPhase({ kind: 'codes', codes })
            refresh()
          }}
        />
      )}
      {phase.kind === 'codes' && <RecoveryCodes codes={phase.codes} onDone={() => setPhase({ kind: 'idle' })} />}
      {phase.kind === 'disable' && (
        <DisableStep
          onCancel={() => setPhase({ kind: 'idle' })}
          onDisabled={() => {
            setPhase({ kind: 'idle' })
            refresh()
          }}
        />
      )}
      {phase.kind === 'idle' && (
        <div className="flex flex-wrap items-center justify-between gap-3 text-[13px]">
          <span className="flex items-center gap-2">
            <span className={`size-1.5 rounded-full ${me.mfa_enabled ? 'bg-good' : 'bg-line'}`} />
            {me.mfa_enabled ? 'On: a code is asked at every sign-in.' : 'Off.'}
          </span>
          {me.mfa_enabled ? (
            <Button onClick={() => setPhase({ kind: 'disable' })}>Turn off</Button>
          ) : (
            <Button variant="primary" onClick={() => setup.mutate()} disabled={setup.isPending}>
              Set up an authenticator app
            </Button>
          )}
          {setup.isError && <Notice tone="bad">{setup.error.message}</Notice>}
        </div>
      )}
    </Section>
  )
}

function DisableStep({ onCancel, onDisabled }: { onCancel: () => void; onDisabled: () => void }) {
  // Either the password or a code confirms the change; the user picks which.
  const [byCode, setByCode] = useState(false)
  const [secret, setSecret] = useState('')
  const disable = useMutation({
    mutationFn: () => auth.disableMfa(byCode ? { code: secret.trim() } : { password: secret }),
    onSuccess: onDisabled,
  })

  function submit(event: FormEvent) {
    event.preventDefault()
    if (secret.trim()) disable.mutate()
  }

  const toggle = (
    <button type="button" onClick={() => setByCode(!byCode)} className="text-muted hover:text-ink">
      {byCode ? 'Use your password instead' : 'Use a code instead'}
    </button>
  )
  return (
    <form onSubmit={submit} className="max-w-sm space-y-3">
      <Notice tone="warn">Without the second factor, your password alone signs in to the account.</Notice>
      {byCode ? (
        <InputField
          label="Code from your app, or a recovery code"
          autoComplete="one-time-code"
          required
          className="font-mono"
          corner={toggle}
          value={secret}
          onChange={(event) => setSecret(event.target.value)}
        />
      ) : (
        <PasswordField
          label="Your password"
          autoComplete="current-password"
          required
          corner={toggle}
          value={secret}
          onChange={(event) => setSecret(event.target.value)}
        />
      )}
      {disable.isError && <Notice tone="bad">{disable.error.message}</Notice>}
      <div className="flex gap-2">
        <Button type="submit" variant="danger" disabled={!secret.trim() || disable.isPending}>
          Turn off two-factor sign-in
        </Button>
        <Button variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  )
}

function SessionsSection() {
  const queryClient = useQueryClient()
  const sessions = useQuery({ queryKey: ['sessions'], queryFn: auth.sessions })
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['sessions'] })
  const revoke = useMutation({
    mutationFn: (session: UserSession) => auth.revokeSession(session.id),
    onSuccess: (_, session) => {
      // Ending this very session signs this browser out; the server already cleared the cookie.
      if (session.current) setSignedIn(queryClient, null)
      else refresh()
    },
  })
  const revokeOthers = useMutation({ mutationFn: auth.revokeOtherSessions, onSuccess: refresh })
  const others = sessions.data?.filter((session) => !session.current).length ?? 0

  return (
    <Section title="Where you are signed in" description="Every browser and device with an open session. Sign out any you do not recognise.">
      {sessions.isPending && <Loading />}
      {sessions.isError && <Notice tone="bad">{sessions.error.message}</Notice>}
      {sessions.data && (
        <ul className="divide-y divide-line">
          {sessions.data.map((session) => (
            <li key={session.id} className="flex items-center gap-3 py-2.5 text-[13px]">
              <div className="min-w-0 flex-1">
                <p className="flex items-center gap-2">
                  <span className="truncate font-medium">{describeAgent(session.user_agent)}</span>
                  {session.current && <Chip tone="accent">This browser</Chip>}
                </p>
                <p className="text-xs text-muted">
                  {session.ip && <>{session.ip} · </>}
                  Last active {formatDateTime(session.last_seen_at)} · Signed in {formatDateTime(session.created_at)}
                </p>
              </div>
              <IconButton label={session.current ? 'Sign out here' : 'Sign out this session'} danger onClick={() => revoke.mutate(session)} disabled={revoke.isPending}>
                <X className="size-4" />
              </IconButton>
            </li>
          ))}
        </ul>
      )}
      {(revoke.isError || revokeOthers.isError) && (
        <div className="mt-3">
          <Notice tone="bad">{(revoke.error ?? revokeOthers.error)?.message}</Notice>
        </div>
      )}
      {others > 0 && (
        <div className="mt-3">
          <Button onClick={() => revokeOthers.mutate()} disabled={revokeOthers.isPending}>
            Sign out everywhere else
          </Button>
        </div>
      )}
    </Section>
  )
}

function DataSection({ me }: { me: Me }) {
  const days = me.organization.chat_retention_days
  return (
    <Section
      title="Your data"
      description="A copy of everything Emudoc keeps about you: your account, how you sign in, your organisations, where you signed in from, the chats you started and the usage counted to you. The documents belong to your organisation and are not in it."
    >
      <ButtonLink href={ACCOUNT_EXPORT_URL} download icon={<Download className="size-4" />}>
        Download your data
      </ButtonLink>
      {days !== null && (
        <p className="mt-3 text-[13px] text-muted">
          In {me.organization.name}, a chat is deleted {days.toLocaleString()} days after its last question.
        </p>
      )}
    </Section>
  )
}

function otherOrganizations(me: Me): string {
  const others = me.organizations.length - 1
  return `${others} other organisation${others === 1 ? '' : 's'}`
}

function DeleteSection({ me }: { me: Me }) {
  const [open, setOpen] = useState(false)
  return (
    <Section
      title="Delete account"
      description={`The account goes ${DELETION_GRACE_DAYS} days after you ask; until then, signing in lets you keep it. Each organisation you are the only member of goes with it, with its projects, documents and chats.`}
    >
      {open ? (
        <DeleteAccountForm me={me} onCancel={() => setOpen(false)} />
      ) : (
        <Button variant="danger" onClick={() => setOpen(true)}>
          Delete account…
        </Button>
      )}
    </Section>
  )
}

/** "Firefox on Linux" from a user-agent string, or the string itself when it fits no pattern. */
function describeAgent(agent: string): string {
  const browser = /Edg\//.test(agent)
    ? 'Edge'
    : /OPR\//.test(agent)
      ? 'Opera'
      : /Firefox\//.test(agent)
        ? 'Firefox'
        : /Chrome\//.test(agent)
          ? 'Chrome'
          : /Safari\//.test(agent)
            ? 'Safari'
            : null
  const system = /Windows/.test(agent)
    ? 'Windows'
    : /Android/.test(agent)
      ? 'Android'
      : /iPhone|iPad/.test(agent)
        ? 'iPhone or iPad'
        : /Mac OS X/.test(agent)
          ? 'macOS'
          : /Linux/.test(agent)
            ? 'Linux'
            : null
  if (!browser && !system) return agent.slice(0, 60) || 'Unknown device'
  return [browser, system].filter(Boolean).join(' on ')
}
