import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useContext, useState } from 'react'
import type { FormEvent } from 'react'
import { Link, Navigate, useNavigate, useSearchParams } from 'react-router'
import { ApiError, PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH, auth } from '../api.ts'
import { MeContext, safeNext, setSignedIn } from '../auth.ts'
import { Button, Checkbox, InputField, Notice } from '../components/ui.tsx'
import { useLegal } from '../hooks.ts'
import { browserTimeZone } from '../timeZones.ts'
import { AuthCard, PasswordField, ProviderButtons } from './shared.tsx'

/**
 * A new account with an organisation of its own; the verification link follows by email. The
 * box accepts the edition of the terms the form shows, which the server records; an account
 * made through a provider accepts them on its first visit instead (TermsPending).
 */
export function SignupPage() {
  const me = useContext(MeContext)
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const next = safeNext(params.get('next'))
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const legal = useLegal()
  // The edition that was on show when the box was ticked: a newer one unticks it.
  const [acceptedVersion, setAcceptedVersion] = useState<string | null>(null)
  const accepted = acceptedVersion !== null && acceptedVersion === legal.data?.version
  const signup = useMutation({
    mutationFn: (terms_version: string) =>
      auth.signup({ name: name.trim(), email: email.trim(), password, terms_version, time_zone: browserTimeZone() }),
    onSuccess: (account) => {
      setSignedIn(queryClient, account)
      navigate(next, { replace: true })
    },
    // The terms may have changed while the form was open; fetching them again unticks the box.
    onError: (error) => {
      if (error instanceof ApiError && error.status === 409) void queryClient.invalidateQueries({ queryKey: ['legal'] })
    },
  })
  if (me) return <Navigate to={next} replace />

  function submit(event: FormEvent) {
    event.preventDefault()
    if (email.trim() && password.length >= PASSWORD_MIN_LENGTH && accepted && acceptedVersion) signup.mutate(acceptedVersion)
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
          label="Name"
          autoComplete="name"
          autoFocus
          maxLength={200}
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
        <InputField
          label="Email"
          type="email"
          autoComplete="email"
          required
          value={email}
          onChange={(event) => setEmail(event.target.value)}
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
        {signup.isError && <Notice tone="bad">{signup.error.message}</Notice>}
        <Button type="submit" variant="primary" className="w-full" disabled={signup.isPending || !legal.data}>
          Create account
        </Button>
      </form>
    </AuthCard>
  )
}
