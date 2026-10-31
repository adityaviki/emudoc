// A project's question sets and the reports of their answers as the Question sets view and a
// report's view word them (backend/app/question_sets.py, backend/app/question_reports.py): when a
// set is asked and whom it tells, the form's draft, how far a report and each of its answers are,
// whether the page should ask again, and what the views ask before deleting.

import type { AutomationAudience, QuestionReportSummary, QuestionSet, QuestionSetInput, QuestionSetSchedule, ReportAnswer } from './api.ts'
import { listWords } from './automations.ts'
import type { Question } from './components/Confirm.tsx'
import { formatDateTime } from './format.ts'

export const questionSetsKey = (projectId: number) => ['question-sets', projectId] as const
export const reportsKey = (projectId: number) => ['question-reports', projectId] as const
export const reportKey = (reportId: number) => ['question-report', reportId] as const

/** How often the page asks for a report, and the sets, while questions are being answered. */
export const REPORT_POLL = 2000

/** The days of a weekly schedule, Monday 0, as the server numbers them. */
export const WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'] as const

/** The longest saved question (QUESTION_CHARS in backend/app/question_sets.py). */
export const QUESTION_CHARS = 2000

/** What the form says a set's run costs. */
export const COUNTS_NOTE = 'Each question counts as one of the month’s questions as it is asked.'

/** Whom a set's reports are told to, as the form offers it. */
export const REPORT_AUDIENCES: { value: AutomationAudience; label: string }[] = [
  { value: 'maker', label: 'Whoever made it' },
  { value: 'project', label: 'Everyone who sees the project' },
  { value: 'nobody', label: 'Nobody: its reports are listed with the others' },
]

/** An hour of the day as the views show it: "08:00". */
export const hourWords = (hour: number) => `${String(hour).padStart(2, '0')}:00`

/** A zone as a schedule names it: "UTC", or its city's time, "Berlin time". */
export function zoneWords(zone: string): string {
  if (!zone || zone === 'UTC') return 'UTC'
  return `${(zone.split('/').pop() ?? zone).replaceAll('_', ' ')} time`
}

/** When a set is asked: "Every Monday at 08:00 Berlin time, and as documents arrive"; "As documents arrive" without a schedule. */
export function whenWords(set: Pick<QuestionSetInput, 'schedule' | 'weekday' | 'hour' | 'on_arrivals'>, zone: string): string {
  const at = `at ${hourWords(set.hour)} ${zoneWords(zone)}`
  const scheduled =
    set.schedule === 'daily' ? `Every day ${at}` : set.schedule === 'weekly' ? `Every ${WEEKDAYS[set.weekday ?? 0]} ${at}` : null
  if (scheduled) return set.on_arrivals ? `${scheduled}, and as documents arrive` : scheduled
  return set.on_arrivals ? 'As documents arrive' : 'Never on its own'
}

const TELLS: Record<AutomationAudience, (maker: string) => string> = {
  maker: (maker) => `tells ${maker}`,
  project: () => 'tells everyone who sees the project',
  nobody: () => 'tells nobody',
}

/** The set in a sentence, `maker` "you" for one's own: "Every day at 08:00 UTC, it asks 2 questions of the project and tells you." */
export function questionSetSentence(set: QuestionSetInput, maker: string, zone: string): string {
  const count = set.questions.length
  return `${whenWords(set, zone)}, it asks ${count} question${count === 1 ? '' : 's'} of the project and ${TELLS[set.audience](maker)}.`
}

/** How the set stands in a line: off, being answered, when it runs next and when it last made a report. */
export function questionSetStatus(set: QuestionSet): string {
  const parts: string[] = []
  if (!set.enabled) parts.push('Off: only “Run now” asks it')
  else if (set.running) parts.push('Being answered…')
  if (set.enabled && set.next_run_at) parts.push(`Next on ${formatDateTime(set.next_run_at)}`)
  parts.push(set.last_report_at ? `Last report on ${formatDateTime(set.last_report_at)}` : 'No report yet')
  return parts.join(' · ')
}

/** A set as the form edits it: its questions one a line, and its schedule `none` where it has none. */
export interface QuestionSetDraft {
  title: string
  questions: string
  schedule: QuestionSetSchedule | 'none'
  weekday: number
  hour: number
  on_arrivals: boolean
  audience: AutomationAudience
  enabled: boolean
}

/** The form's draft of a set, or of a new one: every morning at eight, told to its maker. */
export function draftOf(set: QuestionSet | null): QuestionSetDraft {
  if (!set) return { title: '', questions: '', schedule: 'daily', weekday: 0, hour: 8, on_arrivals: false, audience: 'maker', enabled: true }
  return {
    title: set.title,
    questions: set.questions.join('\n'),
    schedule: set.schedule ?? 'none',
    weekday: set.weekday ?? 0,
    hour: set.hour,
    on_arrivals: set.on_arrivals,
    audience: set.audience,
    enabled: set.enabled,
  }
}

/** The questions a draft holds, one a line, the blank lines dropped. */
export const questionsOf = (draft: Pick<QuestionSetDraft, 'questions'>) =>
  draft.questions
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)

/** The draft as the server takes it, whole. */
export function inputOf(draft: QuestionSetDraft): QuestionSetInput {
  const schedule = draft.schedule === 'none' ? null : draft.schedule
  return {
    title: draft.title.trim(),
    questions: questionsOf(draft),
    schedule,
    weekday: schedule === 'weekly' ? draft.weekday : null,
    hour: draft.hour,
    on_arrivals: draft.on_arrivals,
    audience: draft.audience,
    enabled: draft.enabled,
  }
}

/** Why the server would refuse the draft, said before it is sent; null when it would take it. */
export function draftProblem(draft: QuestionSetDraft, maxQuestions: number): string | null {
  if (!draft.title.trim()) return 'Give the question set a name.'
  const questions = questionsOf(draft)
  if (questions.length === 0) return 'Write at least one question, one a line.'
  if (questions.length > maxQuestions) return `A question set asks at most ${maxQuestions} questions. Leave out ${questions.length - maxQuestions}.`
  if (questions.some((question) => question.length > QUESTION_CHARS)) return `A question is at most ${QUESTION_CHARS.toLocaleString()} characters. Shorten the longer ones.`
  if (draft.schedule === 'none' && !draft.on_arrivals) return 'A question set runs on a schedule, as documents arrive, or both. Choose a schedule, or have arriving documents run it.'
  return null
}

/** Whether the worker has yet to answer the report, and the page should ask again. */
export const reporting = (report: Pick<QuestionReportSummary, 'status'>) => report.status === 'queued' || report.status === 'running'

/** A report named by its set and when it was made: "Every morning, 26 Oct 2026, 08:00". */
export const reportTitle = (report: QuestionReportSummary) => `${report.title}, ${formatDateTime(report.created_at)}`

/** What made the report, in a line: "Run by Ada Lovelace", "On its schedule", "After INV-1.pdf and INV-2.pdf arrived". */
export function reportByline(report: QuestionReportSummary): string {
  if (report.trigger === 'member') return report.run_by ? `Run by ${report.run_by}` : 'Run by a former member'
  if (report.trigger === 'schedule' && report.arrived === 0) return 'On its schedule'
  const { names, arrived } = report
  const what =
    names.length === 0
      ? `${arrived} document${arrived === 1 ? '' : 's'}`
      : arrived > names.length
        ? `${names.join(', ')} and ${arrived - names.length} more`
        : listWords(names)
  return `${report.trigger === 'schedule' ? 'On its schedule, after' : 'After'} ${what} arrived`
}

/** How far the report is, in a line, or what became of its questions once it settled. */
export function reportStatusWords(report: QuestionReportSummary): string {
  if (report.status === 'queued') return 'Waiting for its turn to be asked.'
  if (report.status === 'running') return `Answering… ${report.answered} of ${report.questions} answered so far.`
  if (report.status === 'failed') return 'The report failed.'
  const answered = `${report.answered} of ${report.questions} question${report.questions === 1 ? '' : 's'} answered`
  return report.failed ? `${answered}, ${report.failed} not.` : `${answered}.`
}

/** How far one answer is, where it has no words yet: waiting, being written, or why it was not. */
export function answerStatusWords(answer: ReportAnswer): string | null {
  if (answer.status === 'queued') return 'Waiting to be asked.'
  if (answer.status === 'running') return 'Being answered…'
  if (answer.status === 'failed') return answer.error ?? 'It could not be answered.'
  return null
}

/** What the view asks before deleting a set, whose reports stay. */
export function setDeletionQuestion(set: QuestionSet): Question {
  return {
    title: 'Delete this question set?',
    body: `“${set.title}” goes for good, and nothing asks its questions any more. The reports it made stay, under its name.`,
    action: 'Delete question set',
  }
}

/** What the view asks before deleting a report, which takes its answers with it. */
export function reportDeletionQuestion(report: QuestionReportSummary): Question {
  return {
    title: 'Delete this report?',
    body: `${reportTitle(report)} goes for good, with its answers.${report.question_set_id != null ? ' Its question set stays, and can be run again.' : ''}`,
    action: 'Delete report',
  }
}
