import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, ChevronDown, ChevronRight, FileText, ListChecks, Play, RotateCcw, Trash2 } from 'lucide-react'
import { useEffect, useId, useRef, useState } from 'react'
import { api, mayChange, sourceLabel } from '../api.ts'
import type { CheckRun, CheckRunRule, FindingOutcome, Source } from '../api.ts'
import { useMe } from '../auth.ts'
import {
  REPORT_NOTE,
  RUN_POLL,
  mayAskReport,
  reportStatusWords,
  runByline,
  runDeletionQuestion,
  runFindingsKey,
  runKey,
  runStatusWords,
  runTitle,
  runsKey,
  settling,
} from '../checkRuns.ts'
import { Confirm } from '../components/Confirm.tsx'
import { Markdown } from '../components/Markdown.tsx'
import { Button, Checkbox, EmptyState, IconButton, Loading, Notice, Segmented, Spinner } from '../components/ui.tsx'
import { OUTCOMES, OUTCOME_CHOICES, checkSentence, countsWords } from '../crossChecks.ts'
import { sourceOf } from '../factTable.ts'
import { useStatus } from '../hooks.ts'
import { describeAnswerUsage } from '../usage.ts'
import { CitationChip } from './ChatPane.tsx'
import { FindingItem, Refused } from './Findings.tsx'
import { useWorkspace } from './state.ts'

/**
 * The project's runs of its cross-checks, under the rules in the Cross-checks view: running them
 * all now, with the model's discrepancy report when asked, and the runs before, newest first,
 * each opening in the library's place.
 */
export function RunsSection({ runnable, changes }: { runnable: boolean; changes: boolean }) {
  const workspace = useWorkspace()
  const { project } = workspace
  const queryClient = useQueryClient()
  const [report, setReport] = useState(false)
  const [refused, setRefused] = useState<Error | null>(null)
  const runs = useInfiniteQuery({
    queryKey: runsKey(project.id),
    queryFn: ({ pageParam }) => api.checkRuns(project.id, pageParam),
    initialPageParam: null as number | null,
    getNextPageParam: (page) => page.next_before,
    refetchInterval: (query) => (query.state.data?.pages[0]?.runs.some(settling) ? RUN_POLL : false),
  })
  const start = useMutation({
    mutationFn: () => api.runChecks(project.id, { report }),
    onMutate: () => setRefused(null),
    onSuccess: (run) => {
      void queryClient.invalidateQueries({ queryKey: runsKey(project.id) })
      queryClient.setQueryData(runKey(run.id), run)
      workspace.showRun(run.id)
    },
    onError: (error) => setRefused(error),
  })
  const listed = runs.data?.pages.flatMap((page) => page.runs) ?? []

  return (
    <section aria-labelledby="check-runs" className="space-y-2 pt-2">
      <h3 id="check-runs" className="text-xs font-semibold text-muted">
        Runs
      </h3>
      <p className="text-xs text-muted">A run works the cross-checks out now and keeps what they found, to come back to and hand on.</p>
      {changes && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <Button
            variant="primary"
            icon={<Play className="size-4" />}
            disabled={!runnable || start.isPending}
            title={runnable ? undefined : 'Every cross-check needs a change before it can be worked out.'}
            onClick={() => start.mutate()}
          >
            {start.isPending ? 'Starting…' : 'Run the checks'}
          </Button>
          <Checkbox label="With a discrepancy report by the model" checked={report} onChange={(event) => setReport(event.target.checked)} />
        </div>
      )}
      {refused && <Refused error={refused} onDismiss={() => setRefused(null)} />}
      {runs.isError && <Notice tone="bad">Could not load the runs: {runs.error.message}</Notice>}
      {runs.data && listed.length === 0 && <p className="text-xs text-muted">No runs yet.</p>}
      {listed.length > 0 && (
        <ul aria-label="Runs" className="space-y-1.5">
          {listed.map((run) => (
            <li key={run.id}>
              <button
                type="button"
                onClick={() => workspace.showRun(run.id)}
                className="w-full rounded-md border border-line bg-surface px-3 py-2 text-left transition hover:bg-sunken"
              >
                <span className="block text-[13px] font-medium">{runTitle(run)}</span>
                <span className="block text-xs text-muted">{runByline(run)}</span>
                <span className="flex items-center gap-1.5 text-xs text-ink">
                  {settling(run) && <Spinner className="size-3" />}
                  {runStatusWords(run)}
                  {run.report_status === 'done' && ' · Report written'}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {runs.hasNextPage && (
        <Button disabled={runs.isFetchingNextPage} onClick={() => void runs.fetchNextPage()}>
          {runs.isFetchingNextPage ? 'Loading…' : 'Show more runs'}
        </Button>
      )}
    </section>
  )
}

/**
 * A run of the project's cross-checks, in the library's place: when and by whom it was made, its
 * discrepancy report, written by the model from what the run found and citing where each value is
 * written, and what each of its rules found, as it was found then.
 */
export function RunView({ runId }: { runId: number }) {
  const workspace = useWorkspace()
  const changes = mayChange(useMe().role)
  const queryClient = useQueryClient()
  const currency = useStatus().data?.currency
  const heading = useRef<HTMLHeadingElement>(null)
  const reportHeading = useRef<HTMLHeadingElement>(null)
  const [asking, setAsking] = useState(false)
  const [refused, setRefused] = useState<Error | null>(null)
  useEffect(() => {
    if (window.document.activeElement === window.document.body) heading.current?.focus()
  }, [])
  const run = useQuery({
    queryKey: runKey(runId),
    queryFn: () => api.checkRun(runId),
    refetchInterval: (query) => (query.state.data && settling(query.state.data) ? RUN_POLL : false),
  })
  const data = run.data
  const settled = data != null && !settling(data)
  // Once the run settles, the list of runs says so too.
  useEffect(() => {
    if (settled && data) void queryClient.invalidateQueries({ queryKey: runsKey(data.project_id) })
  }, [settled, data, queryClient])

  const ask = useMutation({
    mutationFn: () => api.askReport(runId),
    onMutate: () => setRefused(null),
    onSuccess: (asked) => {
      queryClient.setQueryData(runKey(runId), asked)
      // The button goes with the report it asked for: the keyboard goes on from the section's heading.
      requestAnimationFrame(() => reportHeading.current?.focus())
    },
    onError: (error) => setRefused(error),
  })
  const remove = useMutation({
    mutationFn: () => api.deleteCheckRun(runId),
    onSuccess: () => {
      if (data) void queryClient.invalidateQueries({ queryKey: runsKey(data.project_id) })
      workspace.showChecks()
    },
    onError: (error) => setRefused(error),
  })

  return (
    <div className="flex h-full min-h-0 flex-col bg-page">
      <div className="flex h-11 shrink-0 items-center gap-1.5 border-b border-line bg-surface px-2">
        <IconButton label="Back to the cross-checks" onClick={workspace.showChecks}>
          <ArrowLeft className="size-4" />
        </IconButton>
        <h2 ref={heading} tabIndex={-1} className="flex-1 truncate text-[13px] font-semibold">
          {data ? runTitle(data) : 'Run of the cross-checks'}
        </h2>
        {changes && data && (
          <IconButton label="Delete this run" danger disabled={remove.isPending || settling(data)} onClick={() => setAsking(true)}>
            <Trash2 className="size-4" />
          </IconButton>
        )}
      </div>
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3">
        {run.isPending && <Loading />}
        {run.isError && <Notice tone="bad">Could not load the run: {run.error.message}</Notice>}
        {refused && <Refused error={refused} onDismiss={() => setRefused(null)} />}
        {data && (
          <>
            <div className="space-y-0.5">
              <p className="text-xs text-muted">{runByline(data)}</p>
              <p className="flex items-center gap-1.5 text-xs text-ink">
                {(data.status === 'queued' || data.status === 'running') && <Spinner className="size-3" />}
                {runStatusWords(data)}
              </p>
            </div>
            {data.status === 'failed' && data.error && <Notice tone="bad">{data.error}</Notice>}
            <section aria-labelledby={`run-${runId}-report`} className="space-y-2 rounded-md border border-line bg-surface px-3 py-2.5">
              <h3 id={`run-${runId}-report`} ref={reportHeading} tabIndex={-1} className="flex items-center gap-1.5 text-[13px] font-semibold">
                <FileText aria-hidden className="size-4 text-muted" />
                Discrepancy report
              </h3>
              <ReportBody
                run={data}
                currency={currency}
                asking={ask.isPending}
                onAsk={changes && mayAskReport(data) ? () => ask.mutate() : null}
                onOpen={(source) => workspace.openDocument(source.document_id, { passage: source.id })}
              />
            </section>
            <section aria-labelledby={`run-${runId}-rules`} className="space-y-2">
              <h3 id={`run-${runId}-rules`} className="text-xs font-semibold text-muted">
                What each cross-check found
              </h3>
              <ul aria-label="Cross-checks run" className="space-y-2">
                {data.rules.map((rule, index) => (
                  <RuleItem key={index} runId={runId} index={index} rule={rule} />
                ))}
              </ul>
            </section>
          </>
        )}
      </div>
      {asking && data && <Confirm {...runDeletionQuestion(data)} onConfirm={() => remove.mutate()} onClose={() => setAsking(false)} />}
    </div>
  )
}

/** The report as far as it is: the way to ask for it, its run under way, why it failed, or its words with their sources. */
function ReportBody({
  run,
  currency,
  asking,
  onAsk,
  onOpen,
}: {
  run: CheckRun
  currency: string | undefined
  asking: boolean
  onAsk: (() => void) | null
  onOpen: (source: Source) => void
}) {
  const waiting = reportStatusWords(run)
  const ask = (label: string, again: boolean) =>
    onAsk && (
      <Button icon={again ? <RotateCcw className="size-4" /> : <FileText className="size-4" />} disabled={asking} onClick={onAsk}>
        {label}
      </Button>
    )
  if (run.report_status === 'queued' || run.report_status === 'writing') {
    return (
      <p className="flex items-center gap-2 text-xs text-ink">
        <Spinner className="size-3.5" /> {waiting}
      </p>
    )
  }
  if (run.report_status === 'failed') {
    return (
      <div className="space-y-2">
        <Notice tone="bad">{run.report_error ?? waiting}</Notice>
        {ask('Try again', true)}
      </div>
    )
  }
  if (run.report_status === 'done') {
    const usage = run.usage.report
    return (
      <div className="space-y-2.5">
        <CitedReport run={run} onOpen={onOpen} />
        {run.report_notice && <p className="text-xs text-muted">{run.report_notice}</p>}
        <p className="text-[11px] text-muted">
          Written by the model from what the run found: check a value at its source before acting on it.
          {usage?.calls ? ` ${describeAnswerUsage(usage, currency)}` : ''}
        </p>
        {ask('Write it again', true)}
      </div>
    )
  }
  return (
    <div className="space-y-2">
      <p className="text-xs text-muted">{run.status === 'done' ? REPORT_NOTE : 'A report can be asked for once the checks are worked out.'}</p>
      {ask('Write a report', false)}
    </div>
  )
}

/** The report's words, its citations numbered in the order it first makes them, and the passages they cite. */
function CitedReport({ run, onOpen }: { run: CheckRun; onOpen: (source: Source) => void }) {
  const sources = new Map(run.report_sources.map((source) => [source.id, source]))
  const cited = [...new Set([...run.report.matchAll(/\[P(\d+)\]/g)].map((match) => Number(match[1])))].filter((id) => sources.has(id))
  const numberOf = (id: number) => cited.indexOf(id) + 1
  return (
    <>
      <div className="text-[13px]">
        <Markdown
          text={run.report}
          citation={(id) => {
            const source = sources.get(id)
            // A reference the model was never shown: left as plain text, never linked.
            return source ? <CitationChip number={numberOf(id)} source={source} onOpen={() => onOpen(source)} /> : <span className="text-muted">[P{id}]</span>
          }}
        />
      </div>
      {cited.length > 0 && (
        <div className="border-t border-line pt-2.5">
          <p className="mb-1 text-[11px] font-medium tracking-wide text-muted uppercase">Sources</p>
          <ol className="space-y-0.5 text-xs text-muted">
            {cited.map((id) => (
              <li key={id}>
                <button
                  type="button"
                  onClick={() => onOpen(sources.get(id)!)}
                  className="-mx-1 flex gap-2 rounded px-1 py-0.5 text-left transition hover:bg-sunken hover:text-ink"
                >
                  <span className="w-3 shrink-0 font-semibold text-accent tabular-nums">{numberOf(id)}</span>
                  {sourceLabel(sources.get(id)!)}
                </button>
              </li>
            ))}
          </ol>
        </div>
      )}
    </>
  )
}

/** A rule of the run as it was, with what it found, opening the findings themselves below it. */
function RuleItem({ runId, index, rule }: { runId: number; index: number; rule: CheckRunRule }) {
  const [open, setOpen] = useState(false)
  const id = useId()
  const total = rule.counts ? rule.counts.differs + rule.counts.missing + rule.counts.holds : 0
  return (
    <li className="rounded-md border border-line bg-surface px-3 py-2">
      <p className="text-[13px] font-medium break-words">{rule.title}</p>
      <p className="text-xs text-muted">{checkSentence(rule)}</p>
      {rule.problem ? (
        <p className="mt-1 text-xs text-warn">{rule.problem}</p>
      ) : (
        rule.counts && (
          <div className="mt-1.5 flex flex-wrap items-center gap-2">
            {total > 0 && (
              <Button
                icon={open ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
                aria-expanded={open}
                aria-controls={id}
                aria-label={`What it found: ${rule.title}`}
                onClick={() => setOpen(!open)}
              >
                What it found
              </Button>
            )}
            <span className="text-xs text-muted">{countsWords(rule.counts)}</span>
          </div>
        )
      )}
      {open && (
        <div id={id} className="mt-2">
          <RunFindings runId={runId} index={index} rule={rule} />
        </div>
      )}
    </li>
  )
}

/** What one rule of the run found, as it was found, by outcome, each value opening its document unless it is gone since. */
function RunFindings({ runId, index, rule }: { runId: number; index: number; rule: CheckRunRule }) {
  const workspace = useWorkspace()
  const [shown, setShown] = useState<FindingOutcome | 'all'>('all')
  const outcome = shown === 'all' ? null : shown
  const findings = useInfiniteQuery({
    queryKey: runFindingsKey(runId, index, outcome),
    queryFn: ({ pageParam }) => api.runFindings(runId, index, outcome, pageParam),
    initialPageParam: null as number | null,
    getNextPageParam: (page) => page.next_after,
  })
  const listed = findings.data?.pages.flatMap((page) => page.findings) ?? []
  const gone = new Set(findings.data?.pages.flatMap((page) => page.gone) ?? [])
  const counts = rule.counts
  return (
    <div className="space-y-2">
      {counts && (
        <Segmented
          value={shown}
          onChange={setShown}
          options={OUTCOME_CHOICES.map((option) => ({
            ...option,
            label: `${option.label} ${option.value === 'all' ? counts.differs + counts.missing + counts.holds : counts[option.value]}`,
          }))}
        />
      )}
      {findings.isPending && <Loading />}
      {findings.isError && <Notice tone="bad">Could not load what it found: {findings.error.message}</Notice>}
      {findings.data && listed.length === 0 && (
        <EmptyState icon={<ListChecks />} title={outcome ? `None ${OUTCOMES[outcome].toLowerCase()}` : 'Nothing found'}>
          {outcome ? 'Show all to see the others.' : 'No document of its kind had been read for its template.'}
        </EmptyState>
      )}
      {listed.length > 0 && (
        <ul aria-label={`Findings of ${rule.title}`} className="space-y-2">
          {listed.map((finding, place) => (
            <FindingItem
              key={`${place}:${finding.document_id}:${finding.left?.id ?? 'none'}`}
              finding={finding}
              check={rule}
              gone={gone}
              onOpen={(documentId, fact) => workspace.openDocument(documentId, fact ? sourceOf(fact) : null)}
            />
          ))}
        </ul>
      )}
      {findings.hasNextPage && (
        <Button disabled={findings.isFetchingNextPage} onClick={() => void findings.fetchNextPage()}>
          {findings.isFetchingNextPage ? 'Loading…' : 'Show more'}
        </Button>
      )}
    </div>
  )
}
