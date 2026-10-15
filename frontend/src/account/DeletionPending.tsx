// Shown instead of the app while the account is set to be deleted: the day it goes, and what it
// can still do until then, which is keep itself, take a copy of its data along, or sign out.

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Download } from 'lucide-react'
import { ACCOUNT_EXPORT_URL, auth } from '../api.ts'
import type { Me } from '../api.ts'
import { setSignedIn, useSignOut } from '../auth.ts'
import { ThemeToggle } from '../components/ThemeToggle.tsx'
import { Button, ButtonLink, Notice } from '../components/ui.tsx'
import { formatDate } from '../format.ts'
import { AuthCard } from './shared.tsx'

export function DeletionPending({ me }: { me: Me }) {
  const queryClient = useQueryClient()
  const signOut = useSignOut()
  const keep = useMutation({
    mutationFn: auth.keepAccount,
    // The shell shows the app again as soon as the user it holds has nothing due.
    onSuccess: (kept) => setSignedIn(queryClient, kept),
  })

  return (
    <div className="flex min-h-dvh flex-col">
      <div className="flex justify-end p-3">
        <ThemeToggle />
      </div>
      <main className="flex flex-1 flex-col items-center px-4 pt-6 pb-16 sm:pt-12">
        <div className="mb-6 flex items-center gap-2 text-lg font-semibold tracking-tight">
          <img src="/favicon.svg" alt="" className="size-7" />
          Emudoc
        </div>
        <AuthCard
          title="Your account is set to be deleted"
          intro={
            <>
              On <span className="font-medium text-ink">{formatDate(me.deletion_due_at)}</span>, the account{' '}
              <span className="font-medium text-ink">{me.email}</span> goes for good. If you are the only member of{' '}
              {me.organization.name}, it goes too, with its projects, documents and chats. Nothing is removed before
              then, and you can keep the account.
            </>
          }
          footer={
            <button type="button" onClick={signOut} className="font-medium text-accent hover:underline">
              Sign out
            </button>
          }
        >
          <div className="space-y-3">
            {keep.isError && <Notice tone="bad">{keep.error.message}</Notice>}
            <Button variant="primary" className="w-full justify-center" onClick={() => keep.mutate()} disabled={keep.isPending}>
              Keep my account
            </Button>
            <ButtonLink href={ACCOUNT_EXPORT_URL} download className="w-full justify-center" icon={<Download className="size-4" />}>
              Download your data
            </ButtonLink>
          </div>
        </AuthCard>
      </main>
    </div>
  )
}
