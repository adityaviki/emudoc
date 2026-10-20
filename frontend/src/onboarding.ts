// The projects page's list of first steps (backend/app/onboarding.py): what each step says, and
// the next action it offers from the projects the member sees; and the chat's suggestion to
// invite the team, the last step offered again under an answer.

import { isWithin } from './api.ts'
import type { Allowance, Me, OnboardingStep, OnboardingStepKey, Project } from './api.ts'
import type { Message } from './chat.ts'

/** A step's way forward: a page to open, or the projects page's own form for a new project. */
export type StepAction = { label: string; to: string } | { label: string; create: true }

export interface ChecklistStep {
  key: OnboardingStepKey
  title: string
  detail: string
  done: boolean
  /** None once it is done, or while an earlier step must be taken first. */
  action: StepAction | null
}

/** The id of the organisation page's section that invites people (`InvitationsSection`), which a link opens the page at. */
export const INVITATIONS_SECTION = 'invitations'
/** The id of its section that lets everyone at a domain join (`JoiningSection`). */
export const JOINING_SECTION = 'joining'

/**
 * The list as the projects page shows it, or null when there is nothing to show: no step offered
 * (a viewer's), or every step taken. `projects` is the page's list, newest first.
 */
export function checklistOf(steps: OnboardingStep[], projects: Project[]): ChecklistStep[] | null {
  if (!steps.length || steps.every((step) => step.done)) return null
  return steps.map((step) => ({ ...words(step.key, projects), done: step.done, action: step.done ? null : actionOf(step.key, projects) }))
}

function words(key: OnboardingStepKey, projects: Project[]): { key: OnboardingStepKey; title: string; detail: string } {
  switch (key) {
    case 'upload':
      return {
        key,
        title: 'Upload your documents',
        detail: 'Make a project of your own and drop files on its library: each is read and indexed in the background.',
      }
    case 'ask':
      return {
        key,
        title: 'Ask about them',
        detail: projects.some((project) => project.sample)
          ? 'The sample project is indexed already: ask one of its questions, then open the passages the answer cites.'
          : 'Ask in a project’s chat: the answer cites the passages it relies on, and each citation opens its document.',
      }
    case 'invite':
      return {
        key,
        title: 'Invite your team',
        detail: 'Invite colleagues by email with the role each should have, or let everyone at your domain join.',
      }
  }
}

function actionOf(key: OnboardingStepKey, projects: Project[]): StepAction | null {
  const own = projects.find((project) => !project.sample)
  switch (key) {
    case 'upload':
      return own ? { label: `Open ${own.name}`, to: `/projects/${own.id}` } : { label: 'Create a project', create: true }
    case 'ask': {
      // The sample first, which is there to be asked; else the newest project with documents.
      const asked = projects.find((project) => project.sample) ?? projects.find((project) => project.document_count > 0)
      if (!asked) return null
      return { label: asked.sample ? 'Ask the sample' : `Ask in ${asked.name}`, to: `/projects/${asked.id}?chat=new` }
    }
    case 'invite':
      return { label: 'Invite people', to: `/organization#${INVITATIONS_SECTION}` }
  }
}

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
 * read-only one invites nobody. Hiding the projects page's first steps leaves it be.
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
