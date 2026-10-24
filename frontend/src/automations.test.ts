import { describe, expect, it } from 'vitest'
import type { Automation, Folder } from './api.ts'
import {
  automationDeletionQuestion,
  automationSentence,
  automationStatus,
  draftOf,
  draftProblem,
  folderChoices,
  inputOf,
  listWords,
  wordedDraft,
} from './automations.ts'
import { formatDateTime } from './format.ts'

const automation = (patch: Partial<Automation> = {}): Automation => ({
  id: 3,
  title: 'New invoices',
  kind: 'invoice',
  folder_id: 7,
  check_ids: null,
  report: false,
  audience: 'maker',
  recheck: false,
  enabled: true,
  review: null,
  assignee_id: null,
  assignee: null,
  folder: 'Inbox/Invoices',
  checks: null,
  created_by: 2,
  made_by: 'Ada Lovelace',
  problem: null,
  last_run_at: null,
  updated_at: '2026-10-23T14:05:00Z',
  ...patch,
})

const folder = (id: number, name: string, parent_id: number | null = null): Folder => ({
  id,
  project_id: 1,
  parent_id,
  name,
  created_at: '2026-10-23T14:05:00Z',
  document_count: 0,
})

describe('an automation in words', () => {
  it('says what arrives where, what it runs and whom it tells', () => {
    expect(automationSentence(automation(), 'Ada Lovelace')).toBe(
      'When an invoice arrives in Inbox/Invoices, run every cross-check, and tell Ada Lovelace.',
    )
    const other = automation({ kind: 'purchase order', folder: null, checks: ['Totals'], report: true, audience: 'project' })
    expect(automationSentence(other, 'you')).toBe(
      'When a purchase order arrives anywhere in the project, run “Totals” with a discrepancy report by the model, and tell everyone who sees the project.',
    )
    const again = automation({ kind: null, checks: ['A', 'B', 'C'], audience: 'nobody', recheck: true })
    expect(automationSentence(again, 'you')).toBe(
      'When a document arrives in Inbox/Invoices, run 3 cross-checks, and tell nobody. Run them again when a document they compare it with arrives anywhere in the project.',
    )
    expect(automationSentence(automation({ checks: [] }), 'you')).toContain('run no cross-check')
    expect(listWords(['a', 'b', 'c'])).toBe('a, b and c')
  })

  it('says what it puts in review, and for whom', () => {
    expect(automationSentence(automation({ review: 'arrived' }), 'you')).toBe(
      'When an invoice arrives in Inbox/Invoices, run every cross-check, put it in review, and tell you.',
    )
    expect(automationSentence(automation({ review: 'differs', assignee_id: 9, assignee: 'Grace Hopper' }), 'you')).toBe(
      'When an invoice arrives in Inbox/Invoices, run every cross-check, put it in review for Grace Hopper if they find a difference, and tell you.',
    )
  })

  it('says whether it is on and when it last ran', () => {
    expect(automationStatus(automation())).toBe('On · Not run yet')
    expect(automationStatus(automation({ last_run_at: '2026-10-23T15:00:00Z' }))).toBe(`On · Last ran ${formatDateTime('2026-10-23T15:00:00Z')}`)
    expect(automationStatus(automation({ enabled: false }))).toBe('Off: nothing that arrives runs it.')
  })

  it('asks before deleting one, whose runs stay', () => {
    expect(automationDeletionQuestion(automation())).toEqual({
      title: 'Delete this automation?',
      body: '“New invoices” goes for good: nothing that arrives runs it any more. The runs it made stay, with what they found.',
      action: 'Delete automation',
    })
  })
})

describe('the form', () => {
  it('starts with every invoice and every cross-check, and sends what it was given', () => {
    const fresh = draftOf(null, ['contract', 'invoice'])
    expect(fresh).toMatchObject({ kind: 'invoice', folder_id: null, every: true, audience: 'maker', enabled: true })
    expect(draftOf(null, ['contract']).kind).toBe('')
    expect(draftProblem(fresh)).toBe('Name the automation.')
    expect(inputOf({ ...fresh, title: '  New invoices ', kind: '' })).toEqual({
      title: 'New invoices',
      kind: null,
      folder_id: null,
      check_ids: null,
      report: false,
      audience: 'maker',
      recheck: false,
      enabled: true,
      review: null,
      assignee_id: null,
    })
  })

  it('assigns what it puts in review alone, by the name of whom it may assign', () => {
    const fresh = { ...draftOf(null, ['invoice']), title: 'New invoices' }
    expect(inputOf({ ...fresh, assignee_id: 9 })).toMatchObject({ review: null, assignee_id: null })
    const reviewing = { ...fresh, review: 'differs' as const, assignee_id: 9 }
    expect(inputOf(reviewing)).toMatchObject({ review: 'differs', assignee_id: 9 })
    expect(wordedDraft(reviewing, [], new Map(), new Map([[9, 'Grace Hopper']])).assignee).toBe('Grace Hopper')
    expect(draftOf(automation({ review: 'arrived', assignee_id: 9, assignee: 'Grace Hopper' }), [])).toMatchObject({
      review: 'arrived',
      assignee_id: 9,
    })
  })

  it('keeps the cross-checks chosen, and asks for one', () => {
    const chosen = draftOf(automation({ check_ids: [4, 5], checks: ['A', 'B'] }), [])
    expect(chosen).toMatchObject({ every: false, check_ids: [4, 5], folder_id: 7 })
    expect(inputOf(chosen).check_ids).toEqual([4, 5])
    expect(draftProblem({ ...chosen, check_ids: [] })).toBe('Choose a cross-check for it to run, or run every one.')
    const worded = wordedDraft(chosen, [{ id: 7, label: 'Inbox' }], new Map([[4, 'Prices'], [5, 'Totals']]))
    expect(worded).toMatchObject({ folder: 'Inbox', checks: ['Prices', 'Totals'] })
  })

  it('offers the folders in the library’s order, each by its path', () => {
    const folders = [folder(1, 'Inbox'), folder(2, 'invoices 10', 1), folder(3, 'Invoices 2', 1), folder(4, 'Archive')]
    expect(folderChoices(folders)).toEqual([
      { id: 4, label: 'Archive' },
      { id: 1, label: 'Inbox' },
      { id: 3, label: 'Inbox/Invoices 2' },
      { id: 2, label: 'Inbox/invoices 10' },
    ])
  })
})
