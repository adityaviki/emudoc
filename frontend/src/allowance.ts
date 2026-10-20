// What the usage page says of each figure the organisation's plan sets: what it holds or used of
// it against what it may, how near the limit that is, and the days and months of its history, as
// the server counts them (backend/app/api/usage.py). The server's checks refuse past the same
// counts, so the page and a refusal agree.

import type { Figure, UsagePage } from './api.ts'

/** The share of a limit from which the page says it is near. */
export const NEAR = 0.8

/** How a figure stands against its limit: none, within it, near it, at it, or past it once the plan was lowered. */
export type Level = 'unlimited' | 'within' | 'near' | 'reached' | 'over'

export interface Standing {
  /** What it used or holds as a share of what it may; null without a limit. */
  share: number | null
  level: Level
}

/** The month's figures, which start afresh on the first; the others are what is held now. */
export const MONTHLY = ['pages_per_month', 'questions_per_month'] as const
export type Monthly = (typeof MONTHLY)[number]

/** What it may of a figure, the packs bought for the month on top of the plan's; null for no limit. */
export function allowedOf(figure: Figure): number | null {
  return figure.allowed === null ? null : figure.allowed + figure.bought
}

export function standingOf(figure: Figure): Standing {
  const allowed = allowedOf(figure)
  if (allowed === null) return { share: null, level: 'unlimited' }
  if (figure.used > allowed) return { share: allowed ? figure.used / allowed : 1, level: 'over' }
  if (figure.used === allowed) return { share: 1, level: 'reached' }
  const share = figure.used / allowed
  return { share, level: share >= NEAR ? 'near' : 'within' }
}

/** "170 of 300", "2.4 MB of 100 MB", or the count alone without a limit, each number worded by `words`. */
export function describeFigure(figure: Figure, words: (value: number) => string = (value) => value.toLocaleString()): string {
  const allowed = allowedOf(figure)
  return allowed === null ? words(figure.used) : `${words(figure.used)} of ${words(allowed)}`
}

export const percent = (share: number) => share.toLocaleString(undefined, { style: 'percent', maximumFractionDigits: 0 })

const NOUNS: Record<Monthly, { many: string; done: string; until: string }> = {
  pages_per_month: { many: 'pages', done: 'indexed', until: 'no more are indexed' },
  questions_per_month: { many: 'questions', done: 'asked', until: 'no more are answered' },
}

/**
 * What the page warns of a month's figure from NEAR of its limit on, null below it or without
 * one: "The organisation has indexed 170 of its 200 pages for October (85%)."
 */
export function describeNear(name: Monthly, page: UsagePage): string | null {
  const figure = page[name]
  const { share, level } = standingOf(figure)
  const allowed = allowedOf(figure)
  if (share === null || allowed === null || level === 'within') return null
  const { many, done, until } = NOUNS[name]
  const month = monthOf(page.month)
  const afresh = `${until} until ${dayOf(page.next_month, 'long')}`
  const used = figure.used.toLocaleString()
  switch (level) {
    case 'near':
      return `The organisation has ${done} ${used} of its ${allowed.toLocaleString()} ${many} for ${month} (${percent(share)}).`
    case 'reached':
      return `The organisation has ${done} all ${allowed.toLocaleString()} of its ${many} for ${month}: ${afresh}.`
    default:
      return `The organisation has ${done} ${used} ${many} in ${month}, more than the ${allowed.toLocaleString()} it may: ${afresh}.`
  }
}

/** The month's figures from NEAR of their limits on, which the page warns of with the way past them. */
export function nearLimits(page: UsagePage): Monthly[] {
  return MONTHLY.filter((name) => describeNear(name, page) !== null)
}

/** Each bar of a history as a share of its tallest; all of them 0 when nothing was used. */
export function heights(values: number[]): number[] {
  const most = Math.max(0, ...values)
  return values.map((value) => (most ? value / most : 0))
}

// The server names a UTC day, or a month by its first day, as an ISO date ("2026-10-14"); the page
// words it in UTC too, as the plan counts it, whatever the browser's own zone.
const utc = (start: string) => new Date(`${start}T00:00:00Z`)

/** "Oct 14", or "October 14" when `month` is long. */
export function dayOf(start: string, month: 'short' | 'long' = 'short'): string {
  return utc(start).toLocaleDateString(undefined, { day: 'numeric', month, timeZone: 'UTC' })
}

/** "October", or "Oct" when short; with its year when `year` is true. */
export function monthOf(start: string, month: 'short' | 'long' = 'long', year = false): string {
  return utc(start).toLocaleDateString(undefined, { month, timeZone: 'UTC', ...(year ? { year: 'numeric' } : {}) })
}
