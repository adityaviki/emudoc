// Asking for the account to be deleted, confirmed by its password or, for an account that signs
// in only through a provider, by its address typed out. The account page offers it.

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import type { FormEvent } from 'react'
import { auth } from '../api.ts'
import type { Me } from '../api.ts'
import { setSignedIn } from '../auth.ts'
import { Button, InputField, Notice } from '../components/ui.tsx'
import { PasswordField } from './shared.tsx'

export function DeleteAccountForm({ me, onCancel }: { me: Me; onCancel: () => void }) {
  const queryClient = useQueryClient()
  const [confirmation, setConfirmation] = useState('')
  const remove = useMutation({
    mutationFn: () => auth.deleteAccount(me.has_password ? { password: confirmation } : { email: confirmation }),
    // The shell shows the page that offers to keep the account from now on.
    onSuccess: (deleted) => setSignedIn(queryClient, deleted),
  })

  function submit(event: FormEvent) {
    event.preventDefault()
    if (confirmation.trim()) remove.mutate()
  }

  return (
    <form onSubmit={submit} className="max-w-sm space-y-3">
      <Notice tone="warn">Every other browser and device is signed out now. Download your data first if you want a copy.</Notice>
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
      {remove.isError && <Notice tone="bad">{remove.error.message}</Notice>}
      <div className="flex gap-2">
        <Button type="submit" variant="danger" disabled={!confirmation.trim() || remove.isPending}>
          Delete my account
        </Button>
        <Button variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  )
}
