// Runs of a project's cross-checks as the Cross-checks view and the run's view word them
// (backend/app/check_runs.py, backend/app/api/check_runs.py): the run in a line, how far it and
// its discrepancy report are, whether the page should ask again, and what the view asks before
// deleting a run.

import type { CheckRun, CheckRunSummary, FindingOutcome } from './api.ts'
import type { Question } from './components/Confirm.tsx'
import { countsWords } from './crossChecks.ts'
import { formatDateTime } from './format.ts'

export const runsKey = (projectId: number) => ['check-runs', projectId] as const
export const runKey = (runId: number) => ['check-run', runId] as const
export const runFindingsKey = (runId: number, rule: number, outcome: FindingOutcome | null) =>
  ['run-findings', runId, rule, outcome] as const

/** How often the page asks for a run while the worker works it out or writes its report. */
export const RUN_POLL = 1500

/** What the view says a report is and costs, beside the button that asks for it. */
export const REPORT_NOTE =
  'The model writes it from what the run found, citing where each value is written. It counts as one of the month’s questions.'

/** Whether the worker has yet to settle the run or its report, and the page should ask again. */
export function settling(run: CheckRunSummary): boolean {
  return run.status === 'queued' || run.status === 'running' || run.report_status === 'queued' || run.report_status === 'writing'
}

/** A run named by when it was made: "Run of 23 Oct 2026, 14:05". */
export const runTitle = (run: CheckRunSummary) => `Run of ${formatDateTime(run.created_at)}`

/** Who made the run and what it ran, in a line: "By Ada Lovelace · Prices, Totals", or the rules counted past three. */
export function runByline(run: CheckRunSummary): string {
  const rules = run.titles.length <= 3 ? run.titles.join(', ') : `${run.titles.slice(0, 2).join(', ')} and ${run.titles.length - 2} more`
  return [run.run_by ? `By ${run.run_by}` : 'By a former member', rules].join(' · ')
}

/** How far the run is, or what its rules found together once worked out. */
export function runStatusWords(run: CheckRunSummary): string {
  if (run.status === 'queued') return 'Waiting for its turn to run.'
  if (run.status === 'running') return 'Working the checks out…'
  if (run.status === 'failed') return 'The run failed.'
  return run.counts ? countsWords(run.counts) : 'Nothing found.'
}

/** How far the report is, in a line; null while none was asked for or once it is written. */
export function reportStatusWords(run: CheckRun | CheckRunSummary): string | null {
  if (run.report_status === 'queued') return run.status === 'done' ? 'Waiting to write the report…' : 'The report is written once the checks are worked out.'
  if (run.report_status === 'writing') return 'Writing the report…'
  if (run.report_status === 'failed') return 'The report could not be written.'
  return null
}

/** Whether the member may ask for the run's report, or for it again: once the run is worked out and no report is under way. */
export const mayAskReport = (run: CheckRun) => run.status === 'done' && run.report_status !== 'queued' && run.report_status !== 'writing'

/** What the view asks before deleting a run, which takes its findings and report with it. */
export function runDeletionQuestion(run: CheckRunSummary): Question {
  return {
    title: 'Delete this run?',
    body: `${runTitle(run)} goes for good, with what it found${run.report_status === 'done' ? ' and its report' : ''}. Its cross-checks stay, and can be run again.`,
    action: 'Delete run',
  }
}
