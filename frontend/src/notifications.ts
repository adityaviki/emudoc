// The notification centre's words and arithmetic: the kinds of notice as the account page names
// them, the bell's count, how long ago a notice came, marking notices read in what the page
// holds before the server answers, which notices arrived since the bell last looked, for the
// browser's own notifications.

import type { NotificationItem, NotificationPage, NoticeKind, PastNoticeKind } from './api.ts'

/** The kinds of notice, in the server's order, as the account page offers them. */
export const NOTICE_KINDS: Record<NoticeKind, { label: string; detail: string }> = {
  upload_indexed: {
    label: 'A long upload is indexed',
    detail: 'When documents you uploaded took five minutes or more, that they are done and which could not be indexed.',
  },
  project_export: {
    label: 'A project export is ready',
    detail: 'When the export of a project you asked for is ready to download, or could not be made.',
  },
  checks_run: {
    label: 'An automation ran the cross-checks',
    detail: 'When documents arrived and an automation that tells you ran a project’s cross-checks: what they found about them.',
  },
}

/** The kinds of notice no longer told, as the account page named them, for the audit log's rows of a choice made before they went. */
export const PAST_NOTICE_KINDS: Record<PastNoticeKind, string> = {
  mentioned: 'You are mentioned in a comment',
  thread_replied: 'A reply in a thread you wrote in',
  review_assigned: 'A document is assigned to you',
  report_ready: 'A question set’s report is ready',
  weekly_digest: 'What arrived this week',
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
