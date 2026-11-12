import { describe, expect, it } from 'vitest'
import type { Cost, Job, Worker } from './api.ts'
import {
  ago,
  amount,
  count,
  known,
  lease,
  release,
  span,
  topDay,
  workerStands,
} from './words.ts'

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

  it('words a worker as the jobs command does: running, stopped, or lost', () => {
    const worker: Worker = {
      id: 'host:12',
      version: 'development',
      started_at: '2026-10-21T09:00:00Z',
      last_seen_at: '2026-10-21T11:59:30Z',
      stopped_at: null,
      running: true,
    }
    expect(workerStands(worker, '2026-10-21T12:00:00Z')).toBe('running, seen 30 s ago')
    const stopped = { ...worker, running: false, last_seen_at: '2026-10-21T11:50:00Z', stopped_at: '2026-10-21T11:50:00Z' }
    expect(workerStands(stopped, '2026-10-21T12:00:00Z')).toBe('stopped 10 min ago')
    // Neither running nor stopped: it died, or lost the database.
    expect(workerStands({ ...worker, running: false, last_seen_at: '2026-10-21T11:40:00Z' }, '2026-10-21T12:00:00Z')).toBe(
      'not seen for 20 min',
    )
  })

  it("shows a commit's SHA by seven digits, and a checkout's word whole", () => {
    expect(release('3f2c1ab' + '0'.repeat(33))).toBe('3f2c1ab')
    expect(release('development')).toBe('development')
  })

  it('counts with the noun that fits', () => {
    expect(count(1, 'member')).toBe('1 member')
    expect(count(1200, 'job')).toBe('1,200 jobs')
  })
})
