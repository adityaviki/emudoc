import { describe, expect, it } from 'vitest'
import type { OnboardingStep, Project } from './api.ts'
import { checklistOf } from './onboarding.ts'

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
