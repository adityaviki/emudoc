// A forgotten password: ask for the link, then set the new password from it.

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useNavigate } from 'react-router'
import { PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH, auth } from '../api.ts'
import { setSignedIn } from '../auth.ts'
import { Button, InputField, Notice } from '../components/ui.tsx'
import { useLinkToken } from './linkToken.ts'
import { AuthCard, PasswordField } from './shared.tsx'

const BACK_TO_LOGIN = (
  <Link to="/login" className="font-medium text-accent hover:underline">
    Back to sign in
  </Link>
)

export function ForgotPasswordPage() {
  const [email, setEmail] = useState('')
  const forgot = useMutation({ mutationFn: () => auth.forgotPassword(email.trim()) })

  function submit(event: FormEvent) {
    event.preventDefault()
    if (email.trim()) forgot.mutate()
  }

  if (forgot.isSuccess) {
    return (
      <AuthCard title="Check your email" footer={BACK_TO_LOGIN}>
        <p className="text-[13px] leading-relaxed text-muted">
          If an account exists for <span className="font-medium text-ink">{email.trim()}</span>, a link to set a new
          password is on its way. It works for one hour. If nothing arrives, look in the spam folder or check the
          address.
        </p>
      </AuthCard>
    )
  }

  return (
    <AuthCard
      title="Reset your password"
      intro="Enter the email address of your account and we send you a link to set a new password."
      footer={BACK_TO_LOGIN}
    >
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
        {forgot.isError && <Notice tone="bad">{forgot.error.message}</Notice>}
        <Button type="submit" variant="primary" className="w-full" disabled={forgot.isPending}>
          Send the link
        </Button>
      </form>
    </AuthCard>
  )
}

/** The page a reset link opens. Setting the password signs the account out everywhere. */
export function ResetPasswordPage() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const token = useLinkToken()
  const [password, setPassword] = useState('')
  const reset = useMutation({
    mutationFn: () => auth.resetPassword({ token, password }),
    onSuccess: () => {
      // Every session was just revoked, this browser's included.
      setSignedIn(queryClient, null)
      navigate('/login', { replace: true, state: { notice: 'Your password is changed. Sign in with it.' } })
    },
  })

  function submit(event: FormEvent) {
    event.preventDefault()
    if (password.length >= PASSWORD_MIN_LENGTH) reset.mutate()
  }

  if (!token) {
    return (
      <AuthCard title="This link is incomplete" footer={BACK_TO_LOGIN}>
        <p className="text-[13px] leading-relaxed text-muted">
          The address is missing its token, which can happen when a link is copied by hand.{' '}
          <Link to="/forgot-password" className="font-medium text-accent hover:underline">
            Request a new link
          </Link>
          .
        </p>
      </AuthCard>
    )
  }

  return (
    <AuthCard
      title="Choose a new password"
      intro="Every browser and device that is signed in to your account is signed out when you set it."
      footer={BACK_TO_LOGIN}
    >
      <form onSubmit={submit} className="space-y-3">
        <PasswordField
          label="New password"
          autoComplete="new-password"
          autoFocus
          required
          minLength={PASSWORD_MIN_LENGTH}
          maxLength={PASSWORD_MAX_LENGTH}
          hint={`At least ${PASSWORD_MIN_LENGTH} characters.`}
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />
        {reset.isError && (
          <Notice tone="bad">
            {reset.error.message}{' '}
            <Link to="/forgot-password" className="font-medium underline">
              Request a new link
            </Link>
          </Notice>
        )}
        <Button type="submit" variant="primary" className="w-full" disabled={reset.isPending}>
          Set the password
        </Button>
      </form>
    </AuthCard>
  )
}
