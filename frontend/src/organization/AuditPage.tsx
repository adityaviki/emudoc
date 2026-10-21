// The organisation's audit log, for its owners and admins (backend/app/api/audit.py): who did what and when, the
// newest first, a page at a time, of the actions, member, project and days chosen, and those rows as a CSV file to
// download. The words are audit.ts's; the days are the browser's.

import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { ArrowLeft, Download } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Link } from 'react-router'
import { api, auditExportUrl, isWithin, organizations } from '../api.ts'
import type { AuditEvent } from '../api.ts'
import { ACTIONS, KINDS, NO_CHOICES, actionsOf, describeDetail, filterOf, whoDid } from '../audit.ts'
import type { AuditChoices, AuditKind } from '../audit.ts'
import { useMe } from '../auth.ts'
import { Button, ButtonLink, FIELD, Loading, Notice } from '../components/ui.tsx'
import { formatDateTime } from '../format.ts'

export function AuditPage() {
  const me = useMe()
  const admin = isWithin('admin', me.role)
  const [choices, setChoices] = useState<AuditChoices>(NO_CHOICES)
  const filter = useMemo(() => filterOf(choices), [choices])
  const members = useQuery({ queryKey: ['organization', 'members'], queryFn: organizations.members, enabled: admin })
  const projects = useQuery({ queryKey: ['projects'], queryFn: api.projects, enabled: admin })
  const log = useInfiniteQuery({
    queryKey: ['audit', me.organization.id, filter],
    queryFn: ({ pageParam }) => api.audit(filter, pageParam),
    initialPageParam: null as number | null,
    getNextPageParam: (page) => page.next_before,
    enabled: admin,
  })
  const names = useMemo(() => new Map((members.data ?? []).map((member) => [member.user_id, member.name || member.email])), [members.data])
  const events = log.data?.pages.flatMap((page) => page.events) ?? []
  const choose = (change: Partial<AuditChoices>) => setChoices({ ...choices, ...change })

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-3xl px-6 py-10">
        <Link to="/organization" className="inline-flex items-center gap-1 text-[13px] text-muted hover:text-ink">
          <ArrowLeft className="size-3.5" />
          {me.organization.name}
        </Link>
        <div className="mt-3 mb-6 flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Audit log</h1>
            <p className="mt-1 text-[13px] text-muted">
              Who signed in, changed, shared or deleted what in {me.organization.name}, and when. It never holds what a
              document or a question says.
            </p>
          </div>
          {admin && (
            <ButtonLink href={auditExportUrl(filter)} download icon={<Download className="size-4" />}>
              Download CSV
            </ButtonLink>
          )}
        </div>
        {!admin ? (
          <Notice tone="info">Only the owners and admins of the organisation read its audit log.</Notice>
        ) : (
          <>
            <div className="mb-4 grid gap-2 text-[13px] sm:grid-cols-[2fr_1fr_1fr]">
              <select aria-label="What" className={FIELD} value={choices.what} onChange={(event) => choose({ what: event.target.value })}>
                <option value="">Every action</option>
                {(Object.keys(KINDS) as AuditKind[]).map((kind) => (
                  <optgroup key={kind} label={KINDS[kind]}>
                    <option value={`kind:${kind}`}>{`All of ${KINDS[kind].toLowerCase()}`}</option>
                    {actionsOf(kind).map((action) => (
                      <option key={action} value={action}>
                        {ACTIONS[action][0]}
                      </option>
                    ))}
                  </optgroup>
                ))}
              </select>
              <select aria-label="Who" className={FIELD} value={choices.who} onChange={(event) => choose({ who: event.target.value })}>
                <option value="">Anyone</option>
                {members.data?.map((member) => (
                  <option key={member.user_id} value={member.user_id}>
                    {member.name || member.email}
                  </option>
                ))}
              </select>
              <select aria-label="Project" className={FIELD} value={choices.project} onChange={(event) => choose({ project: event.target.value })}>
                <option value="">Any project</option>
                {projects.data?.map((project) => (
                  <option key={project.id} value={project.id}>
                    {project.name}
                  </option>
                ))}
              </select>
              <div className="flex flex-wrap items-center gap-2 sm:col-span-3">
                <label className="flex items-center gap-2 text-muted">
                  From
                  <input type="date" className={`${FIELD} w-auto!`} value={choices.from} max={choices.to || undefined} onChange={(event) => choose({ from: event.target.value })} />
                </label>
                <label className="flex items-center gap-2 text-muted">
                  to
                  <input type="date" className={`${FIELD} w-auto!`} value={choices.to} min={choices.from || undefined} onChange={(event) => choose({ to: event.target.value })} />
                </label>
                {Object.values(choices).some(Boolean) && (
                  <Button variant="ghost" onClick={() => setChoices(NO_CHOICES)}>
                    Clear
                  </Button>
                )}
              </div>
            </div>
            {log.isPending && <Loading />}
            {log.isError && <Notice tone="bad">{log.error.message}</Notice>}
            {log.isSuccess && events.length === 0 && <p className="py-8 text-center text-[13px] text-muted">Nothing was recorded that these choices pick.</p>}
            <ul className="divide-y divide-line border-y border-line">
              {events.map((event) => (
                <EventRow key={event.id} event={event} names={names} />
              ))}
            </ul>
            {log.hasNextPage && (
              <div className="mt-4 text-center">
                <Button onClick={() => log.fetchNextPage()} disabled={log.isFetchingNextPage}>
                  Show older
                </Button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}

function EventRow({ event, names }: { event: AuditEvent; names: ReadonlyMap<number, string> }) {
  const detail = describeDetail(event, names)
  const where = [event.project_name, event.ip].filter(Boolean).join(' · ')
  return (
    <li className="py-2.5 text-[13px]">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4">
        <p className="min-w-0">
          <span className="font-medium">{whoDid(event)}</span> <span className="text-muted">·</span> {ACTIONS[event.action][0]}
        </p>
        <time dateTime={event.occurred_at} className="shrink-0 text-xs text-muted">
          {formatDateTime(event.occurred_at)}
        </time>
      </div>
      {(detail || where) && (
        <p className="mt-0.5 truncate text-xs text-muted" title={JSON.stringify(event.detail)}>
          {[detail, where].filter(Boolean).join(' · ')}
        </p>
      )}
    </li>
  )
}
