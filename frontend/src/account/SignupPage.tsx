import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useContext, useState } from 'react'
import type { FormEvent } from 'react'
import { Link, Navigate, useNavigate, useSearchParams } from 'react-router'
import { ApiError, PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH, auth } from '../api.ts'
import { MeContext, forgetUser, safeNext, setSignedIn } from '../auth.ts'
import { Button, Checkbox, InputField, Notice, Spinner } from '../components/ui.tsx'
import { useLegal } from '../hooks.ts'
import { browserTimeZone } from '../timeZones.ts'
import { useLinkToken } from './linkToken.ts'
import { AuthCard, PasswordField, ProviderButtons } from './shared.tsx'

const ASK_AGAIN = (
  <Link to="/signup" className="font-medium text-accent hover:underline">
    Ask for a new link
  </Link>
)

/**
 * The start of a new account: the address alone, which is mailed the link to the page that makes
 * the account (FinishSignupPage). The server answers alike whether the address has an account or
 * not, and this page says the same either way; the mail tells an address that has one how to sign
 * in instead. An account made through a provider needs no link.
 */
export function SignupPage() {
  const me = useContext(MeContext)
  const [params] = useSearchParams()
  const next = safeNext(params.get('next'))
  const [email, setEmail] = useState('')
  const signup = useMutation({ mutationFn: () => auth.signup(email.trim()) })
  if (me) return <Navigate to={next} replace />

  function submit(event: FormEvent) {
    event.preventDefault()
    if (email.trim()) signup.mutate()
  }

  if (signup.isSuccess) {
    return (
      <AuthCard
        title="Check your email"
        footer={
          <button type="button" onClick={() => signup.reset()} className="font-medium text-accent hover:underline">
            Use another address
          </button>
        }
      >
        <p className="text-[13px] leading-relaxed text-muted">
          A message is on its way to <span className="font-medium text-ink">{email.trim()}</span>. Open the link in it
          to choose your password and finish; it works for 24 hours. If the address has an account already, the message
          says how to sign in instead. If nothing arrives, look in the spam folder or check the address.
        </p>
      </AuthCard>
    )
  }

  return (
    <AuthCard
      title="Create your account"
      intro="You get a workspace of your own to upload documents into and ask questions about."
      footer={
        <>
          Already have an account?{' '}
          <Link to={`/login${params.has('next') ? `?${params}` : ''}`} className="font-medium text-accent hover:underline">
            Sign in
          </Link>
        </>
      }
    >
      <ProviderButtons next={next} verb="Sign up" />
      <form onSubmit={submit} className="space-y-3">
        <InputField
          label="Email"
          type="email"
          autoComplete="email"
          autoFocus
          required
          hint="We send a link there to confirm it is yours, and you choose your password on its page."
          value={email}
          onChange={(event) => setEmail(event.target.value)}
        />
        {signup.isError && <Notice tone="bad">{signup.error.message}</Notice>}
        <Button type="submit" variant="primary" className="w-full" disabled={signup.isPending}>
          Continue
        </Button>
      </form>
    </AuthCard>
  )
}

/**
 * The page a sign-up link opens, signed in or not: whoever holds the address chooses the name and
 * the password and accepts the terms, and the account is made and signed in. The address is shown
 * first, which leaves the link unused; a refused password or a newer edition of the terms leaves it
 * working too. The box accepts the edition the page shows, which the server records.
 */
export function FinishSignupPage() {
  const me = useContext(MeContext)
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const token = useLinkToken()
  const link = useQuery({
    queryKey: ['signup-link', token],
    queryFn: () => auth.signupLink(token),
    enabled: token !== '',
    retry: false,
    staleTime: Infinity,
  })
  const [name, setName] = useState('')
  const [password, setPassword] = useState('')
  const legal = useLegal()
  // The edition that was on show when the box was ticked: a newer one unticks it.
  const [acceptedVersion, setAcceptedVersion] = useState<string | null>(null)
  const accepted = acceptedVersion !== null && acceptedVersion === legal.data?.version
  const finish = useMutation({
    mutationFn: (terms_version: string) =>
      auth.finishSignup({ token, name: name.trim(), password, terms_version, time_zone: browserTimeZone() }),
    onSuccess: (account) => {
      // Whoever was signed in in this tab before is not the new account.
      forgetUser(queryClient)
      setSignedIn(queryClient, account)
      navigate('/', { replace: true })
    },
    // The terms may have changed while the page was open; fetching them again unticks the box.
    onError: (error) => {
      if (error instanceof ApiError && error.status === 409) void queryClient.invalidateQueries({ queryKey: ['legal'] })
    },
  })

  function submit(event: FormEvent) {
    event.preventDefault()
    if (password.length >= PASSWORD_MIN_LENGTH && accepted && acceptedVersion) finish.mutate(acceptedVersion)
  }

  if (!token) {
    return (
      <AuthCard title="This link is incomplete" footer={ASK_AGAIN}>
        <p className="text-[13px] leading-relaxed text-muted">
          The address is missing its token, which can happen when a link is copied by hand. Open the link from the email
          again.
        </p>
      </AuthCard>
    )
  }

  if (link.isPending) {
    return (
      <AuthCard title="Opening your link">
        <div className="flex justify-center py-4">
          <Spinner className="size-5" />
        </div>
      </AuthCard>
    )
  }

  if (link.isError) {
    return (
      <AuthCard
        title="This link no longer works"
        footer={
          // Most often the account was just made with it, in this browser.
          me ? (
            <Link to="/" className="font-medium text-accent hover:underline">
              Continue to your projects
            </Link>
          ) : (
            <>
              <Link to="/login" className="font-medium text-accent hover:underline">
                Sign in
              </Link>{' '}
              · {ASK_AGAIN}
            </>
          )
        }
      >
        <Notice tone="bad">{link.error.message}</Notice>
      </AuthCard>
    )
  }

  const email = link.data.email
  return (
    <AuthCard
      title="Finish creating your account"
      intro={
        <>
          For <span className="font-medium text-ink">{email}</span>. You get a workspace of your own to upload documents
          into and ask questions about.
        </>
      }
    >
      <form onSubmit={submit} className="space-y-3">
        {me && (
          <Notice tone="info">
            This browser is signed in as {me.email}. Creating the account signs it in to the new one instead.
          </Notice>
        )}
        {/* For a password manager, which saves the new password under the address it finds here. */}
        <input type="email" autoComplete="username" value={email} readOnly hidden />
        <InputField
          label="Name"
          autoComplete="name"
          autoFocus
          maxLength={200}
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
        <PasswordField
          label="Password"
          autoComplete="new-password"
          required
          minLength={PASSWORD_MIN_LENGTH}
          maxLength={PASSWORD_MAX_LENGTH}
          hint={`At least ${PASSWORD_MIN_LENGTH} characters; a sentence you can remember is a good choice.`}
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />
        <Checkbox
          required
          disabled={!legal.data}
          checked={accepted}
          onChange={(event) => setAcceptedVersion(event.target.checked ? (legal.data?.version ?? null) : null)}
          label={
            <>
              I accept the{' '}
              <a href="/terms" target="_blank" className="font-medium text-accent hover:underline">
                terms of service
              </a>{' '}
              and have read the{' '}
              <a href="/privacy" target="_blank" className="font-medium text-accent hover:underline">
                privacy policy
              </a>
              .
            </>
          }
        />
        {legal.isError && <Notice tone="bad">{legal.error.message}</Notice>}
        {finish.isError && (
          <Notice tone="bad">
            {finish.error.message}
            {/* A link that was used or ran out meanwhile; any other refusal leaves it working. */}
            {finish.error instanceof ApiError && finish.error.status === 400 && (
              <>
                {' '}
                <Link to="/signup" className="font-medium underline">
                  Ask for a new link
                </Link>
              </>
            )}
          </Notice>
        )}
        <Button type="submit" variant="primary" className="w-full" disabled={finish.isPending || !legal.data}>
          Create account
        </Button>
      </form>
    </AuthCard>
  )
}
