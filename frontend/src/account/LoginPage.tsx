import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useContext, useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { Link, Navigate, useLocation, useNavigate, useSearchParams } from 'react-router'
import { auth } from '../api.ts'
import { MeContext, safeNext, setSignedIn, signOut } from '../auth.ts'
import { Button, InputField, Notice } from '../components/ui.tsx'
import { usePageTitle } from '../hooks.ts'
import { AuthCard, PasswordField, ProviderButtons } from './shared.tsx'
import { useAuthOptions } from './authOptions.ts'

/** What a page that sends the user here may leave in the router state. */
interface LoginState {
  /** Sent by the sign-out: end the session here, where no shell is left to redirect. */
  signOut?: boolean
  /** A word to show, such as that the password was changed. */
  notice?: string
}

/**
 * Sign in with the password, then, for accounts that ask for it, with the authenticator code.
 * A sign-in through a provider ends here too when a code is due (`?mfa=1`) or failed (`?error=`).
 */
export function LoginPage() {
  const me = useContext(MeContext)
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const location = useLocation()
  const [params] = useSearchParams()
  const state = location.state as LoginState | null
  usePageTitle('Sign in · Emudoc')
  const next = safeNext(params.get('next'))
  const [codeStep, setCodeStep] = useState(params.has('mfa'))
  const signingOut = Boolean(me && state?.signOut)

  useEffect(() => {
    if (!signingOut) return
    signOut(queryClient)
    // The flag has done its work; left on this history entry, it would end the next sign-in too.
    navigate(location.pathname + location.search, { replace: true, state: null })
  }, [signingOut, queryClient, navigate, location.pathname, location.search])

  if (me && !signingOut) return <Navigate to={next} replace />
  return codeStep ? (
    <CodeStep next={next} onRestart={() => setCodeStep(false)} />
  ) : (
    <PasswordStep next={next} onCode={() => setCodeStep(true)} />
  )
}

function PasswordStep({ next, onCode }: { next: string; onCode: () => void }) {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const notice = (useLocation().state as LoginState | null)?.notice
  const options = useAuthOptions().data
  const closed = options && !options.signup_open ? options : null
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const login = useMutation({
    mutationFn: async () => {
      const { mfa_required } = await auth.login({ email: email.trim(), password })
      return mfa_required ? 'code' : await auth.me()
    },
    onSuccess: (result) => {
      if (result === 'code') return onCode()
      setSignedIn(queryClient, result)
      navigate(next, { replace: true })
    },
  })

  function submit(event: FormEvent) {
    event.preventDefault()
    if (email.trim() && password) login.mutate()
  }

  return (
    <AuthCard
      title="Sign in"
      footer={
        <>
          New to Emudoc?{' '}
          {closed ? (
            // While sign-up is closed, the way to ask for an account: a page of another site, a mail or the help's.
            <a href={closed.book_url} className="font-medium text-accent hover:underline">
              {closed.book_label}
            </a>
          ) : (
            <Link to={`/signup${params.has('next') ? `?${params}` : ''}`} className="font-medium text-accent hover:underline">
              Create an account
            </Link>
          )}
        </>
      }
    >
      {params.get('error') && (
        <div className="mb-4">
          <Notice tone="bad">{params.get('error')}</Notice>
        </div>
      )}
      {notice && (
        <div className="mb-4">
          <Notice tone="info">{notice}</Notice>
        </div>
      )}
      <ProviderButtons next={next} verb="Continue" />
      <form onSubmit={submit} className="space-y-3">
        <InputField
          label="Email"
          type="email"
          autoComplete="email"
          autoFocus
          required
          value={email}
          onChange={(event) => setEmail(event.target.value)}
        />
        <PasswordField
          label="Password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          corner={
            <Link to="/forgot-password" className="text-muted hover:text-ink">
              Forgot password?
            </Link>
          }
        />
        {login.isError && <Notice tone="bad">{login.error.message}</Notice>}
        <Button type="submit" variant="primary" className="w-full" disabled={login.isPending}>
          Sign in
        </Button>
      </form>
    </AuthCard>
  )
}

function CodeStep({ next, onRestart }: { next: string; onRestart: () => void }) {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [code, setCode] = useState('')
  const verify = useMutation({
    mutationFn: () => auth.verifyMfa(code.trim()),
    onSuccess: (me) => {
      setSignedIn(queryClient, me)
      navigate(next, { replace: true })
    },
  })

  function submit(event: FormEvent) {
    event.preventDefault()
    if (code.trim()) verify.mutate()
  }

  return (
    <AuthCard
      title="Enter your code"
      intro="Open your authenticator app and enter the code it shows for Emudoc. If you no longer have the app, enter one of your recovery codes instead."
      footer={
        <button type="button" onClick={onRestart} className="font-medium text-accent hover:underline">
          Start over
        </button>
      }
    >
      <form onSubmit={submit} className="space-y-3">
        <InputField
          label="Code"
          autoComplete="one-time-code"
          inputMode="text"
          autoFocus
          required
          spellCheck={false}
          className="font-mono"
          value={code}
          onChange={(event) => setCode(event.target.value)}
        />
        {verify.isError && <Notice tone="bad">{verify.error.message}</Notice>}
        <Button type="submit" variant="primary" className="w-full" disabled={verify.isPending}>
          Continue
        </Button>
      </form>
    </AuthCard>
  )
}
