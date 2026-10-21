// The `jobs` and `failed-jobs` commands' report: each kind's jobs by state, the running ones
// with their leases, and the dead letters with why each failed.

import { useQuery } from '@tanstack/react-query'
import { RefreshCw } from 'lucide-react'
import { Button, Loading, Notice, Section } from '../components/ui.tsx'
import { operatorApi } from './api.ts'
import { Cell, Heading, Table } from './Table.tsx'
import { ago, count, lease, span } from './words.ts'

export function JobsPage() {
  const jobs = useQuery({ queryKey: ['operator', 'jobs'], queryFn: operatorApi.jobs, refetchInterval: 15_000 })
  return (
    <>
      <Heading title="Jobs" command="jobs">
        The queue as it stands, asked again every 15 seconds. To run failed documents again, use <code>failed-jobs --requeue</code>.
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
          {jobs.data.failed.length > 0 && (
            <Section
              title="Failed"
              description={`The jobs the worker gave up on in the last ${jobs.data.history_days} days, the newest first${
                jobs.data.failed_total > jobs.data.failed.length ? `: ${jobs.data.failed.length} of ${count(jobs.data.failed_total, 'job')}` : ''
              }. Each one's members see the failure; the reason here is the operator's.`}
            >
              <Table columns={[{ label: 'Job' }, { label: 'Kind' }, { label: 'About' }, { label: 'Ended' }, { label: 'Why' }]}>
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
                  </tr>
                ))}
              </Table>
            </Section>
          )}
        </div>
      )}
    </>
  )
}
