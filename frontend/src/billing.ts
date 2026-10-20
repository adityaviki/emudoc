// What the organisation page says of a subscription and offers an owner, from what Stripe last
// said of it (`billing` on the organisation, backend/app/billing.py): the server holds the plan
// to the subscription, and these only word it.

import type { Billing } from './api.ts'

/** The statuses of a subscription that is still billed, as the server's `BILLED`: no second is bought meanwhile. */
const BILLED = new Set(['trialing', 'active', 'past_due', 'incomplete'])

export interface Standing {
  tone: 'good' | 'warn' | 'bad'
  text: string
}

/** Whether the organisation's subscription is still billed. */
export function billed(billing: Billing): boolean {
  return billing.status !== null && BILLED.has(billing.status)
}

/** Whether an owner may buy a plan for the organisation here now. */
export function mayBuy(billing: Billing): boolean {
  return billing.sold.length > 0 && billing.managed && !billed(billing)
}

/**
 * What the page says of the subscription, each date worded by `date`; null when there is
 * nothing to say: none yet, or one that ended, which the plans on offer follow.
 */
export function describeBilling(billing: Billing, date: (value: string) => string, now = new Date()): Standing | null {
  const { status, trial_ends_at: trial, renews_at: renews, ends_at: ends, grace_ends_at: grace } = billing
  switch (status) {
    case 'trialing':
      if (ends) return { tone: 'warn', text: `In its trial until ${date(trial ?? ends)}, and cancelled: the organisation moves to the Free plan then.` }
      return { tone: 'good', text: trial ? `In its trial until ${date(trial)}, when the first payment is taken.` : 'In its trial.' }
    case 'active':
      if (ends) return { tone: 'warn', text: `Cancelled: it ends on ${date(ends)}, and the organisation moves to the Free plan then.` }
      return { tone: 'good', text: renews ? `Paid, and renews on ${date(renews)}.` : 'Paid.' }
    case 'past_due':
      if (grace && new Date(grace) > now) {
        return {
          tone: 'bad',
          text: `A payment failed. Pay, or change the payment method, under Manage billing by ${date(grace)}, or the organisation moves to the Free plan, keeping all it holds.`,
        }
      }
      return { tone: 'bad', text: 'A payment failed, so the organisation is on the Free plan until it is made under Manage billing.' }
    case 'incomplete':
      return { tone: 'warn', text: 'The first payment waits to be confirmed on Stripe’s page.' }
    case 'unpaid':
      return { tone: 'bad', text: 'Left unpaid, so the organisation is on the Free plan. Pay the open invoice under Manage billing to have the plan again.' }
    case 'paused':
      return { tone: 'bad', text: 'Paused, since its trial ended without a payment method: add one under Manage billing to have the plan again.' }
    default:
      return null
  }
}
