import { describe, expect, it } from 'vitest'
import type { Billing, Organization } from './api.ts'
import { billed, describeBilling, describeExcess, mayBuy, offerFor, thisMonth } from './billing.ts'

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

const free: Organization = {
  id: 1,
  name: 'Acme',
  slug: 'acme',
  plan: 'free',
  seats: null,
  allowance: {
    projects: 1,
    documents: 25,
    storage_bytes: 100_000_000,
    pages_per_month: 200,
    questions_per_month: 50,
    members: 1,
    agreed: [],
    pages_bought: 0,
    questions_bought: 0,
    over: [],
  },
  billing: { ...none, packs: [{ unit: 'pages', quantity: 1000 }] },
  region: null,
  chat_retention_days: 30,
  join_domains: [],
  join_role: 'member',
}
const team: Organization = { ...free, plan: 'team', seats: 3, billing: { ...none, status: 'active', customer: true } }

describe('offerFor', () => {
  it('sells an owner a pack of what ran out, beside the plans', () => {
    expect(offerFor('pages_per_month', free, 'owner')).toEqual({
      text: `Your organisation is on the Free plan. Buy more pages for ${thisMonth()}, or upgrade it on the organisation page.`,
      packs: [{ unit: 'pages', quantity: 1000 }],
      plans: true,
      manage: false,
    })
    // A pack of pages adds no projects.
    expect(offerFor('projects', free, 'owner')).toEqual({
      text: 'Your organisation is on the Free plan. Upgrade it on the organisation page.',
      packs: [],
      plans: true,
      manage: false,
    })
  })

  it('tells anyone else whom to ask, and offers them nothing to buy', () => {
    expect(offerFor('pages_per_month', free, 'admin')).toEqual({
      text: `Your organisation is on the Free plan. Ask an owner to buy more pages for ${thisMonth()}, or upgrade it.`,
      packs: [],
      plans: false,
      manage: false,
    })
    expect(offerFor('members', team, 'member').text).toBe('Your organisation is on the Team plan. Ask an owner to add seats.')
  })

  it('changes a subscription under Manage billing, seats on a plan sold by them', () => {
    expect(offerFor('members', team, 'owner')).toMatchObject({
      text: 'Your organisation is on the Team plan. Add seats under Manage billing.',
      manage: true,
      plans: false,
    })
    const pro = { ...team, plan: 'pro' as const, seats: null }
    expect(offerFor('projects', pro, 'owner')).toMatchObject({
      text: 'Your organisation is on the Pro plan. Change the plan under Manage billing.',
      manage: true,
    })
    // No larger plan is sold than Team: a higher limit is the operator's.
    expect(offerFor('projects', team, 'owner')).toEqual({
      text: 'Your organisation is on the Team plan. Ask the Emudoc team for a higher limit.',
      packs: [],
      plans: false,
      manage: false,
    })
  })

  it('leaves agreed terms and a deployment that sells nothing to the operator', () => {
    const agreed = { ...free, billing: { ...free.billing, managed: false } }
    expect(offerFor('projects', agreed, 'owner')).toEqual({
      text: 'Your organisation is on the Free plan. Its terms were agreed with the Emudoc team: ask them for a higher limit.',
      packs: [],
      plans: false,
      manage: false,
    })
    const unsold = { ...free, billing: { ...free.billing, sold: [], packs: [] } }
    expect(offerFor('questions_per_month', unsold, 'owner').text).toBe(
      'Your organisation is on the Free plan. Contact the Emudoc team to change it.',
    )
  })
})

describe('describeExcess', () => {
  it('says what the organisation holds past its plan, and nothing while it is within it', () => {
    expect(describeExcess(free)).toBeNull()
    const over: Organization = {
      ...free,
      allowance: {
        ...free.allowance,
        over: [
          { limit: 'projects', words: '3 projects of 1' },
          { limit: 'documents', words: '40 documents of 25' },
          { limit: 'members', words: '2 members of 1' },
        ],
      },
    }
    expect(describeExcess(over)).toBe(
      'Acme holds more than the Free plan allows: 3 projects of 1, 40 documents of 25 and 2 members of 1. Until it is within it, nothing can be added or changed, though everything it holds can still be read, asked about and deleted.',
    )
  })
})
