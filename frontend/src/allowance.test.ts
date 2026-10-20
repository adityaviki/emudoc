import { describe, expect, it } from 'vitest'
import type { Figure, UsagePage } from './api.ts'
import { allowedOf, dayOf, describeFigure, describeNear, heights, monthOf, nearLimits, percent, standingOf } from './allowance.ts'

const figure = (used: number, allowed: number | null, bought = 0): Figure => ({ used, allowed, bought })

const page = (pages: Figure, questions: Figure): UsagePage => ({
  month: '2026-10-01',
  next_month: '2026-11-01',
  projects: figure(1, 1),
  documents: figure(3, 25),
  storage_bytes: figure(2_400_000, 100_000_000),
  members: figure(1, 1),
  pages_per_month: pages,
  questions_per_month: questions,
  days: [],
  months: [],
})

describe('standingOf', () => {
  it('has no share without a limit', () => {
    expect(standingOf(figure(12, null))).toEqual({ share: null, level: 'unlimited' })
  })

  it('is near from four fifths of the limit on, the packs bought counted in it', () => {
    expect(standingOf(figure(159, 200))).toEqual({ share: 0.795, level: 'within' })
    expect(standingOf(figure(160, 200))).toEqual({ share: 0.8, level: 'near' })
    expect(standingOf(figure(160, 200, 100)).level).toBe('within')
    expect(allowedOf(figure(160, 200, 100))).toBe(300)
  })

  it('tells a limit reached from one passed once the plan was lowered', () => {
    expect(standingOf(figure(1, 1))).toEqual({ share: 1, level: 'reached' })
    expect(standingOf(figure(3, 1))).toEqual({ share: 3, level: 'over' })
    // A figure agreed at nothing is reached at once, and passed by anything.
    expect(standingOf(figure(0, 0)).level).toBe('reached')
    expect(standingOf(figure(2, 0))).toEqual({ share: 1, level: 'over' })
  })
})

describe('describeFigure', () => {
  it('words the count against what may be, or alone', () => {
    expect(describeFigure(figure(1234, 5000))).toBe(`${(1234).toLocaleString()} of ${(5000).toLocaleString()}`)
    expect(describeFigure(figure(170, 200, 100))).toBe('170 of 300')
    expect(describeFigure(figure(7, null))).toBe('7')
    expect(describeFigure(figure(2, 10), (value) => `${value} MB`)).toBe('2 MB of 10 MB')
  })
})

describe('describeNear', () => {
  it('says nothing below four fifths of the month, nor without a limit', () => {
    expect(describeNear('pages_per_month', page(figure(100, 200), figure(0, 50)))).toBeNull()
    expect(describeNear('questions_per_month', page(figure(0, 200), figure(900, null)))).toBeNull()
    expect(nearLimits(page(figure(100, 200), figure(900, null)))).toEqual([])
  })

  it('says how near, at or past the limit the month is, and when it starts afresh', () => {
    const october = monthOf('2026-10-01')
    const first = dayOf('2026-11-01', 'long')
    const near = page(figure(170, 200), figure(50, 50))
    expect(describeNear('pages_per_month', near)).toBe(`The organisation has indexed 170 of its 200 pages for ${october} (${percent(0.85)}).`)
    expect(describeNear('questions_per_month', near)).toBe(
      `The organisation has asked all 50 of its questions for ${october}: no more are answered until ${first}.`,
    )
    expect(nearLimits(near)).toEqual(['pages_per_month', 'questions_per_month'])
    expect(describeNear('pages_per_month', page(figure(250, 200), figure(0, 50)))).toBe(
      `The organisation has indexed 250 pages in ${october}, more than the 200 it may: no more are indexed until ${first}.`,
    )
  })
})

describe('heights', () => {
  it('scales every bar to the tallest, and leaves them flat when nothing was used', () => {
    expect(heights([0, 5, 10])).toEqual([0, 0.5, 1])
    expect(heights([0, 0])).toEqual([0, 0])
    expect(heights([])).toEqual([])
  })
})

describe('dayOf and monthOf', () => {
  it('word the UTC day or month the server names, whatever the zone', () => {
    const utc = { timeZone: 'UTC' } as const
    expect(dayOf('2026-10-14')).toBe(new Date(Date.UTC(2026, 9, 14)).toLocaleDateString(undefined, { day: 'numeric', month: 'short', ...utc }))
    expect(monthOf('2026-01-01', 'long', true)).toBe(new Date(Date.UTC(2026, 0, 1)).toLocaleDateString(undefined, { month: 'long', year: 'numeric', ...utc }))
    // The first of a month is never the last day of the one before, west of Greenwich.
    expect(monthOf('2026-11-01')).toBe(new Date(Date.UTC(2026, 10, 15)).toLocaleDateString(undefined, { month: 'long', ...utc }))
  })
})
