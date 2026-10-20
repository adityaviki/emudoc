// The notification centre's words and arithmetic: the kinds of notice as the account page names
// them, the bell's count, how long ago a notice came, marking notices read in what the page
// holds before the server answers, which notices arrived since the bell last looked, for the
// browser's own notifications, and the organisation a link names, which the page switches to.

import type { NotificationItem, NotificationPage, NoticeKind } from './api.ts'

/** The kinds of notice, in the server's order, as the account page offers them. */
export const NOTICE_KINDS: Record<NoticeKind, { label: string; detail: string }> = {
  upload_indexed: {
    label: 'A long upload is indexed',
    detail: 'When documents you uploaded took five minutes or more, that they are done and which could not be indexed.',
  },
  quota: {
    label: "The month's pages or questions run low",
    detail: "To an organisation's owners and admins, at 80 % and all of what it may use.",
  },
}

/** Where the notices are kept in the cache: the newest page, which the bell asks every minute. */
export const NOTIFICATIONS_KEY = ['notifications'] as const

/** The bell's count, short enough for its badge. */
export function unreadLabel(count: number): string {
  return count > 9 ? '9+' : String(count)
}

/** How long ago a notice came, as the centre says it: "just now", "5 min ago", "3 h ago", then the date. */
export function ago(value: string, now: Date = new Date()): string {
  const minutes = Math.floor((now.getTime() - new Date(value).getTime()) / 60_000)
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes} min ago`
  if (minutes < 24 * 60) return `${Math.floor(minutes / 60)} h ago`
  return new Date(value).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

/** The notices marked read at `now`: those named, or every one. */
export function markRead(items: NotificationItem[], ids: number[] | undefined, now: string): NotificationItem[] {
  return items.map((item) => (item.read_at || (ids && !ids.includes(item.id)) ? item : { ...item, read_at: now }))
}

/** The newest page with the notices named marked read, and the count less those that were unread. */
export function pageRead(page: NotificationPage, ids: number[] | undefined, now: string): NotificationPage {
  if (!ids) return { ...page, notifications: markRead(page.notifications, ids, now), unread: 0 }
  const unread = page.notifications.filter((item) => !item.read_at && ids.includes(item.id)).length
  return { ...page, notifications: markRead(page.notifications, ids, now), unread: Math.max(0, page.unread - unread) }
}

/** The unread notices newer than the newest the bell had seen, oldest first: those the browser shows as they come. */
export function arrived(items: NotificationItem[], seen: number): NotificationItem[] {
  return items.filter((item) => item.id > seen && !item.read_at).reverse()
}

/** The query a link names its organisation with, as the server writes it (`in_organization` in backend/app/mail.py). */
export const ORGANIZATION_QUERY = 'organization'

/**
 * The organisation a link to a page names, and the link without it; null when it names none. A
 * notice's mail names it, since its reader may work in another organisation when they open it.
 */
export function linkedOrganization(path: string, search: string, hash: string): { id: number; rest: string } | null {
  const params = new URLSearchParams(search)
  const named = params.get(ORGANIZATION_QUERY)
  if (named === null) return null
  params.delete(ORGANIZATION_QUERY)
  const query = params.toString()
  return { id: Number(named), rest: `${path}${query ? `?${query}` : ''}${hash}` }
}

/** Where this browser keeps whether it shows the user's notices as they come: a choice of the browser's, not the account's. */
export const browserNoticesKey = (userId: number) => `browser-notices:${userId}`

/** Whether this browser shows the user's notices as they come: chosen here, and allowed by the browser. */
export function browserNoticesOn(userId: number): boolean {
  try {
    return (
      localStorage.getItem(browserNoticesKey(userId)) === 'true' &&
      'Notification' in window &&
      window.Notification.permission === 'granted'
    )
  } catch {
    return false
  }
}
