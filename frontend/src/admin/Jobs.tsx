// The `jobs` and `failed-jobs` commands' report for the workspace: each kind's jobs by state,
// the running ones with their leases, the dead letters with why each failed, whose documents are
// queued again from here as `failed-jobs --requeue` queues them, and the workers seen lately.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { RefreshCw, RotateCcw } from 'lucide-react'
import { useState } from 'react'
import { Confirm } from '../components/Confirm.tsx'
import type { Question } from '../components/Confirm.tsx'
import { admin } from '../api.ts'
import { Button, Loading, Notice, Section } from '../components/ui.tsx'
import { Cell, Heading, Table } from './Table.tsx'
import { ago, count, lease, release, span, workerStands } from './words.ts'

// The one kind of job that runs again: an index job, from the stage its document stopped at.
const RUNS_AGAIN = 'index_document'

// What is asked before a failed job's document, or every one's, is queued again.
function runAgainQuestion(jobIds: number[] | null): Question {
  return jobIds === null
    ? {
        title: 'Run every failed document again?',
        body: 'The document of every failed index job is queued again, from the stage each stopped at. The jobs of other kinds are left as they are.',
        action: 'Run them again',
      }
    : { title: `Run job ${jobIds.join(', ')} again?`, body: 'Its document is queued again, from the stage it stopped at.', action: 'Run it again' }
}

export function JobsView() {
  const queryClient = useQueryClient()
  const jobs = useQuery({ queryKey: ['admin', 'jobs'], queryFn: admin.jobs, refetchInterval: 15_000 })
  const requeue = useMutation({
    mutationFn: admin.requeue,
    onSettled: () => queryClient.invalidateQueries({ queryKey: ['admin', 'jobs'] }),
  })
  // The jobs whose documents are to run again while the dialog asks: null for every failed one.
  const [asking, setAsking] = useState<{ jobIds: number[] | null } | null>(null)
  const runAgain = (jobIds: number[] | null) => setAsking({ jobIds })
  return (
    <>
      <Heading title="Jobs" command="jobs">
        The queue as it stands, asked again every 15 seconds. A failed index job's document is queued again from the stage it stopped at, as{' '}
        <code>failed-jobs --requeue</code> does, once its cause is fixed.
      </Heading>
      {jobs.isPending ? (
        <Loading />
      ) : jobs.isError ? (
        <Notice tone="bad">{jobs.error.message}</Notice>
      ) : (
        <div className="space-y-4">
          <div className="flex items-center gap-3">
            <Button icon={<RefreshCw className="size-3.5" />} onClick={() => jobs.refetch()} disabled={jobs.isFetching}>
              Ask again
            </Button>
          </div>
          {!jobs.data.workers.some((worker) => worker.running) && (
            <Notice tone="bad">
              No worker has been seen running in the last 3 minutes: nothing is indexed, answered, mailed or exported until one runs (
              <code>python -m app.worker</code>).
            </Notice>
          )}
          {jobs.data.queue.length === 0 ? (
            <Notice tone="info">Nothing is queued or running, and no job failed in the last {jobs.data.history_days} days.</Notice>
          ) : (
            <Table
              columns={[
                { label: 'Kind' },
                { label: 'Due', figure: true },
                { label: 'Waiting', figure: true },
                { label: 'Running', figure: true },
                { label: 'Failed', figure: true },
                { label: 'Oldest due waited', figure: true },
              ]}
            >
              {jobs.data.queue.map((queue) => (
                <tr key={queue.kind}>
                  <Cell className="font-mono text-xs">{queue.kind}</Cell>
                  <Cell figure>{queue.due.toLocaleString('en')}</Cell>
                  <Cell figure>{queue.waiting.toLocaleString('en')}</Cell>
                  <Cell figure>{queue.running.toLocaleString('en')}</Cell>
                  <Cell figure>{queue.failed.toLocaleString('en')}</Cell>
                  <Cell figure>{queue.oldest_due_seconds === null ? '—' : span(queue.oldest_due_seconds)}</Cell>
                </tr>
              ))}
            </Table>
          )}
          {jobs.data.running.length > 0 && (
            <Section title="Running" description="A lease that ran out is a worker that stopped or lost the database: the next free thread of its lane takes the job over.">
              <Table columns={[{ label: 'Job' }, { label: 'Kind' }, { label: 'About' }, { label: 'Attempt' }, { label: 'Due' }, { label: 'Lease' }, { label: 'Worker' }]}>
                {jobs.data.running.map((job) => (
                  <tr key={job.id}>
                    <Cell>{job.id}</Cell>
                    <Cell className="font-mono text-xs">{job.kind}</Cell>
                    <Cell>{job.about || '—'}</Cell>
                    <Cell className="whitespace-nowrap">
                      {job.attempts} of {job.max_attempts}
                    </Cell>
                    <Cell className="whitespace-nowrap">{ago(job.run_at, jobs.data.taken_at)}</Cell>
                    <Cell className={`whitespace-nowrap ${lease(job, jobs.data.taken_at).startsWith('ran out') ? 'text-bad' : ''}`}>
                      {lease(job, jobs.data.taken_at)}
                    </Cell>
                    <Cell className="font-mono text-xs">{job.worker_id}</Cell>
                  </tr>
                ))}
              </Table>
            </Section>
          )}
          {jobs.data.workers.length > 0 && (
            <Section
              title="Workers"
              description="The workers seen in the last day, the latest first. One neither running nor stopped died, or lost the database: its log's last lines say why."
            >
              <Table columns={[{ label: 'Worker' }, { label: 'Release' }, { label: 'Started' }, { label: 'Stands' }]}>
                {jobs.data.workers.map((worker) => (
                  <tr key={worker.id}>
                    <Cell className="font-mono text-xs">{worker.id}</Cell>
                    <Cell className="font-mono text-xs">{release(worker.version)}</Cell>
                    <Cell className="whitespace-nowrap">{ago(worker.started_at, jobs.data.taken_at)}</Cell>
                    <Cell className={`whitespace-nowrap ${worker.running ? '' : worker.stopped_at ? 'text-muted' : 'text-bad'}`}>
                      {workerStands(worker, jobs.data.taken_at)}
                    </Cell>
                  </tr>
                ))}
              </Table>
            </Section>
          )}
          {requeue.isError && <Notice tone="bad">{requeue.error.message}</Notice>}
          {requeue.isSuccess && (
            <Notice tone="info">
              {count(requeue.data.queued, 'document')} queued again; the worker indexes them while it runs.
              {requeue.data.lines.length > 0 && (
                <ul className="mt-1 list-disc pl-4">
                  {requeue.data.lines.map((line) => (
                    <li key={line}>{line}</li>
                  ))}
                </ul>
              )}
            </Notice>
          )}
          {jobs.data.failed.length > 0 && (
            <Section
              title="Failed"
              description={`The jobs the worker gave up on in the last ${jobs.data.history_days} days, the newest first${
                jobs.data.failed_total > jobs.data.failed.length ? `: ${jobs.data.failed.length} of ${count(jobs.data.failed_total, 'job')}` : ''
              }. Its members see the failure; the reason here is for whoever runs the server.`}
            >
              {jobs.data.failed.some((job) => job.kind === RUNS_AGAIN) && (
                <Button className="mb-3" icon={<RotateCcw className="size-3.5" />} onClick={() => runAgain(null)} disabled={requeue.isPending}>
                  Run every failed document again
                </Button>
              )}
              <Table columns={[{ label: 'Job' }, { label: 'Kind' }, { label: 'About' }, { label: 'Ended' }, { label: 'Why' }, { label: 'Action', unseen: true }]}>
                {jobs.data.failed.map((job) => (
                  <tr key={job.id}>
                    <Cell>{job.id}</Cell>
                    <Cell className="font-mono text-xs">{job.kind}</Cell>
                    <Cell>{job.about || '—'}</Cell>
                    <Cell className="whitespace-nowrap">
                      {job.finished_at ? ago(job.finished_at, jobs.data.taken_at) : '—'}
                      {!job.reported && <div className="text-xs text-muted">not mailed yet</div>}
                    </Cell>
                    <Cell className="text-xs break-words">{job.last_error}</Cell>
                    <Cell>
                      {job.kind === RUNS_AGAIN && (
                        <Button variant="ghost" onClick={() => runAgain([job.id])} disabled={requeue.isPending} aria-label={`Run job ${job.id} again`}>
                          Run again
                        </Button>
                      )}
                    </Cell>
                  </tr>
                ))}
              </Table>
            </Section>
          )}
        </div>
      )}
      {asking && (
        <Confirm
          {...runAgainQuestion(asking.jobIds)}
          danger={false}
          onConfirm={() => requeue.mutate(asking.jobIds)}
          onClose={() => setAsking(null)}
        />
      )}
    </>
  )
}
