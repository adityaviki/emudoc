// Under an answer about the organisation's own documents, the suggestion to invite its team
// (`inviteNudgeOf` in src/onboarding.ts): a form that sends the invitation from the chat when the
// plan has room for someone, else the way to a plan that has. An invitation takes the
// organisation's invite step, so the suggestion is made no more, though the chat keeps this one
// (`onInvited`) for the member to read where the invitation went; hiding it is the member's
// choice for every organisation, kept on the account.

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { UserPlus, X } from 'lucide-react'
import { useState } from 'react'
import type { FormEvent } from 'react'
import { Link } from 'react-router'
import { ROLES, ROLE_LABELS, auth, isPlanLimit, isWithin, organizations } from '../api.ts'
import type { Onboarding, Role } from '../api.ts'
import { setSignedIn, useMe } from '../auth.ts'
import { PlanLimitNotice, PlanOffer } from '../components/PlanLimit.tsx'
import { Button, IconButton, InputField, Notice, SelectField } from '../components/ui.tsx'
import { INVITATIONS_SECTION, JOINING_SECTION } from '../onboarding.ts'

const LINK = 'font-medium text-accent hover:underline'

export function InviteTeam({ room, onInvited }: { room: boolean; onInvited: () => void }) {
  const me = useMe()
  const queryClient = useQueryClient()
  const [email, setEmail] = useState('')
  const [role, setRole] = useState<Role>('member')
  const invite = useMutation({
    mutationFn: () => organizations.invite({ email: email.trim(), role }),
    onSuccess: () => {
      setEmail('')
      onInvited()
      queryClient.setQueryData<Onboarding>(['onboarding', me.organization.id], (data) =>
        data && { steps: data.steps.map((step) => (step.key === 'invite' ? { ...step, done: true } : step)) },
      )
    },
  })
  const hide = useMutation({
    mutationFn: () => auth.chooseOnboarding({ invite_hidden: true }),
    onSuccess: (changed) => setSignedIn(queryClient, changed),
  })

  function submit(event: FormEvent) {
    event.preventDefault()
    if (email.trim()) invite.mutate()
  }

  return (
    <section aria-labelledby="invite-team" className="rounded-xl border border-line bg-surface p-4 shadow-xs">
      <div className="flex items-start gap-3">
        <div className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-accent-soft text-accent">
          <UserPlus className="size-4" />
        </div>
        <div className="min-w-0 flex-1">
          <h2 id="invite-team" className="text-[13px] font-semibold">
            Invite your team
          </h2>
          <p className="mt-0.5 text-[13px] leading-relaxed text-muted">
            Colleagues you invite to {me.organization.name} ask about its documents too, and read the chats you share
            with a project.
          </p>
        </div>
        <IconButton label="Hide the suggestion to invite your team" onClick={() => hide.mutate()} disabled={hide.isPending}>
          <X className="size-4" />
        </IconButton>
      </div>
      <div className="mt-3 text-[13px] sm:pl-11">
        {invite.isSuccess ? (
          <p className="text-muted">
            An invitation is on its way to {invite.data.email}.{' '}
            <Link to={`/organization#${INVITATIONS_SECTION}`} className={LINK}>
              See the invitations
            </Link>{' '}
            or{' '}
            <button type="button" onClick={() => invite.reset()} className={LINK}>
              invite someone else
            </button>
            .
          </p>
        ) : room ? (
          <>
            <form onSubmit={submit} className="flex flex-wrap items-end gap-2">
              <InputField
                label="Email"
                type="email"
                required
                autoComplete="off"
                className="min-w-48 flex-1"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
              />
              <SelectField label="Role" value={role} onChange={(event) => setRole(event.target.value as Role)}>
                {ROLES.filter((option) => isWithin(option, me.role)).map((option) => (
                  <option key={option} value={option}>
                    {ROLE_LABELS[option]}
                  </option>
                ))}
              </SelectField>
              <Button type="submit" variant="primary" disabled={!email.trim() || invite.isPending}>
                Invite
              </Button>
            </form>
            {invite.isError && (
              <div className="mt-2">
                {isPlanLimit(invite.error) ? (
                  <PlanLimitNotice refusal={invite.error} onDismiss={() => invite.reset()} />
                ) : (
                  <Notice tone="bad">{invite.error.message}</Notice>
                )}
              </div>
            )}
            <p className="mt-2 text-xs text-muted">
              Or let everyone at your company’s domain join, on the{' '}
              <Link to={`/organization#${JOINING_SECTION}`} className={LINK}>
                organisation page
              </Link>
              .
            </p>
          </>
        ) : (
          <div className="text-muted">
            <p>Its plan has room for you alone.</p>
            <PlanOffer limit="members" />
          </div>
        )}
        {hide.isError && <p className="mt-2 text-bad">{hide.error.message}</p>}
      </div>
    </section>
  )
}
