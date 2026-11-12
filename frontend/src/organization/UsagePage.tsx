// The workspace's usage page: what it holds now, and the pages it indexed, the questions it asked
// and what its work cost day by day and month by month, in UTC as the server counts them
// (backend/app/api/usage.py). Every member reads it; the costs are the owners' and admins' alone,
// the server leaving them out for anyone else. It names no member.

import { useQuery } from '@tanstack/react-query'
import { ArrowLeft } from 'lucide-react'
import { Link } from 'react-router'
import { organizations } from '../api.ts'
import type { Metered, UsagePage as Usage } from '../api.ts'
import { useMe } from '../auth.ts'
import { Loading, Notice, Section } from '../components/ui.tsx'
import { formatSize } from '../format.ts'
import { useStatus } from '../hooks.ts'
import { dayOf, formatCost, heights, monthOf } from '../usage.ts'

const HELD = [
  { name: 'projects', label: 'Projects' },
  { name: 'documents', label: 'Documents' },
  { name: 'storage_bytes', label: 'Storage' },
  { name: 'members', label: 'Members' },
] as const

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
            What {me.organization.name} holds and has used, counted across its projects and members. Days and months are in UTC.
          </p>
        </div>
        {usage.isPending && <Loading />}
        {usage.isError && <Notice tone="bad">{usage.error.message}</Notice>}
        {usage.data && <UsageSections page={usage.data} />}
      </div>
    </div>
  )
}

function UsageSections({ page }: { page: Usage }) {
  const currency = useStatus().data?.currency
  // What the work cost is shown to whoever the server shows it to: owners and admins.
  const costs = currency !== undefined && [...page.days, ...page.months].some((item) => item.cost_micros !== null)
  const measures: { title: string; nouns: [string, string]; value: (item: Metered) => number; words?: (value: number) => string }[] = [
    { title: 'Pages indexed', nouns: ['page', 'pages'], value: (item) => item.pages },
    { title: 'Questions', nouns: ['question', 'questions'], value: (item) => item.questions },
    ...(costs && currency !== undefined ? [{ title: 'Cost of the models', nouns: ['', ''] as [string, string], value: (item: Metered) => item.cost_micros ?? 0, words: (value: number) => formatCost(value, currency) }] : []),
  ]
  // A Word file's pages, counted by its text, would otherwise read as a mistake.
  const byText = `A document without pages, such as a Word file or a spreadsheet, counts a page for every ${page.chars_per_page.toLocaleString()} characters of its text.`

  return (
    <div className="space-y-5">
      <Section
        title="Held now"
        description="What the workspace keeps: deleting a document frees its storage, and an open invitation counts as a member until it is accepted, revoked or runs out."
      >
        <dl className="divide-y divide-line border-t border-line">
          {HELD.map(({ name, label }) => (
            <div key={name} className="flex flex-wrap items-baseline gap-x-6 gap-y-1 py-2.5 text-[13px]">
              <dt className="w-28 shrink-0 text-muted">{label}</dt>
              <dd className="min-w-0 flex-1">
                {name === 'storage_bytes' ? formatSize(page[name]) : page[name].toLocaleString()}
                {heldNote(name, page) && <p className="mt-1 text-xs text-muted">{heldNote(name, page)}</p>}
              </dd>
            </div>
          ))}
        </dl>
      </Section>
      <Section title="Day by day" description={`What was used on each of the last ${page.days.length} days. ${byText}`}>
        <div className="space-y-5">
          {measures.map(({ title, nouns, value, words }) => (
            <History key={title} title={title} nouns={nouns} series={page.days} value={value} words={words} short={dayOf} long={(start) => dayOf(start, 'long')} />
          ))}
        </div>
      </Section>
      <Section title="Month by month" description={`What was used in each of the last ${page.months.length} months.`}>
        <div className="space-y-5">
          {measures.map(({ title, nouns, value, words }) => (
            <History
              key={title}
              title={title}
              nouns={nouns}
              series={page.months}
              value={value}
              words={words}
              short={(start) => monthOf(start, 'short', true)}
              long={(start) => monthOf(start, 'long', true)}
            />
          ))}
        </div>
      </Section>
    </div>
  )
}

/** What a figure held counts beyond its rows, or leaves out: the sample project, the app's own (backend/app/sample). */
function heldNote(name: (typeof HELD)[number]['name'], page: Usage): string | null {
  if (name === 'members') return 'Open invitations counted.'
  if (name === 'storage_bytes') return 'Earlier versions and My files counted.'
  if (!page.sample_documents) return null
  if (name === 'projects') return 'Not counted: the sample project.'
  if (name === 'documents') return `Not counted: the sample project’s ${count(page.sample_documents, 'document')}.`
  return null
}

/** A bar for each day or month of a history, as tall as its share of the tallest, read out one by one. */
function History({
  title,
  series,
  value,
  words,
  nouns: [one, many],
  short,
  long,
}: {
  title: string
  series: Metered[]
  value: (item: Metered) => number
  /** How an amount reads, a count of the nouns when none is given. */
  words?: (value: number) => string
  nouns: [string, string]
  short: (start: string) => string
  long: (start: string) => string
}) {
  const values = series.map(value)
  const shares = heights(values)
  const total = values.reduce((sum, item) => sum + item, 0)
  const worded = (amount: number) => (words ? words(amount) : count(amount, one, many))
  const said = (item: Metered, index: number) => `${long(item.start)}: ${worded(values[index])}`

  return (
    <figure>
      <figcaption className="flex items-baseline justify-between gap-3 text-[13px]">
        <span className="font-medium">{title}</span>
        <span className="text-xs text-muted">{worded(total)} in all</span>
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
