// What the organisation page says of a subscription and offers an owner, from what Stripe last
// said of it (`billing` on the organisation, backend/app/billing.py), and what a refusal by the
// plan offers in its place: the server holds the plan to the subscription, and these only word it.

import { PLANS, PLAN_LABELS } from './api.ts'
import type { Billing, Organization, Pack, PlanLimit, Role } from './api.ts'

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

/** The month a pack bought now counts in, the calendar month in UTC, as the server counts it. */
export function thisMonth(now = new Date()): string {
  return now.toLocaleString(undefined, { month: 'long', timeZone: 'UTC' })
}

/** What a refusal by the plan offers beneath its sentence: the way past the limit for whoever reads it. */
export interface Offer {
  text: string
  /** The packs an owner may buy here and now, of the unit refused. */
  packs: Pack[]
  /** Whether to show the plans on the organisation page, where an owner buys one. */
  plans: boolean
  /** Whether to offer Manage billing, where the subscription's plan and seats change. */
  manage: boolean
}

// The limits a pack adds to for the month (PACK_LIMITS in backend/app/billing.py).
const PACKED: Partial<Record<PlanLimit, Pack['unit']>> = { pages_per_month: 'pages', questions_per_month: 'questions' }

/**
 * What to offer past `limit`, the figure a refusal names, to `role` in `organization`: a pack
 * for the month, a larger plan, more seats, or whom to ask; the server's sentence has said what
 * the limit is.
 */
export function offerFor(limit: PlanLimit | null, organization: Organization, role: Role): Offer {
  const { billing, plan } = organization
  const on = `Your organisation is on the ${PLAN_LABELS[plan]} plan.`
  const nothing = { packs: [], plans: false, manage: false }
  if (!billing.managed) {
    return { ...nothing, text: `${on} Its terms were agreed with the Emudoc team: ask them for a higher limit.` }
  }
  if (billing.sold.length === 0 && billing.packs.length === 0) {
    return { ...nothing, text: `${on} Contact the Emudoc team to change it.` }
  }
  const unit = limit === null ? undefined : PACKED[limit]
  const packs = billing.packs.filter((pack) => pack.unit === unit)
  const pack = packs.length > 0 ? `buy more ${unit} for ${thisMonth()}` : null
  const subscribed = billed(billing)
  // More seats on a plan sold by the seat, else a larger plan on sale, else nothing billing sells.
  const way =
    limit === 'members' && organization.seats !== null
      ? 'add seats'
      : billing.sold.some((sold) => PLANS.indexOf(sold) > PLANS.indexOf(plan))
        ? subscribed
          ? 'change the plan'
          : 'upgrade it'
        : null

  if (role !== 'owner') {
    const asked = [pack, way].filter((option) => option !== null)
    return { ...nothing, text: `${on} ${asked.length ? `Ask an owner to ${asked.join(', or ')}.` : 'Ask an owner about a higher limit.'}` }
  }
  const where = way && (subscribed ? `${way} under Manage billing` : `${way} on the organisation page`)
  const options = [pack, where ?? (pack ? 'ask the Emudoc team for a higher limit' : null)].filter((option) => option !== null)
  return {
    text: `${on} ${options.length ? capital(options.join(', or ')) : 'Ask the Emudoc team for a higher limit'}.`,
    packs,
    plans: way !== null && !subscribed && mayBuy(billing),
    manage: way !== null && subscribed && billing.customer,
  }
}

const capital = (text: string) => text.charAt(0).toUpperCase() + text.slice(1)

/**
 * What the pages say while the organisation holds more than its plan allows, once its plan,
 * seats or terms were lowered under it; null while it is within them.
 */
export function describeExcess(organization: Organization): string | null {
  const over = organization.allowance.over
  if (over.length === 0) return null
  const words = over.map((excess) => excess.words)
  const held = words.length > 1 ? `${words.slice(0, -1).join(', ')} and ${words.at(-1)}` : words[0]
  return `${organization.name} holds more than the ${PLAN_LABELS[organization.plan]} plan allows: ${held}. Until it is within it, nothing can be added or changed, though everything it holds can still be read, asked about and deleted.`
}
