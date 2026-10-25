import { describe, expect, it } from 'vitest'
import type { Billing, Organization, PlanChange } from './api.ts'
import { billed, describeBilling, describeChange, describeExcess, describeHeld, mayBuy, mayChange, offerFor, thisMonth } from './billing.ts'

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
    retention_days: 30,
    api_access: false,
    connectors: false,
    share_links: false,
    agreed: [],
    pages_bought: 0,
    questions_bought: 0,
    over: [],
  },
  billing: { ...none, packs: [{ unit: 'pages', quantity: 1000 }] },
  region: null,
  chat_retention_days: 30,
  audit_retention_days: 90,
  retention_held: null,
  language: null,
  chat_days: null,
  notice_defaults: [],
  mfa_required: false,
  session_days: null,
  max_session_days: 30,
  share_links: true,
  join_domains: [],
  join_role: 'member',
  deletion_due_at: null,
  flags_on: [],
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

describe('mayChange', () => {
  it('moves or cancels a subscription in its trial or paid, not one cancelled or waiting for a payment', () => {
    expect(mayChange({ ...none, status: 'active' })).toBe(true)
    expect(mayChange({ ...none, status: 'trialing' })).toBe(true)
    expect(mayChange({ ...none, status: 'active', ends_at: '2026-11-20T00:00:00Z' })).toBe(false)
    expect(mayChange({ ...none, status: 'past_due' })).toBe(false)
    expect(mayChange(none)).toBe(false)
    expect(mayChange({ ...none, status: 'active', managed: false })).toBe(false)
  })
})

const onTeam: Organization = {
  ...team,
  allowance: { ...free.allowance, projects: 50, documents: 10_000, storage_bytes: 100_000_000_000, pages_per_month: 25_000, questions_per_month: 5_000, members: 3 },
  chat_retention_days: null,
  audit_retention_days: 365,
}
const cancelled: PlanChange = {
  plan: 'free',
  seats: null,
  at: '2027-01-15T00:00:00Z',
  allowance: { ...free.allowance, over: [{ limit: 'members', words: '2 members of 1' }] },
  retention: { chat_days: 30, audit_days: 90 },
  held: { chat_days: null, audit_days: 365, until: '2027-02-14T00:00:00Z' },
  held_days: 30,
}

describe('describeChange', () => {
  it('says when a cancellation ends the plan, what it leaves read-only and what goes later', () => {
    const { figures, notes } = describeChange(cancelled, onTeam, date)
    expect(figures).toEqual([
      { label: 'Projects', now: '50', then: '1' },
      { label: 'Documents', now: '10,000', then: '25' },
      { label: 'Storage', now: '100 GB', then: '100 MB' },
      { label: 'Pages indexed', now: '25,000 a month', then: '200 a month' },
      { label: 'Questions', now: '5,000 a month', then: '50 a month' },
      { label: 'Members', now: '3', then: '1' },
      { label: 'Chats kept', now: 'Until deleted', then: '30 days' },
    ])
    expect(notes).toEqual([
      'The subscription ends on 2027-01-15, at the end of the period paid for, and the organisation then moves to the Free plan, keeping everything it holds.',
      'It would hold more than the Free plan allows (2 members of 1), so until it is within the plan nothing could be added or changed, though everything it holds could still be read, asked about and deleted.',
      'The Free plan keeps chats 30 days after their last question and audit log entries 90 days. Those older would be kept as now until 2027-02-14, and deleted then.',
    ])
  })

  it('leaves the price to Stripe, and names only what the new plan keeps for less time', () => {
    const toPro: PlanChange = {
      ...cancelled,
      plan: 'pro',
      at: null,
      allowance: { ...onTeam.allowance, projects: 10, members: 1, over: [] },
      retention: { chat_days: 365, audit_days: 365 },
      held: { chat_days: null, audit_days: 365, until: null },
    }
    const { notes } = describeChange(toPro, onTeam, date)
    expect(notes[0]).toMatch(/^Stripe’s page shows the new price .* the organisation is on the Pro plan, keeping everything it holds\.$/)
    expect(notes[1]).toBe('The Pro plan keeps chats 365 days after their last question. Those older would be kept as now for 30 days after the change, and deleted then.')
    const moreSeats = describeChange({ ...toPro, plan: 'team', seats: 5, allowance: { ...onTeam.allowance, members: 5, over: [] }, retention: { chat_days: null, audit_days: 365 }, held: null }, onTeam, date)
    expect(moreSeats).toEqual({
      figures: [{ label: 'Members', now: '3', then: '5' }],
      notes: [expect.stringContaining('on the Team plan with 5 seats')],
    })
  })
})

describe('describeHeld', () => {
  it('says what is kept as before after a lower plan, and until when', () => {
    expect(describeHeld(free, date)).toBeNull()
    const held = { ...free, retention_held: { chat_days: null, audit_days: 365, until: '2026-11-19T00:00:00Z' } }
    expect(describeHeld(held, date)).toBe(
      'Until 2026-11-19 it keeps chats until they are deleted and audit log entries 365 days, as its plan before did; then those older go.',
    )
    // A hold of what the plan keeps as long anyway says nothing.
    expect(describeHeld({ ...held, retention_held: { chat_days: 30, audit_days: 90, until: '2026-11-19T00:00:00Z' } }, date)).toBeNull()
  })
})
