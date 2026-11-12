// The chat's suggestion to invite the team, under an answer, while the organisation's invite step
// (backend/app/onboarding.py) is untaken.

import { isWithin } from './api.ts'
import type { Allowance, Me, OnboardingStep, Project } from './api.ts'
import type { Message } from './chat.ts'

/** The id of the organisation page's section that invites people (`InvitationsSection`), which a link opens the page at. */
export const INVITATIONS_SECTION = 'invitations'

/** What of the account the suggestion to invite the team reads. */
export type Inviter = Pick<Me, 'role' | 'invite_hidden'> & {
  organization: { allowance: Pick<Allowance, 'members' | 'over'> }
}

/** The chat's suggestion to invite the team: with a form when the plan has room for someone, else the way to a plan that has. */
export interface InviteNudge {
  room: boolean
}

/**
 * Whether the chat of `project` may suggest inviting the team, and so asks for the first steps:
 * to an owner or admin, who alone invite, in a project of the organisation's own (an answer from
 * the sample shows what the app does; one about its own documents is the work a team would
 * share), until the member hides it, and while the organisation is within its plan, since a
 * read-only one invites nobody.
 */
export function mayInvite(me: Inviter, project: Pick<Project, 'sample'>): boolean {
  return isWithin('admin', me.role) && !project.sample && !me.invite_hidden && me.organization.allowance.over.length === 0
}

/**
 * The suggestion under a chat whose last message is an answer that ended well, while the
 * organisation's invite step is untaken (`steps`, the first steps, once asked): nobody else is a
 * member or invited. Null for none.
 */
export function inviteNudgeOf(
  me: Inviter,
  project: Pick<Project, 'sample'>,
  messages: Pick<Message, 'role' | 'status' | 'error'>[],
  steps: OnboardingStep[] | undefined,
): InviteNudge | null {
  const last = messages.at(-1)
  if (!mayInvite(me, project) || last?.role !== 'assistant' || last.status !== 'done' || last.error) return null
  if (!steps?.some((step) => step.key === 'invite' && !step.done)) return null
  // The member alone holds a place, so a plan for more than one member has room.
  const { members } = me.organization.allowance
  return { room: members === null || members > 1 }
}
