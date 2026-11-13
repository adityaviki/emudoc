import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useContext, useState } from 'react'
import type { FormEvent } from 'react'
import { Link, Navigate, useNavigate } from 'react-router'
import { ApiError, PASSWORD_MIN_LENGTH, auth } from '../api.ts'
import type { AuthOptions } from '../api.ts'
import { MeContext, setSignedIn } from '../auth.ts'
import { Button, InputField, Notice, Spinner } from '../components/ui.tsx'
import { usePageTitle } from '../hooks.ts'
import { browserTimeZone } from '../timeZones.ts'
import { AUTH_OPTIONS_KEY, useAuthOptions } from './authOptions.ts'
import { AuthCard } from './shared.tsx'
import { NewPasswordField } from './SignupPage.tsx'

const SIGN_IN = (
  <Link to="/login" className="font-medium text-accent hover:underline">
    Sign in
  </Link>
)

/**
 * The first page of a new install, which every page sends to while it has no account (backend/app/signups.py):
 * whoever runs it makes the admin's account and names the team's workspace, and is signed in. The server refuses
 * it once any account exists, and the page then says so.
 */
export function SetupPage() {
  const me = useContext(MeContext)
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const options = useAuthOptions()
  const [workspace, setWorkspace] = useState('')
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const setup = useMutation({
    mutationFn: () =>
      auth.setup({
        email: email.trim(),
        name: name.trim(),
        password,
        workspace: workspace.trim(),
        time_zone: browserTimeZone(),
      }),
    onSuccess: (account) => {
      // Nothing sends here any more, whoever signs in next in this tab.
      queryClient.setQueryData<AuthOptions>(AUTH_OPTIONS_KEY, (known) => known && { ...known, setup_needed: false })
      setSignedIn(queryClient, account)
      navigate('/', { replace: true })
    },
    // Someone set the install up meanwhile, which the page then says.
    onError: (error) => {
      if (error instanceof ApiError && error.status === 409) {
        void queryClient.invalidateQueries({ queryKey: AUTH_OPTIONS_KEY })
      }
    },
  })
  usePageTitle('Set up Emudoc')

  if (me) return <Navigate to="/" replace />
  if (options.isPending) {
    return (
      <AuthCard title="Set up Emudoc">
        <Spinner />
      </AuthCard>
    )
  }
  if (options.data && !options.data.setup_needed) {
    return (
      <AuthCard title="Emudoc is set up" footer={SIGN_IN}>
        <p className="text-[13px] leading-relaxed text-muted">
          Its admin made the first account already. Sign in, or ask the admin for an invitation.
        </p>
      </AuthCard>
    )
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    if (workspace.trim() && email.trim() && password.length >= PASSWORD_MIN_LENGTH) setup.mutate()
  }

  return (
    <AuthCard
      title="Set up Emudoc"
      intro="Make the admin's account and name your team's workspace. It starts with a sample project to try questions on, and you invite your team from it."
    >
      <form onSubmit={submit} className="space-y-3">
        <InputField
          label="Workspace name"
          autoComplete="organization"
          autoFocus
          required
          maxLength={200}
          hint="Your team's or your company's name, which everyone you invite sees. You can change it later."
          value={workspace}
          onChange={(event) => setWorkspace(event.target.value)}
        />
        <InputField
          label="Your name"
          autoComplete="name"
          maxLength={200}
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
        <InputField
          label="Email"
          type="email"
          autoComplete="username"
          required
          value={email}
          onChange={(event) => setEmail(event.target.value)}
        />
        <NewPasswordField value={password} onChange={setPassword} />
        {setup.isError && <Notice tone="bad">{setup.error.message}</Notice>}
        <Button type="submit" variant="primary" className="w-full" disabled={setup.isPending}>
          Create the workspace
        </Button>
      </form>
    </AuthCard>
  )
}
