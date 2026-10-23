import { describe, expect, it } from 'vitest'
import type { CheckRun, CheckRunSummary } from './api.ts'
import { arrivalWords, mayAskReport, reportStatusWords, runByline, runDeletionQuestion, runStatusWords, runTitle, settling } from './checkRuns.ts'
import { formatDateTime } from './format.ts'

const summary = (patch: Partial<CheckRunSummary> = {}): CheckRunSummary => ({
  id: 4,
  status: 'done',
  report_status: null,
  run_by: 'Ada Lovelace',
  trigger: null,
  titles: ['Prices as on the price list', 'Total within its purchase order'],
  counts: { differs: 2, missing: 1, holds: 7 },
  created_at: '2026-10-23T14:05:00Z',
  finished_at: '2026-10-23T14:05:03Z',
  ...patch,
})

const whole = (patch: Partial<CheckRun> = {}): CheckRun => ({
  ...summary(),
  project_id: 1,
  user_id: 2,
  error: null,
  rules: [],
  report: '',
  report_sources: [],
  report_notice: null,
  report_error: null,
  usage: {},
  ...patch,
})

describe('a run in words', () => {
  it('names it by when it was made, who made it and what it ran', () => {
    expect(runTitle(summary())).toBe(`Run of ${formatDateTime('2026-10-23T14:05:00Z')}`)
    expect(runByline(summary())).toBe('By Ada Lovelace · Prices as on the price list, Total within its purchase order')
    const many = summary({ run_by: null, titles: ['A', 'B', 'C', 'D', 'E'] })
    expect(runByline(many)).toBe('By a former member · A, B and 3 more')
  })

  it('names the automation that made it, and what arrived for it', () => {
    const trigger = { automation_id: 3, automation: 'New invoices', documents: 2, partners: 0, names: ['INV-1.md', 'INV-2.md'] }
    const made = summary({ run_by: null, trigger, titles: ['Totals'] })
    expect(runByline(made)).toBe('By the automation “New invoices” · Totals')
    expect(arrivalWords(trigger)).toBe('After INV-1.md and INV-2.md arrived.')
    expect(arrivalWords({ ...trigger, documents: 5, names: ['a.md', 'b.md', 'c.md'] })).toBe('After a.md, b.md, c.md and 2 more arrived.')
    expect(arrivalWords({ ...trigger, documents: 0, partners: 1, names: ['PO-7702.md'] })).toBe(
      'After PO-7702.md, which its cross-checks compare with the documents it watches, arrived.',
    )
    expect(arrivalWords({ ...trigger, documents: 1, names: [] })).toBe('After 1 document arrived.')
  })

  it('says how far it is, or what its rules found together', () => {
    expect(runStatusWords(summary({ status: 'queued', counts: null }))).toBe('Waiting for its turn to run.')
    expect(runStatusWords(summary({ status: 'running', counts: null }))).toBe('Working the checks out…')
    expect(runStatusWords(summary({ status: 'failed', counts: null }))).toBe('The run failed.')
    expect(runStatusWords(summary())).toBe('2 differ · 1 misses a value · 7 hold')
  })

  it('is asked for again until it and its report settle', () => {
    expect(settling(summary({ status: 'queued' }))).toBe(true)
    expect(settling(summary({ status: 'running' }))).toBe(true)
    expect(settling(summary({ report_status: 'writing' }))).toBe(true)
    expect(settling(summary({ report_status: 'queued' }))).toBe(true)
    expect(settling(summary())).toBe(false)
    expect(settling(summary({ status: 'failed', report_status: 'failed' }))).toBe(false)
  })
})

describe('the report', () => {
  it('says how far it is until it is written', () => {
    expect(reportStatusWords(summary())).toBeNull()
    expect(reportStatusWords(summary({ status: 'running', report_status: 'queued' }))).toBe(
      'The report is written once the checks are worked out.',
    )
    expect(reportStatusWords(summary({ report_status: 'queued' }))).toBe('Waiting to write the report…')
    expect(reportStatusWords(summary({ report_status: 'writing' }))).toBe('Writing the report…')
    expect(reportStatusWords(summary({ report_status: 'done' }))).toBeNull()
  })

  it('is asked for once the run is worked out and none is under way', () => {
    expect(mayAskReport(whole())).toBe(true)
    expect(mayAskReport(whole({ report_status: 'done' }))).toBe(true)
    expect(mayAskReport(whole({ report_status: 'failed' }))).toBe(true)
    expect(mayAskReport(whole({ report_status: 'writing' }))).toBe(false)
    expect(mayAskReport(whole({ status: 'running' }))).toBe(false)
    expect(mayAskReport(whole({ status: 'failed' }))).toBe(false)
  })
})

describe('deleting a run', () => {
  it('says what goes with it and what stays', () => {
    const question = runDeletionQuestion(summary({ report_status: 'done' }))
    expect(question.title).toBe('Delete this run?')
    expect(question.body).toBe(
      `Run of ${formatDateTime('2026-10-23T14:05:00Z')} goes for good, with what it found and its report. Its cross-checks stay, and can be run again.`,
    )
    expect(runDeletionQuestion(summary()).body).not.toContain('report')
    expect(question.action).toBe('Delete run')
  })
})
