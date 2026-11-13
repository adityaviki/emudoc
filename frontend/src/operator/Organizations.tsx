// The organisations, the newest first and found by name, slug, id or a member's address, and
// one of them as the `usage` command shows it, with its usage page as its members read it, its
// members and what its work cost.

import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { ArrowLeft } from 'lucide-react'
import type { FormEvent } from 'react'
import { Link, useParams, useSearchParams } from 'react-router'
import type { UsagePage } from '../api.ts'
import { Button, FIELD, Loading, Notice, Row, Section } from '../components/ui.tsx'
import { formatDate, formatSize } from '../format.ts'
import { operatorApi } from './api.ts'
import type { OrganizationDetail } from './api.ts'
import { Cell, Heading, Table } from './Table.tsx'
import { amount, count, known, tokensOf, topDay } from './words.ts'

export function OrganizationsPage({ currency }: { currency: string }) {
  const [search, setSearch] = useSearchParams()
  const q = search.get('q') ?? ''
  const pages = useInfiniteQuery({
    queryKey: ['operator', 'organizations', q],
    queryFn: ({ pageParam }) => operatorApi.organizations(q, pageParam),
    initialPageParam: null as number | null,
    getNextPageParam: (page) => page.next_before,
  })
  const find = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const given = String(new FormData(event.currentTarget).get('q') ?? '').trim()
    setSearch(given ? { q: given } : {})
  }
  const rows = pages.data?.pages.flatMap((page) => page.organizations) ?? []
  const days = pages.data?.pages[0]?.cost_days ?? 30
  return (
    <>
      <Heading title="Organisations" command="usage <slug>">
        The newest first, with what each one's work cost the platform over the last {days} days.
      </Heading>
      <form onSubmit={find} className="mb-4 flex max-w-md gap-2" role="search">
        <input
          key={q}
          name="q"
          defaultValue={q}
          aria-label="Find organisations"
          placeholder="Name, slug, id or a member's address"
          className={FIELD}
        />
        <Button type="submit">Find</Button>
      </form>
      {pages.isPending ? (
        <Loading />
      ) : pages.isError ? (
        <Notice tone="bad">{pages.error.message}</Notice>
      ) : rows.length === 0 ? (
        <Notice tone="info">{q ? `No organisation matches “${q}”.` : 'There are no organisations yet.'}</Notice>
      ) : (
        <>
          <Table
            columns={[
              { label: 'Organisation' },
              { label: 'Members', figure: true },
              { label: `Cost, ${days} days`, figure: true },
              { label: 'Created' },
            ]}
          >
            {rows.map((row) => (
              <tr key={row.id}>
                <Cell>
                  <Link to={`/operator/organizations/${row.id}`} className="font-medium text-accent hover:underline">
                    {row.name}
                  </Link>
                  <span className="ml-2 text-muted">
                    {row.slug} · {row.id}
                  </span>
                </Cell>
                <Cell figure>{row.members}</Cell>
                <Cell figure>{row.cost_micros === null ? '—' : amount(row.cost_micros, currency)}</Cell>
                <Cell className="whitespace-nowrap text-muted">{formatDate(row.created_at)}</Cell>
              </tr>
            ))}
          </Table>
          {pages.hasNextPage && (
            <Button className="mt-3" onClick={() => pages.fetchNextPage()} disabled={pages.isFetchingNextPage}>
              Show more
            </Button>
          )}
        </>
      )}
    </>
  )
}

const HELD: { name: 'projects' | 'documents' | 'storage_bytes' | 'members'; label: string }[] = [
  { name: 'projects', label: 'Projects' },
  { name: 'documents', label: 'Documents' },
  { name: 'storage_bytes', label: 'Storage' },
  { name: 'members', label: 'Members' },
]

/** What the organisation holds now, and this month's pages and questions, as its usage page counts them. */
function heldRows(usage: UsagePage): { label: string; words: string }[] {
  const month = usage.months[usage.months.length - 1]
  return [
    ...HELD.map(({ name, label }) => ({
      label,
      words: name === 'storage_bytes' ? formatSize(usage[name]) : usage[name].toLocaleString('en'),
    })),
    { label: 'Pages this month', words: month.pages.toLocaleString('en') },
    { label: 'Questions this month', words: month.questions.toLocaleString('en') },
  ]
}

export function OrganizationPage({ currency }: { currency: string }) {
  const id = Number(useParams().id)
  const shown = useQuery({ queryKey: ['operator', 'organization', id], queryFn: () => operatorApi.organization(id) })
  return (
    <>
      <Link to="/operator/organizations" className="inline-flex items-center gap-1 text-[13px] text-muted hover:text-ink">
        <ArrowLeft className="size-3.5" />
        Organisations
      </Link>
      <div className="mt-3">
        {shown.isPending ? (
          <Loading />
        ) : shown.isError ? (
          <Notice tone="bad">{shown.error.message}</Notice>
        ) : (
          <Organization detail={shown.data} currency={currency} />
        )}
      </div>
    </>
  )
}

function Organization({ detail, currency }: { detail: OrganizationDetail; currency: string }) {
  const { organization, usage, cost } = detail
  return (
    <>
      <Heading title={organization.name} command={`usage ${organization.slug}`}>
        {organization.slug} · id {organization.id}
      </Heading>
      <div className="space-y-4">
        <Section title="This month" description={`What it holds now, and what it used from ${formatDate(usage.month)}.`}>
          <dl className="divide-y divide-line">
            {heldRows(usage).map((row) => (
              <Row key={row.label} label={row.label}>
                {row.words}
              </Row>
            ))}
          </dl>
        </Section>
        <Section
          title="Model costs"
          description={`From ${formatDate(detail.since)} to ${formatDate(detail.until)}, in UTC.`}
        >
          <dl className="divide-y divide-line">
            <Row label="In all">{known(cost.cost_micros, tokensOf(cost), currency)}</Row>
            <Row label="Costliest day">{topDay(cost, currency)}</Row>
            <Row label="Work">
              {count(cost.answers, 'answer')}, {count(cost.calls, 'model call')}, {count(tokensOf(cost), 'token')}, {count(cost.pages, 'page')}
            </Row>
          </dl>
          {detail.cost_days.length > 0 && (
            <div className="mt-3">
              <Table columns={[{ label: 'Day (UTC)' }, { label: 'Cost', figure: true }]}>
                {detail.cost_days.map((day) => (
                  <tr key={day.day}>
                    <Cell>{day.day}</Cell>
                    <Cell figure>{amount(day.cost_micros, currency)}</Cell>
                  </tr>
                ))}
              </Table>
            </div>
          )}
        </Section>
        <Section title="Members">
          <Table columns={[{ label: 'Name' }, { label: 'Address' }, { label: 'Role' }, { label: 'Two-factor' }, { label: 'Joined' }]}>
            {detail.members.map((member) => (
              <tr key={member.user_id}>
                <Cell>{member.name || '—'}</Cell>
                <Cell>{member.email}</Cell>
                <Cell>{member.role}</Cell>
                <Cell>{member.mfa_enabled ? 'on' : 'off'}</Cell>
                <Cell className="whitespace-nowrap text-muted">{formatDate(member.joined_at)}</Cell>
              </tr>
            ))}
          </Table>
        </Section>
      </div>
    </>
  )
}
