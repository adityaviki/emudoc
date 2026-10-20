// The organisation's usage page: every figure its plan sets beside what it holds or has used of
// it, counted as the server's checks count it, the way past a month's limit from NEAR of it on,
// and its pages and questions day by day and month by month, in UTC as the plan counts them
// (backend/app/api/usage.py). Every member reads it, as every member reads the plan's figures on
// the organisation page; it names no member.

import { useQuery } from '@tanstack/react-query'
import { ArrowLeft } from 'lucide-react'
import type { ReactNode } from 'react'
import { Link } from 'react-router'
import { PLAN_LABELS, organizations } from '../api.ts'
import type { Figure, Figured, Me, Metered, MeteredMonth, UsagePage as Usage } from '../api.ts'
import { useMe } from '../auth.ts'
import { MONTHLY, dayOf, describeFigure, describeNear, heights, monthOf, nearLimits, percent, standingOf } from '../allowance.ts'
import type { Level, Monthly } from '../allowance.ts'
import { PlanLimitNotice } from '../components/PlanLimit.tsx'
import { Chip, Loading, Notice, Section } from '../components/ui.tsx'
import { formatSize } from '../format.ts'

const HELD = ['projects', 'documents', 'storage_bytes', 'members'] as const

const LABELS: Record<Figured, string> = {
  projects: 'Projects',
  documents: 'Documents',
  storage_bytes: 'Storage',
  members: 'Members',
  pages_per_month: 'Pages indexed',
  questions_per_month: 'Questions',
}

const BARS: Record<Level, string> = {
  unlimited: 'bg-accent',
  within: 'bg-accent',
  near: 'bg-warn',
  reached: 'bg-warn',
  over: 'bg-bad',
}

// The two measures of the history, the month's figures as they went day by day and month by month.
const MEASURES: { title: string; nouns: [string, string]; value: (item: Metered) => number; bought: (month: MeteredMonth) => number }[] = [
  { title: 'Pages indexed', nouns: ['page', 'pages'], value: (item) => item.pages, bought: (month) => month.pages_bought },
  { title: 'Questions', nouns: ['question', 'questions'], value: (item) => item.questions, bought: (month) => month.questions_bought },
]

const count = (value: number, one: string, many = `${one}s`) => `${value.toLocaleString()} ${value === 1 ? one : many}`

export function UsagePage() {
  const me = useMe()
  const usage = useQuery({ queryKey: ['organization', 'usage'], queryFn: organizations.usage })
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-2xl px-6 py-10">
        <Link to="/organization" className="inline-flex items-center gap-1 text-[13px] text-muted hover:text-ink">
          <ArrowLeft className="size-3.5" />
          {me.organization.name}
        </Link>
        <div className="mt-3 mb-6">
          <h1 className="text-2xl font-semibold tracking-tight">Usage</h1>
          <p className="mt-1 text-[13px] text-muted">
            What {me.organization.name} holds and has used of what the {PLAN_LABELS[me.organization.plan]} plan allows, counted across its
            projects and members. Days and months are in UTC, as the plan counts them.
          </p>
        </div>
        {usage.isPending && <Loading />}
        {usage.isError && <Notice tone="bad">{usage.error.message}</Notice>}
        {usage.data && <UsageSections me={me} page={usage.data} />}
      </div>
    </div>
  )
}

function UsageSections({ me, page }: { me: Me; page: Usage }) {
  const { agreed } = me.organization.allowance
  const month = monthOf(page.month)
  const plain = (value: number) => value.toLocaleString()
  const bought = (name: Monthly) => {
    const { allowed, bought } = page[name]
    return allowed !== null && bought > 0
      ? `${allowed.toLocaleString()} a month on the plan, and ${bought.toLocaleString()} more bought for ${month}.`
      : null
  }
  // A Word file's pages, counted by its text, would otherwise read as a mistake.
  const byText = `A document without pages, such as a Word file or a spreadsheet, counts a page for every ${page.chars_per_page.toLocaleString()} characters of its text.`
  const seats = me.organization.seats !== null

  return (
    <div className="space-y-5">
      <Section title="This month" description={`Since ${dayOf(page.month, 'long')}; they start afresh on ${dayOf(page.next_month, 'long')}.`}>
        <dl className="divide-y divide-line border-t border-line">
          {MONTHLY.map((name) => (
            <FigureRow key={name} label={LABELS[name]} figure={page[name]} words={plain} agreed={agreed.includes(name)}>
              {[bought(name), name === 'pages_per_month' ? byText : null].filter(Boolean).join(' ')}
            </FigureRow>
          ))}
        </dl>
        {nearLimits(page).map((name) => (
          <div key={name} className="mt-3">
            <PlanLimitNotice refusal={{ message: describeNear(name, page)!, limit: name }} />
          </div>
        ))}
      </Section>
      <Section
        title="Held now"
        description="What the organisation keeps: deleting a document frees its place and its storage, and an open invitation takes a member’s place until it is accepted, revoked or runs out."
      >
        <dl className="divide-y divide-line border-t border-line">
          {HELD.map((name) => (
            <FigureRow
              key={name}
              label={LABELS[name]}
              figure={page[name]}
              words={name === 'storage_bytes' ? formatSize : plain}
              agreed={agreed.includes(name)}
            >
              {heldNote(name, page, seats)}
            </FigureRow>
          ))}
        </dl>
      </Section>
      <Section title="Day by day" description={`The pages indexed and the questions asked on each of the last ${page.days.length} days.`}>
        <div className="space-y-5">
          {MEASURES.map(({ title, nouns, value }) => (
            <History key={title} title={title} nouns={nouns} series={page.days} value={value} short={dayOf} long={(start) => dayOf(start, 'long')} />
          ))}
        </div>
      </Section>
      <Section title="Month by month" description={`The pages indexed and the questions asked in each of the last ${page.months.length} months.`}>
        <div className="space-y-5">
          {MEASURES.map(({ title, nouns, value, bought }) => (
            <History
              key={title}
              title={title}
              nouns={nouns}
              series={page.months}
              value={value}
              short={(start) => monthOf(start, 'short', true)}
              long={(start) => monthOf(start, 'long', true)}
              extra={bought}
            />
          ))}
        </div>
      </Section>
    </div>
  )
}

/** One figure beside its limit, with a bar of the share used while it has one. */
function FigureRow({
  label,
  figure,
  words,
  agreed,
  children,
}: {
  label: string
  figure: Figure
  words: (value: number) => string
  agreed: boolean
  children?: ReactNode
}) {
  const { share, level } = standingOf(figure)
  return (
    <div className="flex flex-wrap items-baseline gap-x-6 gap-y-1 py-2.5 text-[13px]">
      <dt className="w-28 shrink-0 text-muted">{label}</dt>
      <dd className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className={level === 'over' ? 'text-bad' : undefined}>{describeFigure(figure, words)}</span>
          {share === null && <span className="text-muted">No limit</span>}
          {agreed && (
            <span title="Agreed with the organisation in place of the plan's">
              <Chip>Agreed</Chip>
            </span>
          )}
          {share !== null && <span className="ml-auto text-xs text-muted tabular-nums">{percent(share)}</span>}
        </div>
        {share !== null && (
          <div aria-hidden className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-sunken">
            <div className={`h-full rounded-full ${BARS[level]}`} style={{ width: `${Math.min(share, 1) * 100}%` }} />
          </div>
        )}
        {children && <p className="mt-1 text-xs text-muted">{children}</p>}
      </dd>
    </div>
  )
}

/** What a figure held counts beyond its rows, or leaves out: the sample project counts against nothing (backend/app/sample). */
function heldNote(name: (typeof HELD)[number], page: Usage, seats: boolean): string | null {
  if (name === 'members') return seats ? 'The seats bought for the plan, open invitations counted.' : 'Open invitations counted.'
  if (!page.sample_documents) return null
  if (name === 'projects') return 'Not counted: the sample project.'
  if (name === 'documents') return `Not counted: the sample project’s ${count(page.sample_documents, 'document')}.`
  return null
}

/** A bar for each day or month of a history, as tall as its share of the tallest, read out one by one. */
function History<T extends Metered>({
  title,
  series,
  value,
  nouns: [one, many],
  short,
  long,
  extra,
}: {
  title: string
  series: T[]
  value: (item: T) => number
  nouns: [string, string]
  short: (start: string) => string
  long: (start: string) => string
  /** The packs bought for a month, on top of the plan's. */
  extra?: (item: T) => number
}) {
  const values = series.map(value)
  const shares = heights(values)
  const total = values.reduce((sum, item) => sum + item, 0)
  const said = (item: T, index: number) => {
    const bought = extra?.(item) ?? 0
    return `${long(item.start)}: ${count(values[index], one, many)}${bought ? `, and ${bought.toLocaleString()} more bought` : ''}`
  }

  return (
    <figure>
      <figcaption className="flex items-baseline justify-between gap-3 text-[13px]">
        <span className="font-medium">{title}</span>
        <span className="text-xs text-muted">{count(total, one, many)} in all</span>
      </figcaption>
      <ol className="mt-2 flex h-20 items-end gap-px border-b border-line">
        {series.map((item, index) => (
          <li key={item.start} title={said(item, index)} className="flex h-full min-w-0 flex-1 items-end">
            <span className="sr-only">{said(item, index)}</span>
            {values[index] > 0 && (
              <span aria-hidden className="w-full rounded-t-sm bg-accent" style={{ height: `${Math.max(shares[index] * 100, 4)}%` }} />
            )}
          </li>
        ))}
      </ol>
      <div aria-hidden className="mt-1 flex justify-between text-[11px] text-muted">
        <span>{short(series[0].start)}</span>
        <span>{short(series[series.length - 1].start)}</span>
      </div>
    </figure>
  )
}
