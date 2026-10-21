// A member's leave for Emudoc's support to see their account (backend/app/support.py), and support's view of it, as the
// pages word them: the account page's line and choices, and the banner a view shows under the header.

import type { Me } from './api.ts'
import { formatDateTime } from './format.ts'

/** How long the account page offers to let support in, in days: SUPPORT_DAYS at most. */
export const SUPPORT_SPANS = [
  { days: 1, label: 'for a day' },
  { days: 7, label: 'for a week' },
] as const

/** The account page's line: whether support may see the account in the organisation it works in, and until when. */
export function leaveWords(me: Pick<Me, 'organization' | 'support_until'>): string {
  const where = me.organization.name
  if (!me.support_until) return `Emudoc's support cannot see your account in ${where}.`
  return `Emudoc's support may see your account in ${where} until ${formatDateTime(me.support_until)}, without changing anything.`
}

/** The banner of a view by support: whose account, where, and until when; null for the member's own. */
export function viewWords(me: Pick<Me, 'name' | 'email' | 'organization' | 'support_view'>): string | null {
  if (!me.support_view) return null
  const until = new Date(me.support_view.ends_at).toLocaleTimeString(undefined, { timeStyle: 'short' })
  return `You are viewing ${me.name || me.email}'s account in ${me.organization.name} as Emudoc's support, read only, until ${until}.`
}
