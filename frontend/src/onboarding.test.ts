import { describe, expect, it } from 'vitest'
import type { OnboardingStep, Project } from './api.ts'
import { checklistOf, inviteNudgeOf, mayInvite } from './onboarding.ts'
import type { Inviter } from './onboarding.ts'

const project = (id: number, name: string, changes: Partial<Project> = {}): Project => ({
  id,
  name,
  description: '',
  created_at: '2026-10-21T12:00:00Z',
  visibility: 'organization',
  sample: false,
  suggestions: [],
  document_count: 0,
  unenriched_count: 0,
  ...changes,
})

const SAMPLE = project(1, 'Sample: Acme supplier contract', { sample: true, document_count: 8 })
const none: OnboardingStep[] = [
  { key: 'upload', done: false },
  { key: 'ask', done: false },
  { key: 'invite', done: false },
]

describe('the first steps', () => {
  it('show nothing to a viewer, offered none, or once every step is taken', () => {
    expect(checklistOf([], [SAMPLE])).toBeNull()
    expect(checklistOf(none.map((step) => ({ ...step, done: true })), [SAMPLE])).toBeNull()
  })

  it('start a new organisation at the sample, a project of its own and its invitations', () => {
    const steps = checklistOf(none, [SAMPLE])!
    expect(steps.map((step) => [step.key, step.action])).toEqual([
      ['upload', { label: 'Create a project', create: true }],
      ['ask', { label: 'Ask the sample', to: '/projects/1?chat=new' }],
      ['invite', { label: 'Invite people', to: '/organization#invitations' }],
    ])
    expect(steps[1].detail).toMatch(/^The sample project is indexed already/)
  })

  it('open the newest project of its own to upload into, and offer nothing for a step taken', () => {
    const projects = [project(3, 'Leases'), project(2, 'Contracts'), SAMPLE]
    const steps = checklistOf([{ key: 'upload', done: false }, { key: 'ask', done: true }], projects)!
    expect(steps.map((step) => [step.key, step.done, step.action])).toEqual([
      ['upload', false, { label: 'Open Leases', to: '/projects/3' }],
      ['ask', true, null],
    ])
  })

  it('ask in a project that holds documents, and wait for one without the sample', () => {
    const ask: OnboardingStep[] = [{ key: 'ask', done: false }]
    expect(checklistOf(ask, [project(3, 'Leases')])![0].action).toBeNull()
    expect(checklistOf(ask, [project(3, 'Leases'), project(2, 'Contracts', { document_count: 2 })])![0]).toMatchObject({
      action: { label: 'Ask in Contracts', to: '/projects/2?chat=new' },
      detail: expect.stringMatching(/^Ask in a project’s chat/),
    })
  })
})

describe('the suggestion to invite the team', () => {
  const owner: Inviter = { role: 'owner', invite_hidden: false, organization: { allowance: { members: 3, over: [] } } }
  const own = project(2, 'Contracts', { document_count: 1 })
  const answered = [
    { role: 'user' as const, status: 'done' as const },
    { role: 'assistant' as const, status: 'done' as const },
  ]
  const untaken = none
  const invited = none.map((step) => (step.key === 'invite' ? { ...step, done: true } : step))

  it('follows an answer about the organisation’s own documents while nobody else is in it', () => {
    expect(inviteNudgeOf(owner, own, answered, untaken)).toEqual({ room: true })
    expect(inviteNudgeOf({ ...owner, role: 'admin' }, own, answered, untaken)).toEqual({ room: true })
    // Once someone is, or the steps are not known yet.
    expect(inviteNudgeOf(owner, own, answered, invited)).toBeNull()
    expect(inviteNudgeOf(owner, own, answered, undefined)).toBeNull()
  })

  it('waits for an answer that ended well', () => {
    expect(inviteNudgeOf(owner, own, [], untaken)).toBeNull()
    expect(inviteNudgeOf(owner, own, answered.slice(0, 1), untaken)).toBeNull()
    for (const status of ['streaming', 'stopped', 'failed'] as const) {
      expect(inviteNudgeOf(owner, own, [answered[0], { role: 'assistant', status }], untaken)).toBeNull()
    }
    expect(inviteNudgeOf(owner, own, [answered[0], { ...answered[1], error: 'The day’s limit is spent.' }], untaken)).toBeNull()
  })

  it('is not made in the sample, to those who do not invite, once hidden, or while the organisation is read-only', () => {
    expect(mayInvite(owner, own)).toBe(true)
    expect(mayInvite(owner, SAMPLE)).toBe(false)
    expect(mayInvite({ ...owner, role: 'member' }, own)).toBe(false)
    expect(mayInvite({ ...owner, role: 'viewer' }, own)).toBe(false)
    expect(mayInvite({ ...owner, invite_hidden: true }, own)).toBe(false)
    const over = { members: 3, over: [{ limit: 'documents' as const, words: '12 documents of 10' }] }
    expect(mayInvite({ ...owner, organization: { allowance: over } }, own)).toBe(false)
    expect(inviteNudgeOf({ ...owner, organization: { allowance: over } }, own, answered, untaken)).toBeNull()
  })

  it('offers a form while the plan has room for another member, else the way to a plan that has', () => {
    const allowing = (members: number | null): Inviter => ({ ...owner, organization: { allowance: { members, over: [] } } })
    expect(inviteNudgeOf(allowing(null), own, answered, untaken)).toEqual({ room: true })
    expect(inviteNudgeOf(allowing(2), own, answered, untaken)).toEqual({ room: true })
    expect(inviteNudgeOf(allowing(1), own, answered, untaken)).toEqual({ room: false })
  })
})
