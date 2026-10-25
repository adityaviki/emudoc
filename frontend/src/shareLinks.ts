// Share links as the pages word them (backend/app/share_links.py): what a link is made for (a document, or an answer
// of a chat), how long a new link may run, a link's line in a list and the organisation's, who may revoke it, the
// token its address carries, and what its visitor's page says of who shared it and of a view that ended.

import { DEFAULT_SHARE_DAYS, SHARE_DAYS, isWithin, mayChange } from './api.ts'
import type { Me, OrganizationShareLink, ShareDays, ShareLink, Shared, SharedDocument, SharedKind } from './api.ts'
import { formatDate } from './format.ts'

/** What a link is made for: a document by its id and name, or an answer by its chat's id and its own. */
export type ShareTarget = { kind: 'document'; id: number; name: string } | { kind: 'answer'; chatId: number; id: number }

/** The query of a document's or an answer's links, and of the organisation's. */
export const shareLinksKey = (kind: SharedKind, id: number) => ['share-links', kind, id] as const
export const ORGANIZATION_LINKS_KEY = ['organization', 'share-links'] as const

/** The query of the links of what a link shows. */
export const linkKey = (link: ShareLink) => shareLinksKey(link.kind, (link.document_id ?? link.message_id)!)

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

/** What a link of the organisation's shows, as its list names it: a document by its name, an answer by none. */
export function linkAbout(link: OrganizationShareLink): string {
  return `${link.filename ?? 'An answer of a chat'} in ${link.project_name}`
}

/** Who shared the document or the answer with the visitor, and until when the link runs. */
export function sharedWords(shared: Shared): string {
  return `Shared by ${shared.shared_by} of ${shared.organization}, until ${formatDate(shared.expires_at)}.`
}

/** The milliseconds until the visitor's view of the link ends, after which its pages ask for it to be opened again. */
export function viewLeft(shared: SharedDocument, now = Date.now()): number {
  return Math.max(0, new Date(shared.view_ends_at).getTime() - now)
}

const capital = (text: string) => text.charAt(0).toUpperCase() + text.slice(1)
