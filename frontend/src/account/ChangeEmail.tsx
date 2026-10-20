// Moving the account to a new address: the account page asks for it, confirmed by the password
// or, for an account that signs in only through a provider, by its address typed out, and the
// link mailed to the new address opens ConfirmEmailPage, signed in or not.

import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import type { FormEvent } from 'react'
import { auth } from '../api.ts'
import type { Me } from '../api.ts'
import { Button, InputField, Notice } from '../components/ui.tsx'
import { PasswordField } from './shared.tsx'

export function ChangeEmailForm({ me, onClose }: { me: Me; onClose: () => void }) {
  const [email, setEmail] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [sentTo, setSentTo] = useState('')
  const change = useMutation({
    mutationFn: () =>
      auth.requestEmailChange(
        me.has_password ? { email: email.trim(), password: confirmation } : { email: email.trim(), current_email: confirmation },
      ),
    onSuccess: () => setSentTo(email.trim()),
  })

  function submit(event: FormEvent) {
    event.preventDefault()
    if (email.trim() && confirmation.trim()) change.mutate()
  }

  // The server answers alike whether or not the address has an account, and so does this.
  if (sentTo) {
    return (
      <div className="max-w-md space-y-3">
        <Notice tone="info">
          Look for a message from Emudoc at {sentTo}. Its link makes that your account's address and works for 24
          hours; until it is opened, Emudoc keeps writing to {me.email}.
        </Notice>
        <Button onClick={onClose}>Done</Button>
      </div>
    )
  }

  return (
    <form onSubmit={submit} className="max-w-sm space-y-3">
      <InputField
        label="New email address"
        type="email"
        autoComplete="email"
        required
        autoFocus
        value={email}
        onChange={(event) => setEmail(event.target.value)}
      />
      {me.has_password ? (
        <PasswordField
          label="Your password"
          autoComplete="current-password"
          required
          value={confirmation}
          onChange={(event) => setConfirmation(event.target.value)}
        />
      ) : (
        <InputField
          label={`Type ${me.email} to confirm`}
          autoComplete="off"
          required
          value={confirmation}
          onChange={(event) => setConfirmation(event.target.value)}
        />
      )}
      {change.isError && <Notice tone="bad">{change.error.message}</Notice>}
      <div className="flex gap-2">
        <Button type="submit" variant="primary" disabled={!email.trim() || !confirmation.trim() || change.isPending}>
          Send a confirmation link
        </Button>
        <Button variant="ghost" onClick={onClose}>
          Cancel
        </Button>
      </div>
    </form>
  )
}
