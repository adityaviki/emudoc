// Shown instead of the app while the organisation the session works in requires two-factor sign-in
// and the account signs in without it (backend/app/security.py): it can set up an authenticator
// app, work in another of its organisations, leave this one, or sign out; the server refuses it
// everything else there (get_secured_account in backend/app/api/deps.py).

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { auth, organizations } from '../api.ts'
import type { Me, MfaSetup } from '../api.ts'
import { ME_KEY, startAgainInOrganization, useSignOut } from '../auth.ts'
import { ThemeToggle } from '../components/ThemeToggle.tsx'
import { Button, Notice } from '../components/ui.tsx'
import { RecoveryCodes, SetupStep } from './TwoFactor.tsx'
import { AuthCard, LegalLinks } from './shared.tsx'

type Phase = { kind: 'idle' } | { kind: 'setup'; setup: MfaSetup } | { kind: 'codes'; codes: string[] } | { kind: 'leaving' }

export function MfaPending({ me }: { me: Me }) {
  const queryClient = useQueryClient()
  const signOut = useSignOut()
  const [phase, setPhase] = useState<Phase>({ kind: 'idle' })
  const setup = useMutation({
    mutationFn: auth.setupMfa,
    onSuccess: (result) => setPhase({ kind: 'setup', setup: result }),
  })
  // A switch, or leaving, starts the app again in the organisation the session then works in.
  const switching = useMutation({ mutationFn: organizations.switch, onSettled: startAgainInOrganization })
  const leave = useMutation({ mutationFn: organizations.leave, onSuccess: startAgainInOrganization })
  const name = me.organization.name
  const others = me.organizations.filter((organization) => organization.id !== me.organization.id)
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
          {phase.kind === 'leaving' && (
            <div className="space-y-3 text-[13px]">
              <Notice tone="warn">
                Leave {name}? Your private chats in its projects go, the chats you shared stay. Only an invitation lets
                you back in.
              </Notice>
              {leave.isError && <Notice tone="bad">{leave.error.message}</Notice>}
              <div className="flex gap-2">
                <Button variant="danger" onClick={() => leave.mutate()} disabled={leave.isPending}>
                  Leave {name}
                </Button>
                <Button variant="ghost" onClick={() => setPhase({ kind: 'idle' })}>
                  Cancel
                </Button>
              </div>
            </div>
          )}
          {phase.kind === 'idle' && (
            <div className="space-y-4 text-[13px]">
              <Button variant="primary" className="w-full justify-center" onClick={() => setup.mutate()} disabled={setup.isPending}>
                Set up an authenticator app
              </Button>
              {setup.isError && <Notice tone="bad">{setup.error.message}</Notice>}
              {others.length > 0 && (
                <div className="space-y-2 border-t border-line pt-4">
                  <p className="text-muted">Or work in another of your organisations:</p>
                  <div className="flex flex-wrap gap-2">
                    {others.map((organization) => (
                      <Button key={organization.id} onClick={() => switching.mutate(organization.id)} disabled={switching.isPending}>
                        {organization.name}
                      </Button>
                    ))}
                  </div>
                </div>
              )}
              <p className="text-center text-muted">
                <button type="button" onClick={() => setPhase({ kind: 'leaving' })} className="hover:text-ink hover:underline">
                  Leave {name} instead
                </button>
              </p>
            </div>
          )}
        </AuthCard>
      </main>
      <LegalLinks />
    </div>
  )
}
