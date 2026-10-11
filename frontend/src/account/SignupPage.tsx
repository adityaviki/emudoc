import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useContext, useState } from 'react'
import type { FormEvent } from 'react'
import { Link, Navigate, useNavigate, useSearchParams } from 'react-router'
import { PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH, auth } from '../api.ts'
import { MeContext, safeNext, setSignedIn } from '../auth.ts'
import { Button, InputField, Notice } from '../components/ui.tsx'
import { AuthCard, PasswordField, ProviderButtons } from './shared.tsx'

/** A new account with an organisation of its own; the verification link follows by email. */
export function SignupPage() {
  const me = useContext(MeContext)
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const next = safeNext(params.get('next'))
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const signup = useMutation({
    mutationFn: () => auth.signup({ name: name.trim(), email: email.trim(), password }),
    onSuccess: (account) => {
      setSignedIn(queryClient, account)
      navigate(next, { replace: true })
    },
  })
  if (me) return <Navigate to={next} replace />

  function submit(event: FormEvent) {
    event.preventDefault()
    if (email.trim() && password.length >= PASSWORD_MIN_LENGTH) signup.mutate()
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
        {signup.isError && <Notice tone="bad">{signup.error.message}</Notice>}
        <Button type="submit" variant="primary" className="w-full" disabled={signup.isPending}>
          Create account
        </Button>
      </form>
    </AuthCard>
  )
}
