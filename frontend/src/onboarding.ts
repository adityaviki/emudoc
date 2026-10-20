// The projects page's list of first steps (backend/app/onboarding.py): what each step says, and
// the next action it offers from the projects the member sees.

import type { OnboardingStep, OnboardingStepKey, Project } from './api.ts'

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
