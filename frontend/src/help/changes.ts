// What's new (src/help/WhatsNew.tsx): the words of a day of the changelog, and which entries
// the member has not read. The server counts those (`changes_unread`, backend/app/changelog.py),
// the newest entries, so the page marks that many from the top, across the pages it loaded.

import type { ChangesDay } from '../api.ts'

/** The day an entry was made on, or the days told as one: "21 October 2026", "5 – 10 October 2026". */
export function dayWords(day: Pick<ChangesDay, 'first' | 'last'>, locale?: string): string {
  // The changelog's days are dates, not moments: read and written in UTC, nobody's zone moves them.
  const format = new Intl.DateTimeFormat(locale, { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' })
  const first = new Date(`${day.first}T00:00:00Z`)
  if (day.first === day.last) return format.format(first)
  return format.formatRange(first, new Date(`${day.last}T00:00:00Z`))
}

/** How many of each day's entries, from its first, the member has not read, of `unread` newest ones. */
export function unreadPerDay(days: Pick<ChangesDay, 'entries'>[], unread: number): number[] {
  let left = unread
  return days.map((day) => {
    const marked = Math.min(left, day.entries.length)
    left -= marked
    return marked
  })
}

/** What the help menu says of what's new while some of it is unread. */
export function unreadWords(unread: number): string {
  return unread === 1 ? '1 new' : `${unread} new`
}
