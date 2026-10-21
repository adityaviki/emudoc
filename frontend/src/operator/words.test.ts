import { describe, expect, it } from 'vitest'
import type { Cost, Job } from './api.ts'
import { agreedOf, ago, amount, choiceOf, chosenOf, count, flagWords, known, lease, rolledOut, seatsOf, span, topDay } from './words.ts'

const cost = (top: number | null, limit: number | null): Cost => ({
  cost_micros: 0,
  calls: 0,
  input_tokens: 0,
  output_tokens: 0,
  answers: 0,
  pages: 0,
  top_day_micros: top,
  limit_micros: limit,
  days_at_limit: null,
})

describe('amounts', () => {
  it('words millionths to the cent, or to four places below one', () => {
    expect(amount(3_000_000, 'EUR')).toBe('3.00 EUR')
    expect(amount(1_234_567_000, 'USD')).toBe('1,234.57 USD')
    expect(amount(1_500, 'EUR')).toBe('0.0015 EUR')
    expect(amount(0, 'EUR')).toBe('0.00 EUR')
  })

  it('says a cost is unpriced only when tokens were recorded without a price', () => {
    expect(known(null, 120, 'EUR')).toBe('unpriced')
    expect(known(null, 0, 'EUR')).toBe('0.00 EUR')
    expect(known(250_000, 120, 'EUR')).toBe('0.25 EUR')
  })

  it('sets the costliest day against the daily limit', () => {
    expect(topDay(cost(1_200_000, 1_000_000), 'EUR')).toBe('1.20 EUR, 120 % of 1.00 EUR')
    expect(topDay(cost(1_200_000, null), 'EUR')).toBe('1.20 EUR')
    expect(topDay(cost(null, 1_000_000), 'EUR')).toBe('—')
  })
})

describe('time', () => {
  it('words a span in the largest unit that reads well', () => {
    expect(span(44.6)).toBe('45 s')
    expect(span(720)).toBe('12 min')
    expect(span(3 * 3600 + 300)).toBe('3 h 5 min')
    expect(span(7200)).toBe('2 h')
    expect(span(3 * 86_400)).toBe('3 days')
    expect(span(-5)).toBe('0 s')
  })

  it('words a lease as the time left or how long ago it ran out', () => {
    const job = { lease_until: '2026-10-21T12:02:00Z' } as Job
    expect(lease(job, '2026-10-21T12:00:00Z')).toBe('2 min left')
    expect(lease(job, '2026-10-21T12:05:00Z')).toBe('ran out 3 min ago')
    expect(lease({ ...job, lease_until: null }, '2026-10-21T12:00:00Z')).toBe('')
    expect(ago('2026-10-21T11:00:00Z', '2026-10-21T12:00:00Z')).toBe('1 h ago')
  })

  it('counts with the noun that fits', () => {
    expect(count(1, 'member')).toBe('1 member')
    expect(count(1200, 'job')).toBe('1,200 jobs')
  })
})

describe("a plan's form", () => {
  it('agrees the figures written, leaving the rest to the plan', () => {
    expect(agreedOf({ projects: ' 500 ', documents: '', storage_bytes: '2 GB' }, false)).toEqual({ projects: '500', storage_bytes: '2 GB' })
    // On a plan sold by the seat, its members are its seats.
    expect(agreedOf({ members: '9', projects: '500' }, true)).toEqual({ projects: '500' })
    expect(agreedOf({ members: '9' }, false)).toEqual({ members: '9' })
  })

  it('buys seats only on a plan sold by the seat', () => {
    expect(seatsOf('5', true)).toBe(5)
    expect(seatsOf(' ', true)).toBeNull()
    expect(seatsOf('5', false)).toBeNull()
  })
})

describe('feature flags', () => {
  const flag = (on: boolean, chosen: boolean | null) => ({ name: 'early', title: 'Early access', share: 30, chosen, on })

  it('words how far a flag is rolled out, as the command does', () => {
    expect(rolledOut(0)).toBe('none')
    expect(rolledOut(30)).toBe('30 % of organisations')
    expect(rolledOut(100)).toBe('every organisation')
  })

  it('says whether a flag is on for an organisation and why', () => {
    expect(flagWords(flag(true, null))).toBe('on, as rolled out')
    expect(flagWords(flag(false, false))).toBe('off, chosen here')
  })

  it('holds a choice as the select does, and gives it back as the server takes it', () => {
    for (const chosen of [true, false, null]) expect(chosenOf(choiceOf(chosen))).toBe(chosen)
    expect(choiceOf(null)).toBe('rollout')
  })
})
