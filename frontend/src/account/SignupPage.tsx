import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useContext, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import { Link, Navigate, useNavigate, useSearchParams } from 'react-router'
import { ApiError, PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH, auth } from '../api.ts'
import { MeContext, forgetUser, safeNext, setSignedIn } from '../auth.ts'
import { Button, ButtonLink, InputField, Notice, Spinner } from '../components/ui.tsx'
import { usePageTitle } from '../hooks.ts'
import { browserTimeZone } from '../timeZones.ts'
import { useLinkToken } from './linkToken.ts'
import { AuthCard, PasswordField, ProviderButtons } from './shared.tsx'
import { useAuthOptions } from './authOptions.ts'
import { useTermsBox } from './termsBox.tsx'

const ASK_AGAIN = (
  <Link to="/signup" className="font-medium text-accent hover:underline">
    Ask for a new link
  </Link>
)

/**
 * The start of a new account: the address alone, which is mailed the link to the page that makes
 * the account (FinishSignupPage). The server answers alike whether the address has an account or
 * not, and this page says the same either way; the mail tells an address that has one how to sign
 * in instead. An account made through a provider needs no link. The account joins the team's workspace. While
 * sign-up is closed, as an install starts, the page says to ask the team's admin, with the way to ask
 * (backend/app/signups.py).
 */
export function SignupPage() {
  const me = useContext(MeContext)
  const [params] = useSearchParams()
  const next = safeNext(params.get('next'))
  const [email, setEmail] = useState('')
  const signup = useMutation({ mutationFn: () => auth.signup(email.trim()) })
  const options = useAuthOptions()
  usePageTitle('Sign up · Emudoc')
  if (me) return <Navigate to={next} replace />
  if (options.data?.setup_needed) return <Navigate to="/setup" replace />
  const signIn = (
    <>
      Already have an account?{' '}
      <Link to={`/login${params.has('next') ? `?${params}` : ''}`} className="font-medium text-accent hover:underline">
        Sign in
      </Link>
    </>
  )

  if (options.isPending) {
    return (
      <AuthCard title="Create your account">
        <Spinner />
      </AuthCard>
    )
  }
  if (options.data && !options.data.signup_open) {
    return (
      <AuthCard title="Accounts are made by invitation" intro="Your team's admin invites the people who work here." footer={signIn}>
        <p className="text-[13px] leading-relaxed text-muted">
          Ask them to invite you. Invited already? Open the link in the invitation, from its email or wherever the admin
          sent it.
        </p>
        <ButtonLink href={options.data.book_url} variant="primary" className="mt-5 w-full">
          {options.data.book_label}
        </ButtonLink>
      </AuthCard>
    )
  }

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
      intro="You join your team's workspace, to upload documents into and ask questions about."
      footer={signIn}
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
 * working too.
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
  const finish = useMutation({
    mutationFn: (fields: NewAccount) => auth.finishSignup({ token, ...fields, time_zone: browserTimeZone() }),
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

  if (link.isPending) return <OpeningLink />

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
          For <span className="font-medium text-ink">{email}</span>. You join your team's workspace, to upload documents
          into and ask questions about.
        </>
      }
    >
      <NewAccountForm
        email={email}
        pending={finish.isPending}
        onSubmit={(fields) => finish.mutate(fields)}
        error={
          finish.isError && (
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
          )
        }
      >
        {me && (
          <Notice tone="info">
            This browser is signed in as {me.email}. Creating the account signs it in to the new one instead.
          </Notice>
        )}
      </NewAccountForm>
    </AuthCard>
  )
}

/** The card a link's page shows while the server says what the link is for. */
export function OpeningLink() {
  return (
    <AuthCard title="Opening your link">
      <div className="flex justify-center py-4">
        <Spinner className="size-5" />
      </div>
    </AuthCard>
  )
}

/** What a new account is made with, besides the link's token and the browser's zone. */
export interface NewAccount {
  name: string
  password: string
  terms_version: string
}

interface NewAccountFormProps {
  /** The address the account is made with, for a password manager to save the password under. */
  email: string
  pending: boolean
  onSubmit: (account: NewAccount) => void
  /** Why the last try was refused, under the fields. */
  error?: ReactNode
  /** Above the fields, such as who is signed in in this browser. */
  children?: ReactNode
}

/** The name, the password and the acceptance of the terms that a sign-up link's page and an invitation's page make an account with. */
export function NewAccountForm({ email, pending, onSubmit, error, children }: NewAccountFormProps) {
  const [name, setName] = useState('')
  const [password, setPassword] = useState('')
  const terms = useTermsBox()

  function submit(event: FormEvent) {
    event.preventDefault()
    if (password.length >= PASSWORD_MIN_LENGTH && terms.version) {
      onSubmit({ name: name.trim(), password, terms_version: terms.version })
    }
  }

  return (
    <form onSubmit={submit} className="space-y-3">
      {children}
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
      <NewPasswordField value={password} onChange={setPassword} />
      {terms.box}
      {error}
      <Button type="submit" variant="primary" className="w-full" disabled={pending || !terms.ready}>
        Create account
      </Button>
    </form>
  )
}

/** The password a new account chooses, with what it takes. */
export function NewPasswordField({ value, onChange }: { value: string; onChange: (password: string) => void }) {
  return (
    <PasswordField
      label="Password"
      autoComplete="new-password"
      required
      minLength={PASSWORD_MIN_LENGTH}
      maxLength={PASSWORD_MAX_LENGTH}
      hint={`At least ${PASSWORD_MIN_LENGTH} characters; a sentence you can remember is a good choice.`}
      value={value}
      onChange={(event) => onChange(event.target.value)}
    />
  )
}
