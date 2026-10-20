import { describe, expect, it } from 'vitest'
import type { Billing } from './api.ts'
import { billed, describeBilling, mayBuy } from './billing.ts'

const none: Billing = {
  sold: ['pro', 'team'],
  managed: true,
  status: null,
  trial_ends_at: null,
  renews_at: null,
  ends_at: null,
  payment_failed_at: null,
  grace_ends_at: null,
  customer: false,
  trial_days: 14,
  packs: [],
}
const date = (value: string) => value.slice(0, 10)
const now = new Date('2026-10-20T12:00:00Z')

describe('describeBilling', () => {
  it('says nothing before a subscription, nor after one ended', () => {
    expect(describeBilling(none, date, now)).toBeNull()
    expect(describeBilling({ ...none, status: 'canceled', customer: true }, date, now)).toBeNull()
  })

  it('dates a trial, a renewal and an end', () => {
    expect(describeBilling({ ...none, status: 'trialing', trial_ends_at: '2026-11-03T00:00:00Z' }, date, now)).toEqual({
      tone: 'good',
      text: 'In its trial until 2026-11-03, when the first payment is taken.',
    })
    expect(describeBilling({ ...none, status: 'active', renews_at: '2026-11-20T00:00:00Z' }, date, now)?.text).toBe('Paid, and renews on 2026-11-20.')
    const cancelled = describeBilling({ ...none, status: 'active', ends_at: '2026-11-20T00:00:00Z' }, date, now)
    expect(cancelled).toEqual({ tone: 'warn', text: 'Cancelled: it ends on 2026-11-20, and the organisation moves to the Free plan then.' })
  })

  it('says until when a failed payment keeps the plan, and once it no longer does', () => {
    const failed = { ...none, status: 'past_due', payment_failed_at: '2026-10-18T00:00:00Z' }
    expect(describeBilling({ ...failed, grace_ends_at: '2026-11-01T00:00:00Z' }, date, now)?.text).toContain('by 2026-11-01, or the organisation moves')
    expect(describeBilling({ ...failed, grace_ends_at: '2026-10-19T00:00:00Z' }, date, now)?.text).toBe(
      'A payment failed, so the organisation is on the Free plan until it is made under Manage billing.',
    )
  })
})

describe('mayBuy', () => {
  it('offers the plans while nothing is billed and billing sets the plan', () => {
    expect(mayBuy(none)).toBe(true)
    expect(mayBuy({ ...none, status: 'canceled' })).toBe(true)
    expect(mayBuy({ ...none, status: 'past_due' })).toBe(false)
    expect(billed({ ...none, status: 'incomplete' })).toBe(true)
    expect(mayBuy({ ...none, managed: false })).toBe(false)
    expect(mayBuy({ ...none, sold: [] })).toBe(false)
  })
})
