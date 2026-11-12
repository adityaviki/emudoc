import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, CalendarClock, Pause, Pencil, Play, Plus, Trash2, X } from 'lucide-react'
import { useEffect, useId, useRef, useState } from 'react'
import { api, mayChange } from '../api.ts'
import type { QuestionSet, Source } from '../api.ts'
import { useMe } from '../auth.ts'
import { Confirm } from '../components/Confirm.tsx'
import { Button, Checkbox, EmptyState, FIELD, IconButton, InputField, Loading, Notice, SelectField, Spinner } from '../components/ui.tsx'
import { useStatus } from '../hooks.ts'
import {
  COUNTS_NOTE,
  QUESTION_CHARS,
  REPORT_AUDIENCES,
  REPORT_POLL,
  WEEKDAYS,
  answerStatusWords,
  draftOf,
  draftProblem,
  hourWords,
  inputOf,
  questionSetSentence,
  questionSetStatus,
  questionSetsKey,
  questionsOf,
  reportByline,
  reportDeletionQuestion,
  reportKey,
  reportStatusWords,
  reportTitle,
  reporting,
  reportsKey,
  setDeletionQuestion,
  zoneWords,
} from '../questionSets.ts'
import type { QuestionSetDraft } from '../questionSets.ts'
import { describeAnswerUsage } from '../usage.ts'
import { CitedText } from './CitedText.tsx'
import { Refused } from './Findings.tsx'
import { useWorkspace } from './state.ts'

const HOURS = Array.from({ length: 24 }, (_, hour) => hour)

/**
 * The project's question sets, in the library's place: questions saved to be asked of its
 * documents every day or one day a week, as documents arrive, or both, each run kept as a report
 * told to whom the set names; and those reports, newest first, each opening in the library's
 * place. Whoever may change the project makes, changes, turns off, runs and deletes the sets;
 * anyone who sees it reads them and their reports.
 */
export function QuestionSetsView() {
  const workspace = useWorkspace()
  const { project } = workspace
  const me = useMe()
  const changes = mayChange(me.role)
  const queryClient = useQueryClient()
  const sets = useQuery({
    queryKey: questionSetsKey(project.id),
    queryFn: () => api.questionSets(project.id),
    refetchInterval: (query) => (query.state.data?.question_sets.some((set) => set.running) ? REPORT_POLL : false),
  })
  const [editing, setEditing] = useState<{ set: QuestionSet | null } | null>(null)
  const [asking, setAsking] = useState<QuestionSet | null>(null)
  const [said, setSaid] = useState<string | null>(null)
  const [refused, setRefused] = useState<Error | null>(null)
  const heading = useRef<HTMLHeadingElement>(null)
  // Opened from a button that went with the view before it: the keyboard goes on from here.
  useEffect(() => {
    if (window.document.activeElement === window.document.body) heading.current?.focus()
  }, [])
  const invalidate = () => void queryClient.invalidateQueries({ queryKey: questionSetsKey(project.id) })

  const remove = useMutation({
    mutationFn: (set: QuestionSet) => api.deleteQuestionSet(set.id),
    onMutate: () => {
      setRefused(null)
      // The row goes with its buttons: the keyboard goes on from the heading.
      heading.current?.focus()
    },
    onSuccess: (_, set) => setSaid(`Deleted “${set.title}”.`),
    onError: (error) => setRefused(error),
    onSettled: invalidate,
  })
  const turn = useMutation({
    mutationFn: (set: QuestionSet) => api.updateQuestionSet(set.id, { ...inputOf(draftOf(set)), enabled: !set.enabled }),
    onMutate: () => setRefused(null),
    onSuccess: (turned) => setSaid(`Turned “${turned.title}” ${turned.enabled ? 'on' : 'off'}.`),
    onError: (error) => setRefused(error),
    onSettled: invalidate,
  })
  const run = useMutation({
    mutationFn: (set: QuestionSet) => api.runQuestionSet(set.id),
    onMutate: () => setRefused(null),
    onSuccess: (report) => {
      invalidate()
      void queryClient.invalidateQueries({ queryKey: reportsKey(project.id) })
      queryClient.setQueryData(reportKey(report.id), report)
      workspace.showReport(report.id)
    },
    onError: (error) => setRefused(error),
  })

  const listed = sets.data?.question_sets ?? []
  const room = listed.length < (sets.data?.max_question_sets ?? 0)
  const makerOf = (set: QuestionSet) => (set.created_by === me.id ? 'you' : (set.made_by ?? 'whoever made it'))
  const close = (saved: string | null) => {
    setEditing(null)
    if (saved) setSaid(saved)
    requestAnimationFrame(() => heading.current?.focus())
  }

  return (
    <div className="flex h-full min-h-0 flex-col bg-page">
      <div className="flex h-11 shrink-0 items-center gap-1.5 border-b border-line bg-surface px-2">
        <IconButton label="Back to the library" onClick={() => workspace.goToFolder(null)}>
          <ArrowLeft className="size-4" />
        </IconButton>
        <h2 ref={heading} tabIndex={-1} className="flex-1 truncate text-[13px] font-semibold">
          Question sets
        </h2>
        {changes && !editing && (
          <Button
            icon={<Plus className="size-4" />}
            disabled={!sets.data || !room}
            title={room ? undefined : `A project has at most ${sets.data?.max_question_sets} question sets.`}
            onClick={() => {
              setSaid(null)
              setEditing({ set: null })
            }}
          >
            New question set
          </Button>
        )}
      </div>
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3">
        <p className="text-xs text-muted">
          A question set asks the project’s documents the same questions on a schedule or once new documents arrive, and keeps the answers as a report for whoever
          should read them.
        </p>
        {refused && <Refused error={refused} onDismiss={() => setRefused(null)} />}
        {said && !editing && (
          <Notice tone="info">
            <div className="flex items-start justify-between gap-3">
              <span>{said}</span>
              <IconButton
                label="Dismiss"
                onClick={() => {
                  setSaid(null)
                  heading.current?.focus()
                }}
                className="p-0.5!"
              >
                <X className="size-3.5" />
              </IconButton>
            </div>
          </Notice>
        )}
        {sets.isPending && <Loading />}
        {sets.isError && <Notice tone="bad">Could not load the question sets: {sets.error.message}</Notice>}
        {editing && sets.data ? (
          <QuestionSetForm set={editing.set} maxQuestions={sets.data.max_questions} onClose={close} />
        ) : (
          <>
            {sets.data && listed.length === 0 && (
              <EmptyState icon={<CalendarClock className="size-5" />} title="No question sets yet">
                {changes ? 'Save the questions you ask again and again, and have them asked for you.' : null}
              </EmptyState>
            )}
            {listed.length > 0 && (
              <ul aria-label="Question sets" className="space-y-2">
                {listed.map((set) => (
                  <li key={set.id} className="rounded-md border border-line bg-surface px-3 py-2">
                    <div className="flex items-start gap-2">
                      <div className="min-w-0 flex-1">
                        <p className="text-[13px] font-medium break-words">{set.title}</p>
                        <p className="text-xs text-muted">{questionSetSentence(set, makerOf(set), set.time_zone)}</p>
                        <p className="flex items-center gap-1.5 text-xs text-ink">
                          {set.running && <Spinner className="size-3" />}
                          {questionSetStatus(set)}
                        </p>
                        {set.problem && <p className="mt-1 text-xs text-warn">{set.problem}</p>}
                      </div>
                      {changes && (
                        <span className="flex shrink-0 items-center gap-0.5">
                          <Button
                            aria-label={`Run now: ${set.title}`}
                            disabled={run.isPending || set.running}
                            title={set.running ? 'Its questions are being answered.' : COUNTS_NOTE}
                            onClick={() => run.mutate(set)}
                          >
                            Run now
                          </Button>
                          <IconButton
                            label={`${set.enabled ? 'Turn off' : 'Turn on'} ${set.title}`}
                            disabled={turn.isPending}
                            onClick={() => turn.mutate(set)}
                          >
                            {set.enabled ? <Pause className="size-4" /> : <Play className="size-4" />}
                          </IconButton>
                          <IconButton
                            label={`Change ${set.title}`}
                            onClick={() => {
                              setSaid(null)
                              setEditing({ set })
                            }}
                          >
                            <Pencil className="size-4" />
                          </IconButton>
                          <IconButton label={`Delete ${set.title}`} danger disabled={remove.isPending} onClick={() => setAsking(set)}>
                            <Trash2 className="size-4" />
                          </IconButton>
                        </span>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
        <ReportsSection />
      </div>
      {asking && <Confirm {...setDeletionQuestion(asking)} onConfirm={() => remove.mutate(asking)} onClose={() => setAsking(null)} />}
    </div>
  )
}

/** A question set written whole: its name, its questions one a line, when it is asked, whom its reports are told to, its sentence said as it is chosen. */
function QuestionSetForm({ set, maxQuestions, onClose }: { set: QuestionSet | null; maxQuestions: number; onClose: (saved: string | null) => void }) {
  const { project } = useWorkspace()
  const me = useMe()
  const queryClient = useQueryClient()
  const id = useId()
  const [draft, setDraft] = useState<QuestionSetDraft>(() => draftOf(set))
  const [problem, setProblem] = useState<string | null>(null)
  const change = (patch: Partial<QuestionSetDraft>) => setDraft((current) => ({ ...current, ...patch }))
  // The hour is the maker's: their zone, or for a new one the member's own.
  const zone = set ? set.time_zone : me.time_zone || 'UTC'
  const maker = set && set.created_by !== me.id ? (set.made_by ?? 'whoever made it') : 'you'

  const save = useMutation({
    mutationFn: () => (set ? api.updateQuestionSet(set.id, inputOf(draft)) : api.createQuestionSet(project.id, inputOf(draft))),
    onMutate: () => setProblem(null),
    onSuccess: (saved) => {
      void queryClient.invalidateQueries({ queryKey: questionSetsKey(project.id) })
      onClose(`Saved “${saved.title}”.`)
    },
    onError: (error) => setProblem(error.message),
  })
  const shown = { ...inputOf(draft), questions: questionsOf(draft) }

  return (
    <form
      aria-labelledby={`${id}-heading`}
      className="@container space-y-3 rounded-md border border-line bg-surface p-2.5"
      onSubmit={(event) => {
        event.preventDefault()
        const found = draftProblem(draft, maxQuestions)
        if (found) setProblem(found)
        else save.mutate()
      }}
    >
      <h3 id={`${id}-heading`} className="text-[13px] font-semibold">
        {set ? `Change “${set.title}”` : 'New question set'}
      </h3>
      <InputField label="Name" value={draft.title} maxLength={200} autoFocus onChange={(event) => change({ title: event.target.value })} />
      <div>
        <label htmlFor={`${id}-questions`} className="mb-1 block text-xs font-medium text-muted">
          Questions, one a line
        </label>
        <textarea
          id={`${id}-questions`}
          aria-describedby={`${id}-questions-hint`}
          className={`${FIELD} min-h-24 resize-y placeholder:text-muted`}
          rows={4}
          maxLength={maxQuestions * (QUESTION_CHARS + 1)}
          placeholder={'Which invoices are due this week?\nWhich deliveries arrived late?'}
          value={draft.questions}
          onChange={(event) => change({ questions: event.target.value })}
        />
        <p id={`${id}-questions-hint`} className="mt-1 text-xs text-muted">
          At most {maxQuestions}, each asked of the whole project as in a chat. {COUNTS_NOTE}
        </p>
      </div>
      <div className="grid gap-2 @sm:grid-cols-3">
        <SelectField label="Asked" value={draft.schedule} onChange={(event) => change({ schedule: event.target.value as QuestionSetDraft['schedule'] })}>
          <option value="daily">Every day</option>
          <option value="weekly">Every week</option>
          <option value="none">Not on a schedule</option>
        </SelectField>
        {draft.schedule === 'weekly' && (
          <SelectField label="On" value={draft.weekday} onChange={(event) => change({ weekday: Number(event.target.value) })}>
            {WEEKDAYS.map((day, index) => (
              <option key={day} value={index}>
                {day}
              </option>
            ))}
          </SelectField>
        )}
        {draft.schedule !== 'none' && (
          <SelectField label="At" value={draft.hour} hint={zoneWords(zone)} onChange={(event) => change({ hour: Number(event.target.value) })}>
            {HOURS.map((hour) => (
              <option key={hour} value={hour}>
                {hourWords(hour)}
              </option>
            ))}
          </SelectField>
        )}
      </div>
      <Checkbox
        label="Also once new documents arrive in the project, when they have stopped arriving for a moment"
        checked={draft.on_arrivals}
        onChange={(event) => change({ on_arrivals: event.target.checked })}
      />
      <SelectField
        label="Tell its reports to"
        value={draft.audience}
        hint="In the app and by email as each chose for the notice: how many questions were answered, never an answer. A report you run yourself is told to you."
        onChange={(event) => change({ audience: event.target.value as QuestionSetDraft['audience'] })}
      >
        {REPORT_AUDIENCES.map((audience) => (
          <option key={audience.value} value={audience.value}>
            {audience.label}
          </option>
        ))}
      </SelectField>
      <p className="rounded-md bg-sunken px-3 py-2 text-xs text-ink">{questionSetSentence(shown, maker, zone)}</p>
      {problem && (
        <p role="alert" className="text-xs text-bad">
          {problem}
        </p>
      )}
      <div className="flex justify-end gap-2">
        <Button onClick={() => onClose(null)}>Cancel</Button>
        <Button type="submit" variant="primary" disabled={save.isPending}>
          {save.isPending ? 'Saving…' : 'Save'}
        </Button>
      </div>
    </form>
  )
}

/** The project's reports, newest first, each opening in the library's place, asked again while one is being answered. */
function ReportsSection() {
  const workspace = useWorkspace()
  const { project } = workspace
  const reports = useInfiniteQuery({
    queryKey: reportsKey(project.id),
    queryFn: ({ pageParam }) => api.questionReports(project.id, pageParam),
    initialPageParam: null as number | null,
    getNextPageParam: (page) => page.next_before,
    refetchInterval: (query) => (query.state.data?.pages[0]?.reports.some(reporting) ? REPORT_POLL : false),
  })
  const listed = reports.data?.pages.flatMap((page) => page.reports) ?? []

  return (
    <section aria-labelledby="question-reports" className="space-y-2 pt-2">
      <h3 id="question-reports" className="text-xs font-semibold text-muted">
        Reports
      </h3>
      {reports.isError && <Notice tone="bad">Could not load the reports: {reports.error.message}</Notice>}
      {reports.data && listed.length === 0 && <p className="text-xs text-muted">No reports yet.</p>}
      {listed.length > 0 && (
        <ul aria-label="Reports" className="space-y-1.5">
          {listed.map((report) => (
            <li key={report.id}>
              <button
                type="button"
                onClick={() => workspace.showReport(report.id)}
                className="w-full rounded-md border border-line bg-surface px-3 py-2 text-left transition hover:bg-sunken"
              >
                <span className="block text-[13px] font-medium break-words">{reportTitle(report)}</span>
                <span className="block text-xs text-muted">{reportByline(report)}</span>
                <span className="flex items-center gap-1.5 text-xs text-ink">
                  {reporting(report) && <Spinner className="size-3" />}
                  {reportStatusWords(report)}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {reports.hasNextPage && (
        <Button disabled={reports.isFetchingNextPage} onClick={() => void reports.fetchNextPage()}>
          {reports.isFetchingNextPage ? 'Loading…' : 'Show more reports'}
        </Button>
      )}
    </section>
  )
}

/**
 * A report of a question set, in the library's place: what made it and how far it is, then each
 * question as it was asked with the agent's answer, citing the passages it read, as the worker
 * answers them one after another.
 */
export function ReportView({ reportId }: { reportId: number }) {
  const workspace = useWorkspace()
  const changes = mayChange(useMe().role)
  const queryClient = useQueryClient()
  const currency = useStatus().data?.currency
  const heading = useRef<HTMLHeadingElement>(null)
  const [asking, setAsking] = useState(false)
  const [refused, setRefused] = useState<Error | null>(null)
  useEffect(() => {
    if (window.document.activeElement === window.document.body) heading.current?.focus()
  }, [])
  const report = useQuery({
    queryKey: reportKey(reportId),
    queryFn: () => api.questionReport(reportId),
    refetchInterval: (query) => (query.state.data && reporting(query.state.data) ? REPORT_POLL : false),
  })
  const data = report.data
  const settled = data != null && !reporting(data)
  // Once the report settles, the lists say so too.
  useEffect(() => {
    if (settled && data) {
      void queryClient.invalidateQueries({ queryKey: reportsKey(data.project_id) })
      void queryClient.invalidateQueries({ queryKey: questionSetsKey(data.project_id) })
    }
  }, [settled, data, queryClient])
  const remove = useMutation({
    mutationFn: () => api.deleteQuestionReport(reportId),
    onSuccess: () => {
      if (data) void queryClient.invalidateQueries({ queryKey: reportsKey(data.project_id) })
      workspace.showQuestionSets()
    },
    onError: (error) => setRefused(error),
  })
  const open = (source: Source) => workspace.openDocument(source.document_id, { passage: source.id })
  const usage = data?.usage.answer

  return (
    <div className="flex h-full min-h-0 flex-col bg-page">
      <div className="flex h-11 shrink-0 items-center gap-1.5 border-b border-line bg-surface px-2">
        <IconButton label="Back to the question sets" onClick={workspace.showQuestionSets}>
          <ArrowLeft className="size-4" />
        </IconButton>
        <h2 ref={heading} tabIndex={-1} className="flex-1 truncate text-[13px] font-semibold">
          {data ? reportTitle(data) : 'Report'}
        </h2>
        {changes && data && (
          <IconButton label="Delete this report" danger disabled={remove.isPending || reporting(data)} onClick={() => setAsking(true)}>
            <Trash2 className="size-4" />
          </IconButton>
        )}
      </div>
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3">
        {report.isPending && <Loading />}
        {report.isError && <Notice tone="bad">Could not load the report: {report.error.message}</Notice>}
        {refused && <Refused error={refused} onDismiss={() => setRefused(null)} />}
        {data && (
          <>
            <div className="space-y-0.5">
              <p className="text-xs text-muted">{reportByline(data)}</p>
              <p className="flex items-center gap-1.5 text-xs text-ink">
                {reporting(data) && <Spinner className="size-3" />}
                {reportStatusWords(data)}
              </p>
            </div>
            {data.status === 'failed' && data.error && <Notice tone="bad">{data.error}</Notice>}
            <ol aria-label="Questions and answers" className="space-y-2">
              {data.answers.map((answer, index) => {
                const waiting = answerStatusWords(answer)
                return (
                  <li key={index} className="space-y-2 rounded-md border border-line bg-surface px-3 py-2.5">
                    <h3 className="text-[13px] font-semibold break-words">{answer.question}</h3>
                    {answer.status === 'done' ? (
                      <>
                        <CitedText text={answer.content} sources={answer.sources} onOpen={open} />
                        {answer.notice && <p className="text-xs text-muted">{answer.notice}</p>}
                      </>
                    ) : answer.status === 'failed' ? (
                      <Notice tone="warn">{waiting}</Notice>
                    ) : (
                      <p className="flex items-center gap-2 text-xs text-ink">
                        {answer.status === 'running' && <Spinner className="size-3.5" />} {waiting}
                      </p>
                    )}
                  </li>
                )
              })}
            </ol>
            <p className="text-[11px] text-muted">
              Answered by the model from the project’s documents as they read then: check what matters at its source.
              {usage?.calls ? ` ${describeAnswerUsage(usage, currency)}` : ''}
            </p>
          </>
        )}
      </div>
      {asking && data && <Confirm {...reportDeletionQuestion(data)} onConfirm={() => remove.mutate()} onClose={() => setAsking(false)} />}
    </div>
  )
}
