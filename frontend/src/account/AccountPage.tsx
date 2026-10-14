// The account page: who you are, how you sign in, and where you are signed in.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, Copy, LogOut, X } from 'lucide-react'
import { useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import { PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH, REGION_NAMES, auth } from '../api.ts'
import type { Me, MfaSetup, UserSession } from '../api.ts'
import { ME_KEY, setSignedIn, useMe, useSignOut } from '../auth.ts'
import { Button, Chip, IconButton, InputField, Loading, Notice } from '../components/ui.tsx'
import { formatDateTime } from '../format.ts'
import { QrCode } from './QrCode.tsx'
import { PasswordField } from './shared.tsx'

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
            <p className="mt-1 text-[13px] text-muted">How you sign in, and where you are signed in.</p>
          </div>
          <Button icon={<LogOut className="size-4" />} onClick={signOut}>
            Sign out
          </Button>
        </div>
        <div className="space-y-5">
          <ProfileSection me={me} />
          <PasswordSection />
          <TwoFactorSection me={me} />
          <SessionsSection />
        </div>
      </div>
    </div>
  )
}

function Section({ title, description, children }: { title: string; description?: string; children: ReactNode }) {
  return (
    <section className="rounded-xl border border-line bg-surface p-5 shadow-xs">
      <h2 className="font-semibold">{title}</h2>
      {description && <p className="mt-0.5 text-[13px] leading-relaxed text-muted">{description}</p>}
      <div className="mt-4">{children}</div>
    </section>
  )
}

const capitalised = (text: string) => text[0].toUpperCase() + text.slice(1)

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-wrap items-baseline gap-x-6 gap-y-1 py-2 text-[13px]">
      <dt className="w-28 shrink-0 text-muted">{label}</dt>
      <dd className="flex min-w-0 flex-1 flex-wrap items-center gap-2">{children}</dd>
    </div>
  )
}

function ProfileSection({ me }: { me: Me }) {
  const resend = useMutation({ mutationFn: auth.resendVerification })
  const role = me.role[0].toUpperCase() + me.role.slice(1)
  return (
    <Section title="Profile">
      <dl className="divide-y divide-line">
        <Row label="Name">{me.name || <span className="text-muted">Not given</span>}</Row>
        <Row label="Email">
          <span className="truncate">{me.email}</span>
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
        </Row>
        <Row label="Organisation">
          <span className="truncate">{me.organization.name}</span>
          <Chip>{role}</Chip>
        </Row>
        {me.organization.region && (
          <Row label="Data kept in">
            <span>{capitalised(REGION_NAMES[me.organization.region])}</span>
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

function SetupStep({ setup, onCancel, onEnabled }: { setup: MfaSetup; onCancel: () => void; onEnabled: (codes: string[]) => void }) {
  const [code, setCode] = useState('')
  const enable = useMutation({
    mutationFn: () => auth.enableMfa(code.trim()),
    onSuccess: (result) => onEnabled(result.recovery_codes),
  })

  function submit(event: FormEvent) {
    event.preventDefault()
    if (code.trim()) enable.mutate()
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-5 sm:flex-row">
      <QrCode value={setup.otpauth_url} label="Scan this with your authenticator app" />
      <div className="min-w-0 flex-1 space-y-3 text-[13px]">
        <p className="leading-relaxed text-muted">
          Scan the code with an authenticator app such as Google Authenticator, Microsoft Authenticator, 1Password or
          Aegis, or{' '}
          <a href={setup.otpauth_url} className="font-medium text-accent hover:underline">
            open it in an app on this device
          </a>
          . If you cannot scan it, enter this key by hand:
        </p>
        <code className="block rounded-md border border-line bg-sunken px-3 py-2 font-mono text-xs tracking-wider break-all select-all">
          {setup.secret.match(/.{1,4}/g)?.join(' ')}
        </code>
        <InputField
          label="Then enter the code the app shows"
          autoComplete="one-time-code"
          inputMode="numeric"
          required
          className="max-w-xs font-mono"
          value={code}
          onChange={(event) => setCode(event.target.value)}
        />
        {enable.isError && <Notice tone="bad">{enable.error.message}</Notice>}
        <div className="flex gap-2">
          <Button type="submit" variant="primary" disabled={!code.trim() || enable.isPending}>
            Turn on
          </Button>
          <Button variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
        </div>
      </div>
    </form>
  )
}

/** Shown once, right after two-factor sign-in is turned on. */
function RecoveryCodes({ codes, onDone }: { codes: string[]; onDone: () => void }) {
  const [copied, setCopied] = useState(false)

  async function copy() {
    try {
      await navigator.clipboard.writeText(codes.join('\n'))
      setCopied(true)
    } catch {
      // The clipboard can be unavailable; the codes are on screen to copy by hand.
    }
  }

  return (
    <div className="space-y-3 text-[13px]">
      <Notice tone="warn">
        Two-factor sign-in is on. Save these recovery codes somewhere safe: each one signs you in once if you lose
        the app. They are not shown again.
      </Notice>
      <div className="grid grid-cols-2 gap-x-6 gap-y-1 rounded-md border border-line bg-sunken px-4 py-3 font-mono text-xs sm:grid-cols-4">
        {codes.map((code) => (
          <span key={code} className="select-all">
            {code}
          </span>
        ))}
      </div>
      <div className="flex gap-2">
        <Button icon={copied ? <Check className="size-4" /> : <Copy className="size-4" />} onClick={copy}>
          {copied ? 'Copied' : 'Copy the codes'}
        </Button>
        <Button variant="primary" onClick={onDone}>
          I have saved them
        </Button>
      </div>
    </div>
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
