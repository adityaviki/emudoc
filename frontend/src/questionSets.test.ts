import { describe, expect, it } from 'vitest'
import type { QuestionReportSummary, QuestionSet, ReportAnswer } from './api.ts'
import { formatDateTime } from './format.ts'
import {
  answerStatusWords,
  draftOf,
  draftProblem,
  inputOf,
  questionSetSentence,
  questionSetStatus,
  reportByline,
  reportDeletionQuestion,
  reportStatusWords,
  reportTitle,
  reporting,
  setDeletionQuestion,
  whenWords,
  zoneWords,
} from './questionSets.ts'

const questionSet = (patch: Partial<QuestionSet> = {}): QuestionSet => ({
  id: 4,
  title: 'Every morning',
  questions: ['What is due this week?', 'Who signed the agreement?'],
  schedule: 'daily',
  weekday: null,
  hour: 8,
  on_arrivals: false,
  audience: 'maker',
  enabled: true,
  time_zone: 'Europe/Berlin',
  next_run_at: '2026-11-02T07:00:00Z',
  created_by: 2,
  made_by: 'Ada Lovelace',
  problem: null,
  last_report_at: null,
  running: false,
  updated_at: '2026-10-31T09:00:00Z',
  ...patch,
})

const report = (patch: Partial<QuestionReportSummary> = {}): QuestionReportSummary => ({
  id: 12,
  question_set_id: 4,
  title: 'Every morning',
  trigger: 'schedule',
  run_by: null,
  status: 'done',
  questions: 2,
  answered: 2,
  failed: 0,
  arrived: 0,
  names: [],
  created_at: '2026-11-02T07:00:00Z',
  finished_at: '2026-11-02T07:01:00Z',
  ...patch,
})

describe('a question set', () => {
  it('says when it is asked in its maker’s zone, and whom it tells', () => {
    expect(zoneWords('Europe/Berlin')).toBe('Berlin time')
    expect(zoneWords('America/Argentina/Buenos_Aires')).toBe('Buenos Aires time')
    expect(zoneWords('')).toBe('UTC')
    expect(questionSetSentence(questionSet(), 'you', 'Europe/Berlin')).toBe(
      'Every day at 08:00 Berlin time, it asks 2 questions of the project and tells you.',
    )
    const weekly = questionSet({ schedule: 'weekly', weekday: 0, hour: 9, on_arrivals: true, audience: 'project', questions: ['What changed?'] })
    expect(questionSetSentence(weekly, 'Ada Lovelace', 'UTC')).toBe(
      'Every Monday at 09:00 UTC, and as documents arrive, it asks 1 question of the project and tells everyone who sees the project.',
    )
    expect(whenWords({ schedule: null, weekday: null, hour: 8, on_arrivals: true }, 'UTC')).toBe('As documents arrive')
    expect(questionSetSentence(questionSet({ audience: 'maker' }), 'Ada Lovelace', 'UTC')).toContain('tells Ada Lovelace.')
    expect(questionSetSentence(questionSet({ audience: 'nobody' }), 'you', 'UTC')).toContain('tells nobody.')
  })

  it('says how it stands', () => {
    expect(questionSetStatus(questionSet())).toBe(`Next on ${formatDateTime('2026-11-02T07:00:00Z')} · No report yet`)
    expect(questionSetStatus(questionSet({ running: true, last_report_at: '2026-10-31T07:00:00Z' }))).toBe(
      `Being answered… · Next on ${formatDateTime('2026-11-02T07:00:00Z')} · Last report on ${formatDateTime('2026-10-31T07:00:00Z')}`,
    )
    expect(questionSetStatus(questionSet({ enabled: false, next_run_at: null }))).toBe('Off: only “Run now” asks it · No report yet')
  })

  it('is written whole from the form, its questions one a line', () => {
    const draft = draftOf(questionSet({ schedule: 'weekly', weekday: 3 }))
    expect(draft.questions).toBe('What is due this week?\nWho signed the agreement?')
    expect(inputOf({ ...draft, questions: '  What is due?\n\n Who signed?  \n', title: ' Thursdays ' })).toEqual({
      title: 'Thursdays',
      questions: ['What is due?', 'Who signed?'],
      schedule: 'weekly',
      weekday: 3,
      hour: 8,
      on_arrivals: false,
      audience: 'maker',
      enabled: true,
    })
    // A day given to a schedule that is not weekly, and a schedule of none, are not sent.
    expect(inputOf({ ...draft, schedule: 'daily' }).weekday).toBeNull()
    expect(inputOf({ ...draft, schedule: 'none', on_arrivals: true })).toMatchObject({ schedule: null, weekday: null })
    // A new set asks every morning at eight and tells its maker.
    expect(draftOf(null)).toMatchObject({ schedule: 'daily', hour: 8, audience: 'maker', enabled: true, questions: '' })
  })

  it('says what the server would refuse before it is sent', () => {
    const draft = draftOf(questionSet())
    expect(draftProblem(draft, 10)).toBeNull()
    expect(draftProblem({ ...draft, title: '  ' }, 10)).toBe('Give the question set a name.')
    expect(draftProblem({ ...draft, questions: '\n \n' }, 10)).toBe('Write at least one question, one a line.')
    expect(draftProblem({ ...draft, questions: 'a\nb\nc' }, 2)).toBe('A question set asks at most 2 questions. Leave out 1.')
    expect(draftProblem({ ...draft, questions: 'x'.repeat(2001) }, 10)).toContain('at most 2,000 characters')
    expect(draftProblem({ ...draft, schedule: 'none' }, 10)).toContain('Choose a schedule, or have arriving documents run it.')
    expect(draftProblem({ ...draft, schedule: 'none', on_arrivals: true }, 10)).toBeNull()
  })

  it('asks before it is deleted, its reports staying', () => {
    expect(setDeletionQuestion(questionSet())).toEqual({
      title: 'Delete this question set?',
      body: '“Every morning” goes for good, and nothing asks its questions any more. The reports it made stay, under its name.',
      action: 'Delete question set',
    })
  })
})

describe('a report', () => {
  it('says what made it', () => {
    expect(reportByline(report())).toBe('On its schedule')
    expect(reportByline(report({ trigger: 'member', run_by: 'Ada Lovelace' }))).toBe('Run by Ada Lovelace')
    expect(reportByline(report({ trigger: 'member' }))).toBe('Run by a former member')
    expect(reportByline(report({ trigger: 'arrivals', arrived: 2, names: ['INV-1.pdf', 'INV-2.pdf'] }))).toBe('After INV-1.pdf and INV-2.pdf arrived')
    expect(reportByline(report({ trigger: 'arrivals', arrived: 5, names: ['a.pdf', 'b.pdf', 'c.pdf'] }))).toBe('After a.pdf, b.pdf, c.pdf and 2 more arrived')
    expect(reportByline(report({ trigger: 'schedule', arrived: 1, names: ['a.pdf'] }))).toBe('On its schedule, after a.pdf arrived')
    expect(reportTitle(report())).toBe(`Every morning, ${formatDateTime('2026-11-02T07:00:00Z')}`)
  })

  it('says how far it is, and the page asks again until it settles', () => {
    expect(reportStatusWords(report({ status: 'queued', answered: 0 }))).toBe('Waiting for its turn to be asked.')
    expect(reportStatusWords(report({ status: 'running', answered: 1 }))).toBe('Answering… 1 of 2 answered so far.')
    expect(reportStatusWords(report())).toBe('2 of 2 questions answered.')
    expect(reportStatusWords(report({ answered: 1, failed: 1 }))).toBe('1 of 2 questions answered, 1 not.')
    expect(reportStatusWords(report({ status: 'failed' }))).toBe('The report failed.')
    expect([report({ status: 'queued' }), report({ status: 'running' }), report()].map(reporting)).toEqual([true, true, false])
  })

  it('says why an answer has no words', () => {
    const answer: ReportAnswer = { question: 'What is due?', status: 'done', content: 'Nothing.', sources: [], notice: null, error: null }
    expect(answerStatusWords(answer)).toBeNull()
    expect(answerStatusWords({ ...answer, status: 'queued' })).toBe('Waiting to be asked.')
    expect(answerStatusWords({ ...answer, status: 'failed', error: 'The day’s spending is reached.' })).toBe('The day’s spending is reached.')
  })

  it('asks before it is deleted', () => {
    expect(reportDeletionQuestion(report()).body).toBe(
      `Every morning, ${formatDateTime('2026-11-02T07:00:00Z')} goes for good, with its answers. Its question set stays, and can be run again.`,
    )
    expect(reportDeletionQuestion(report({ question_set_id: null })).body).not.toContain('question set stays')
  })
})
