// Shown instead of the app to a member an admin removed from the workspace: the account stays, in
// no workspace, until an admin invites its address again, whose link brings it back
// (backend/app/api/deps.py). Signing out is all it can do here.

import type { Outsider } from '../api.ts'
import { useSignOut } from '../auth.ts'
import { ThemeToggle } from '../components/ThemeToggle.tsx'
import { Button } from '../components/ui.tsx'
import { AuthCard } from './shared.tsx'

export function NoWorkspace({ me }: { me: Outsider }) {
  const signOut = useSignOut()

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
          title="You are not in a workspace"
          intro={
            <>
              The account <span className="font-medium text-ink">{me.email}</span> no longer belongs to this server’s
              workspace: an admin removed it. Ask an admin of the workspace to invite you again. The invitation’s link
              brings you back, signed in as you are now.
            </>
          }
        >
          <Button className="w-full justify-center" onClick={signOut}>
            Sign out
          </Button>
        </AuthCard>
      </main>
    </div>
  )
}
