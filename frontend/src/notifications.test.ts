import { describe, expect, it } from 'vitest'
import type { NotificationItem, NotificationPage } from './api.ts'
import { ago, arrived, linkedOrganization, pageRead, unreadLabel } from './notifications.ts'

const item = (id: number, read_at: string | null = null): NotificationItem => ({
  id,
  kind: 'quota',
  title: `Notice ${id}`,
  body: 'Globex has used 80 of the 100 questions it may ask on Emudoc in October, 80 %.',
  link: '/organization/usage',
  org_id: 3,
  organization: 'Globex',
  created_at: '2026-10-20T10:00:00Z',
  read_at,
})

describe('the bell', () => {
  it('counts up to nine, then says more', () => {
    expect(unreadLabel(1)).toBe('1')
    expect(unreadLabel(9)).toBe('9')
    expect(unreadLabel(10)).toBe('9+')
  })

  it('says how long ago a notice came, in minutes and hours, then by its date', () => {
    const now = new Date('2026-10-20T12:00:00Z')
    expect(ago('2026-10-20T11:59:40Z', now)).toBe('just now')
    expect(ago('2026-10-20T11:55:00Z', now)).toBe('5 min ago')
    expect(ago('2026-10-20T09:00:00Z', now)).toBe('3 h ago')
    expect(ago('2026-10-18T12:00:00Z', now)).toBe(new Date('2026-10-18T12:00:00Z').toLocaleDateString(undefined, { month: 'short', day: 'numeric' }))
  })
})

describe('marking read', () => {
  const page: NotificationPage = { notifications: [item(3), item(2, '2026-10-20T11:00:00Z'), item(1)], unread: 5, next_before: 1 }

  it('marks the notices named and takes off the count only those that were unread', () => {
    const read = pageRead(page, [3, 2], 'now')
    expect(read.notifications.map((notice) => notice.read_at)).toEqual(['now', '2026-10-20T11:00:00Z', null])
    // Of the five unread, more of them on older pages, one was marked here.
    expect(read.unread).toBe(4)
  })

  it('marks every notice when none is named', () => {
    const read = pageRead(page, undefined, 'now')
    expect(read.notifications.every((notice) => notice.read_at)).toBe(true)
    expect(read.unread).toBe(0)
  })
})

describe('the notices that arrived', () => {
  it('are the unread ones newer than the newest seen, oldest first', () => {
    expect(arrived([item(5), item(4, 'read elsewhere'), item(3), item(2)], 2).map((notice) => notice.id)).toEqual([3, 5])
    expect(arrived([item(2)], 2)).toEqual([])
  })
})

describe('the organisation a link names', () => {
  it('is read from the query, and the link kept without it', () => {
    expect(linkedOrganization('/organization/usage', '?organization=3', '')).toEqual({ id: 3, rest: '/organization/usage' })
    expect(linkedOrganization('/organization', '?billing=back&organization=7', '#plan')).toEqual({
      id: 7,
      rest: '/organization?billing=back#plan',
    })
  })

  it('is none when the link names none', () => {
    expect(linkedOrganization('/projects/12', '?chat=4', '')).toBeNull()
  })
})
