// Shown instead of the app while the workspace requires two-factor sign-in and the account signs in
// without it (backend/app/security.py): it can set up an authenticator app or sign out; the server
// refuses it everything else (get_secured_account in backend/app/api/deps.py).

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { auth } from '../api.ts'
import type { Me, MfaSetup } from '../api.ts'
import { ME_KEY, useSignOut } from '../auth.ts'
import { ThemeToggle } from '../components/ThemeToggle.tsx'
import { Button, Notice } from '../components/ui.tsx'
import { RecoveryCodes, SetupStep } from './TwoFactor.tsx'
import { AuthCard, LegalLinks } from './shared.tsx'

type Phase = { kind: 'idle' } | { kind: 'setup'; setup: MfaSetup } | { kind: 'codes'; codes: string[] }

export function MfaPending({ me }: { me: Me }) {
  const queryClient = useQueryClient()
  const signOut = useSignOut()
  const [phase, setPhase] = useState<Phase>({ kind: 'idle' })
  const setup = useMutation({
    mutationFn: auth.setupMfa,
    onSuccess: (result) => setPhase({ kind: 'setup', setup: result }),
  })
  const name = me.organization.name
  const factor = me.has_password ? 'your password' : 'your Google or Microsoft account'

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
          wide
          title="Set up two-factor sign-in"
          intro={
            <>
              <span className="font-medium text-ink">{name}</span> asks everyone who works in it to sign in with a code
              from an authenticator app as well as {factor}. Set one up to go on working in it.
            </>
          }
          footer={
            <button type="button" onClick={signOut} className="font-medium text-accent hover:underline">
              Sign out
            </button>
          }
        >
          {phase.kind === 'setup' && (
            <SetupStep setup={phase.setup} onCancel={() => setPhase({ kind: 'idle' })} onEnabled={(codes) => setPhase({ kind: 'codes', codes })} />
          )}
          {/* The app opens once the codes are saved: asking who is signed in sooner would take them off the screen. */}
          {phase.kind === 'codes' && (
            <RecoveryCodes codes={phase.codes} onDone={() => void queryClient.invalidateQueries({ queryKey: ME_KEY })} />
          )}
          {phase.kind === 'idle' && (
            <div className="space-y-4 text-[13px]">
              <Button variant="primary" className="w-full justify-center" onClick={() => setup.mutate()} disabled={setup.isPending}>
                Set up an authenticator app
              </Button>
              {setup.isError && <Notice tone="bad">{setup.error.message}</Notice>}
            </div>
          )}
        </AuthCard>
      </main>
      <LegalLinks />
    </div>
  )
}
