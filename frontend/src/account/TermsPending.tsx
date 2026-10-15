// Shown instead of the app while the account has not accepted the current terms of service and
// privacy policy: an account just made through Google or Microsoft, or one that accepted an older
// edition. It can accept them, take a copy of its data along, delete itself instead, or sign
// out; the server refuses it everything else (get_usable_account in backend/app/api/deps.py).

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Download } from 'lucide-react'
import { useState } from 'react'
import type { FormEvent } from 'react'
import { ACCOUNT_EXPORT_URL, ApiError, auth } from '../api.ts'
import type { Me } from '../api.ts'
import { setSignedIn, useSignOut } from '../auth.ts'
import { ThemeToggle } from '../components/ThemeToggle.tsx'
import { Button, ButtonLink, Checkbox, Notice } from '../components/ui.tsx'
import { useLegal } from '../hooks.ts'
import { DeleteAccountForm } from './DeleteAccount.tsx'
import { AuthCard, LegalLinks } from './shared.tsx'

export function TermsPending({ me }: { me: Me }) {
  const queryClient = useQueryClient()
  const signOut = useSignOut()
  const legal = useLegal()
  // The edition that was on show when the box was ticked: a newer one unticks it.
  const [acceptedVersion, setAcceptedVersion] = useState<string | null>(null)
  const accepted = acceptedVersion !== null && acceptedVersion === legal.data?.version
  const [leaving, setLeaving] = useState(false)
  const accept = useMutation({
    mutationFn: auth.acceptTerms,
    // The shell shows the app as soon as the user it holds has nothing due.
    onSuccess: (account) => setSignedIn(queryClient, account),
    onError: (error) => {
      if (error instanceof ApiError && error.status === 409) void queryClient.invalidateQueries({ queryKey: ['legal'] })
    },
  })
  // An account that accepted an earlier edition is told what changed; a new one, what to do.
  const changed = me.terms_version !== null

  function submit(event: FormEvent) {
    event.preventDefault()
    if (accepted && acceptedVersion) accept.mutate(acceptedVersion)
  }

  return (
    <div className="flex min-h-dvh flex-col">
      <div className="flex justify-end p-3">
        <ThemeToggle />
      </div>
      <main className="flex flex-1 flex-col items-center px-4 pt-6 pb-10 sm:pt-12">
        <div className="mb-6 flex items-center gap-2 text-lg font-semibold tracking-tight">
          <img src="/favicon.svg" alt="" className="size-7" />
          Emudoc
        </div>
        <AuthCard
          title={changed ? 'The terms have changed' : 'Accept the terms to start'}
          intro={
            changed ? (
              <>
                Emudoc's terms of service and privacy policy changed on{' '}
                <span className="font-medium text-ink">{legal.data?.published ?? '…'}</span>. Read them, and accept them
                to go on using Emudoc. If you do not, you can still download your data and delete your account.
              </>
            ) : (
              <>
                Before you use Emudoc as <span className="font-medium text-ink">{me.email}</span>, read and accept its
                terms of service and privacy policy.
              </>
            )
          }
          footer={
            <button type="button" onClick={signOut} className="font-medium text-accent hover:underline">
              Sign out
            </button>
          }
        >
          {leaving ? (
            <DeleteAccountForm me={me} onCancel={() => setLeaving(false)} />
          ) : (
            <form onSubmit={submit} className="space-y-3">
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
              {accept.isError && <Notice tone="bad">{accept.error.message}</Notice>}
              <Button type="submit" variant="primary" className="w-full justify-center" disabled={!accepted || accept.isPending}>
                Accept and continue
              </Button>
              <ButtonLink href={ACCOUNT_EXPORT_URL} download className="w-full justify-center" icon={<Download className="size-4" />}>
                Download your data
              </ButtonLink>
              <p className="pt-1 text-center text-[13px] text-muted">
                <button type="button" onClick={() => setLeaving(true)} className="hover:text-ink hover:underline">
                  Delete my account instead
                </button>
              </p>
            </form>
          )}
        </AuthCard>
      </main>
      <LegalLinks />
    </div>
  )
}
