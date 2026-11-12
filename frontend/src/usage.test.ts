import { describe, expect, it } from 'vitest'
import type { Usage } from './api.ts'
import { dayOf, describeAnswerUsage, formatCost, heights, monthOf } from './usage.ts'

const used = (spent: Partial<Usage>): Usage => ({ model: null, cost_micros: null, ...spent })

describe('describeAnswerUsage', () => {
  it('names the model, then the calls, the tokens and the cost', () => {
    const usage = used({ model: 'google:gemini-2.5-flash-lite', calls: 2, input_tokens: 834, output_tokens: 56, cost_micros: 1200 })
    expect(describeAnswerUsage(usage, 'USD')).toBe(`google:gemini-2.5-flash-lite · 2 calls · 834 tokens in, 56 out · ${formatCost(1200, 'USD')}`)
  })

  it('leaves the cost out while the currency is not known', () => {
    expect(describeAnswerUsage(used({ calls: 1, cost_micros: 1200 }), undefined)).toBe('1 call')
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
