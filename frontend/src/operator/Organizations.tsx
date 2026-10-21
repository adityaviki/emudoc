// The organisations, the newest first and found by name, slug, id or a member's address, and
// one of them as the `plan` and `usage` commands show it, with its usage page as its members
// read it, its members and what its work cost; and its plan, seats and agreed figures changed
// as the `plan` command changes them.

import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft } from 'lucide-react'
import { useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useParams, useSearchParams } from 'react-router'
import { PLAN_LABELS, PLANS } from '../api.ts'
import type { Figure, Figured, Plan } from '../api.ts'
import { billed } from '../billing.ts'
import { Button, Chip, FIELD, Loading, Notice, Row, Section, SelectField } from '../components/ui.tsx'
import { formatDate, formatDateTime, formatSize } from '../format.ts'
import { operatorApi } from './api.ts'
import type { OrganizationDetail } from './api.ts'
import { Cell, Heading, Table } from './Table.tsx'
import { agreedOf, amount, count, known, seatsOf, tokensOf, topDay } from './words.ts'

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
      <Heading title="Organisations" command="plan <slug>">
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
              { label: 'Plan' },
              { label: 'Members', figure: true },
              { label: `Cost, ${days} days`, figure: true },
              { label: 'Subscription' },
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
                  {row.deletion_due_at && (
                    <span className="ml-2 text-bad">deleted {formatDate(row.deletion_due_at)}</span>
                  )}
                </Cell>
                <Cell>
                  {PLAN_LABELS[row.plan]}
                  {row.seats !== null && <span className="text-muted">, {count(row.seats, 'seat')}</span>}
                </Cell>
                <Cell figure>{row.members}</Cell>
                <Cell figure>{row.cost_micros === null ? '—' : amount(row.cost_micros, currency)}</Cell>
                <Cell className="text-muted">{row.subscription_status ?? '—'}</Cell>
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

const FIGURES: { name: Figured; label: string; size?: boolean }[] = [
  { name: 'projects', label: 'Projects' },
  { name: 'documents', label: 'Documents' },
  { name: 'storage_bytes', label: 'Storage', size: true },
  { name: 'members', label: 'Members' },
  { name: 'pages_per_month', label: 'Pages this month' },
  { name: 'questions_per_month', label: 'Questions this month' },
]

function figureWords(figure: Figure, size: boolean | undefined): string {
  const words = (value: number) => (size ? formatSize(value) : value.toLocaleString('en'))
  const allowed = figure.allowed === null ? 'no limit' : `of ${words(figure.allowed)}`
  const bought = figure.bought ? `, ${words(figure.bought)} bought` : ''
  return `${words(figure.used)} ${allowed}${bought}`
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
  const billing = organization.billing
  const [changing, setChanging] = useState(false)
  return (
    <>
      <Heading title={organization.name} command={`plan ${organization.slug}`}>
        {organization.slug} · id {organization.id} · {PLAN_LABELS[organization.plan]} plan
        {organization.region && ` · data kept in ${organization.region.toUpperCase()}`}
      </Heading>
      <div className="space-y-4">
        {organization.deletion_due_at && (
          <Notice tone="bad">An owner asked for it to be deleted: it is purged on {formatDateTime(organization.deletion_due_at)}.</Notice>
        )}
        {organization.allowance.over.length > 0 && (
          <Notice>
            It holds more than its plan allows ({organization.allowance.over.map((excess) => excess.words).join(', ')}), and is read-only until it is within it.
          </Notice>
        )}
        <div className="grid gap-4 lg:grid-cols-2">
          <Section title="Plan" description="What it is held to: its plan's figures, its seats, and what was agreed with it in their place.">
            {changing ? (
              <PlanForm detail={detail} onDone={() => setChanging(false)} />
            ) : (
              <>
                <Table columns={[{ label: 'Figure' }, { label: 'Held to' }, { label: 'The plan’s' }]}>
                  {detail.entitlements.map((figure) => (
                    <tr key={figure.name}>
                      <Cell className="font-mono text-xs">{figure.name}</Cell>
                      <Cell>
                        {figure.value} {figure.agreed && <Chip tone="accent">agreed</Chip>}
                      </Cell>
                      <Cell className="text-muted">{figure.plan_value}</Cell>
                    </tr>
                  ))}
                </Table>
                <Button className="mt-3" onClick={() => setChanging(true)}>
                  Change the plan
                </Button>
              </>
            )}
          </Section>
          <div className="space-y-4">
            <Section title="This month" description={`Counted as the plan's checks count them, from ${formatDate(usage.month)}.`}>
              <dl className="divide-y divide-line">
                {FIGURES.map((figure) => (
                  <Row key={figure.name} label={figure.label}>
                    {figureWords(usage[figure.name], figure.size)}
                  </Row>
                ))}
              </dl>
            </Section>
            <Section title="Billing">
              <dl className="divide-y divide-line">
                <Row label="Subscription">
                  {billing.status ?? 'none'}
                  {!billing.managed && <Chip>terms agreed</Chip>}
                </Row>
                {billing.trial_ends_at && <Row label="Trial ends">{formatDate(billing.trial_ends_at)}</Row>}
                {billing.renews_at && <Row label="Renews">{formatDate(billing.renews_at)}</Row>}
                {billing.ends_at && <Row label="Ends">{formatDate(billing.ends_at)}</Row>}
                {billing.payment_failed_at && <Row label="Payment failed">{formatDate(billing.payment_failed_at)}</Row>}
                <Row label="At Stripe">
                  <span className="font-mono text-xs">{[detail.stripe_customer_id, detail.stripe_subscription_id].filter(Boolean).join(' · ') || '—'}</span>
                </Row>
              </dl>
            </Section>
          </div>
        </div>
        <Section
          title="Model costs"
          description={`From ${formatDate(detail.since)} to ${formatDate(detail.until)}, in UTC, against its plan's daily limit.`}
        >
          <dl className="divide-y divide-line">
            <Row label="In all">{known(cost.cost_micros, tokensOf(cost), currency)}</Row>
            <Row label="Costliest day">{topDay(cost, currency)}</Row>
            {cost.days_at_limit !== null && <Row label="At its limit">{count(cost.days_at_limit, 'day')}</Row>}
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

/**
 * The organisation's plan, seats and agreed figures as a whole, as the `plan` command sets them: a figure left empty is the
 * plan's own, one written is agreed in its place, in the words the table shows. Every change is a row of its audit log.
 */
function PlanForm({ detail, onDone }: { detail: OrganizationDetail; onDone: () => void }) {
  const { organization } = detail
  const queryClient = useQueryClient()
  const [plan, setPlan] = useState<Plan>(organization.plan)
  const [seats, setSeats] = useState(organization.seats === null ? '' : String(organization.seats))
  const [written, setWritten] = useState<Record<string, string>>(detail.overrides)
  const offered = detail.plans.find((candidate) => candidate.name === plan) ?? detail.plans[0]
  const save = useMutation({
    mutationFn: () =>
      operatorApi.changePlan(organization.id, {
        plan,
        seats: seatsOf(seats, offered.by_the_seat),
        agreed: agreedOf(written, offered.by_the_seat),
      }),
    onSuccess: (changed) => {
      queryClient.setQueryData(['operator', 'organization', organization.id], changed)
      queryClient.invalidateQueries({ queryKey: ['operator', 'organizations'] })
      onDone()
    },
  })
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    save.mutate()
  }
  return (
    <form onSubmit={submit} className="space-y-3">
      {/* Stripe's next event about a subscription it bills sets the plan again, unless its terms are agreed. */}
      {billed(organization.billing) && organization.billing.managed && (
        <Notice>
          Stripe bills its subscription ({organization.billing.status}), which sets its plan: a change made here lasts until Stripe's next event about it,
          unless figures are agreed with it. Change the subscription in Stripe's dashboard instead.
        </Notice>
      )}
      <SelectField label="Plan" value={plan} onChange={(event) => setPlan(event.target.value as Plan)} className="max-w-xs">
        {PLANS.map((name) => (
          <option key={name} value={name}>
            {PLAN_LABELS[name]}
          </option>
        ))}
      </SelectField>
      <p className="text-xs leading-relaxed text-muted">
        Leave a figure empty for the plan's own, shown in grey. Write an agreed one as the table shows it: 500, 2 GB, {offered.figures.daily_spend_micros}, yes,
        or none for no limit.
      </p>
      <Table columns={[{ label: 'Figure' }, { label: 'Agreed' }]}>
        {detail.entitlements.map(({ name }) =>
          name === 'members' && offered.by_the_seat ? (
            <tr key={name}>
              <Cell className="font-mono text-xs">seats</Cell>
              <Cell>
                <input
                  type="number"
                  min={1}
                  step={1}
                  aria-label="seats"
                  value={seats}
                  onChange={(event) => setSeats(event.target.value)}
                  placeholder={offered.figures.members}
                  className={FIELD}
                />
              </Cell>
            </tr>
          ) : (
            <tr key={name}>
              <Cell className="font-mono text-xs">{name}</Cell>
              <Cell>
                <input
                  aria-label={name}
                  value={written[name] ?? ''}
                  onChange={(event) => setWritten({ ...written, [name]: event.target.value })}
                  placeholder={offered.figures[name]}
                  className={FIELD}
                />
              </Cell>
            </tr>
          ),
        )}
      </Table>
      {save.isError && <Notice tone="bad">{save.error.message}</Notice>}
      <div className="flex gap-2">
        <Button type="submit" variant="primary" disabled={save.isPending}>
          Save
        </Button>
        <Button onClick={onDone} disabled={save.isPending}>
          Cancel
        </Button>
      </div>
    </form>
  )
}
