// Share links as the pages word them (backend/app/share_links.py): how long a new link may run, a link's line in a
// document's list and the organisation's, who may revoke it, the token its address carries, and what its visitor's
// page says of who shared the document and of a view that ended.

import { DEFAULT_SHARE_DAYS, SHARE_DAYS, isWithin, mayChange } from './api.ts'
import type { Me, ShareDays, ShareLink, SharedDocument } from './api.ts'
import { formatDate } from './format.ts'

/** The query of a document's links, and of the organisation's. */
export const shareLinksKey = (documentId: number) => ['share-links', documentId] as const
export const ORGANIZATION_LINKS_KEY = ['organization', 'share-links'] as const

/** The days a new link may run, as the form offers them, the default first. */
export const SHARE_DAY_CHOICES: { value: ShareDays; label: string }[] = [DEFAULT_SHARE_DAYS, ...SHARE_DAYS.filter((days) => days !== DEFAULT_SHARE_DAYS)].map(
  (days) => ({ value: days, label: daysWords(days) }),
)

export function daysWords(days: number): string {
  return `${days} day${days === 1 ? '' : 's'}`
}

/** Whether the page offers the member a document's links: while they are rolled out, to a role that may make one. */
export const offersLinks = (me: Me) => me.organization.flags_on.includes('share_links') && mayChange(me.role)

/**
 * Why a member cannot make a link in the organisation as it stands, or null when they can: the plan's refusal is
 * the plan's offer's (`PlanOffer`), so it says only that the plan lacks them.
 */
export function linksHeld(me: Me): 'plan' | 'off' | null {
  if (!me.organization.allowance.share_links) return 'plan'
  if (!me.organization.share_links) return 'off'
  return null
}

export const LINKS_OFF =
  "The organisation's owners and admins turned share links off, so every link it made has stopped. One of them turns them on again on the organisation page."

/** A link's line in a list: who made it, until when it runs, its password, and how often it was opened. */
export function describeLink(link: ShareLink): string {
  const parts = [`made by ${link.made_by}`, `runs until ${formatDate(link.expires_at)}`]
  if (link.password) parts.push('with a password')
  parts.push(
    link.views === 0
      ? 'not opened yet'
      : `opened ${link.views === 1 ? 'once' : `${link.views} times`}${link.last_viewed_at ? `, last ${formatDate(link.last_viewed_at)}` : ''}`,
  )
  return capital(parts.join(' · '))
}

/** Whether the member may revoke the link: its maker, and the owners and admins, any. */
export const mayRevoke = (link: ShareLink, me: Me) => link.user_id === me.id || isWithin('admin', me.role)

/** The token a link's address carries in its fragment (`#token=…`), which the browser never sends; '' for none. */
export function tokenOf(hash: string): string {
  return new URLSearchParams(hash.replace(/^#/, '')).get('token') ?? ''
}

/** Who shared the document with the visitor, and until when the link runs. */
export function sharedWords(shared: SharedDocument): string {
  return `Shared by ${shared.shared_by} of ${shared.organization}, until ${formatDate(shared.expires_at)}.`
}

/** The milliseconds until the visitor's view of the link ends, after which its pages ask for it to be opened again. */
export function viewLeft(shared: SharedDocument, now = Date.now()): number {
  return Math.max(0, new Date(shared.view_ends_at).getTime() - now)
}

const capital = (text: string) => text.charAt(0).toUpperCase() + text.slice(1)
