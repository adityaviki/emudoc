// The bell in the header: how many of the user's notices are unread, asked every minute, and the
// notification centre it opens, their notices about every organisation they belong to, the
// newest first. Opening one marks it read and goes to its page, in its organisation: one the
// session does not work in is switched to first, which loads the page again. While the browser
// is told to show the notices as they come (the account page), one that arrives while the tab is
// hidden is shown as the browser's own notification too.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Bell, Settings } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router'
import { notifications } from '../api.ts'
import type { NotificationItem, NotificationPage } from '../api.ts'
import { openInOrganization, useMe } from '../auth.ts'
import { Floating } from '../components/Menu.tsx'
import { IconButton, Spinner } from '../components/ui.tsx'
import { NOTIFICATIONS_KEY, ago, arrived, browserNoticesOn, markRead, pageRead, unreadLabel } from '../notifications.ts'

export function NotificationBell() {
  const me = useMe()
  const [anchor, setAnchor] = useState<DOMRect | null>(null)
  const button = useRef<HTMLButtonElement>(null)
  const newest = useQuery({
    queryKey: NOTIFICATIONS_KEY,
    queryFn: () => notifications.page(),
    staleTime: 60_000,
    refetchInterval: 60_000,
  })
  useShownAsTheyCome(me.id, newest.data)
  const unread = newest.data?.unread ?? 0

  return (
    <>
      <button
        ref={button}
        type="button"
        aria-haspopup="dialog"
        aria-expanded={anchor !== null}
        aria-label={unread ? `Notifications, ${unread} unread` : 'Notifications'}
        title="Notifications"
        onClick={(event) => setAnchor(anchor ? null : event.currentTarget.getBoundingClientRect())}
        className="relative inline-flex size-7 shrink-0 items-center justify-center rounded-md text-muted transition hover:bg-sunken hover:text-ink"
      >
        <Bell className="size-4" />
        {unread > 0 && (
          <span className="absolute -top-0.5 -right-0.5 flex h-3.5 min-w-3.5 items-center justify-center rounded-full bg-accent px-1 text-[9px] leading-none font-semibold text-accent-ink">
            {unreadLabel(unread)}
          </span>
        )}
      </button>
      {anchor && newest.data && (
        <Floating anchor={anchor} alignEnd onClose={() => setAnchor(null)} ignore={button} className="w-96 max-w-[calc(100vw-16px)]">
          <Centre page={newest.data} onClose={() => setAnchor(null)} />
        </Floating>
      )}
    </>
  )
}

/** The list the bell opens: the newest page, which the bell keeps asking for, and older ones asked for while it is open. */
function Centre({ page, onClose }: { page: NotificationPage; onClose: () => void }) {
  const me = useMe()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [older, setOlder] = useState<NotificationItem[]>([])
  // The next page's `before`: the newest page's until an older one is shown.
  const [next, setNext] = useState<number | null | undefined>(undefined)
  const more = useMutation({
    mutationFn: notifications.page,
    onSuccess: (found) => {
      setOlder((shown) => [...shown, ...found.notifications])
      setNext(found.next_before)
    },
  })
  const read = useMutation({
    mutationFn: notifications.read,
    onMutate: (ids) => {
      const now = new Date().toISOString()
      queryClient.setQueryData<NotificationPage>(NOTIFICATIONS_KEY, (newest) => newest && pageRead(newest, ids, now))
      setOlder((shown) => markRead(shown, ids, now))
    },
    onSettled: () => void queryClient.invalidateQueries({ queryKey: NOTIFICATIONS_KEY }),
  })
  const items = [...page.notifications, ...older]
  const before = next === undefined ? page.next_before : next
  // As a menu does: the keyboard starts on the newest notice, or the panel's first button.
  const panel = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const first = panel.current?.querySelector<HTMLButtonElement>('li button') ?? panel.current?.querySelector('button')
    first?.focus()
  }, [])
  // The organisation is named when the user belongs to more than one.
  const several = me.organizations.length > 1

  function open(item: NotificationItem) {
    onClose()
    const marked = item.read_at ? Promise.resolve() : read.mutateAsync([item.id]).catch(() => {})
    if (item.org_id === me.organization.id) navigate(item.link)
    else void marked.then(() => openInOrganization(item.org_id, item.link))
  }

  return (
    <div ref={panel} role="dialog" aria-label="Notifications">
      <div className="flex items-center justify-between gap-2 border-b border-line py-1.5 pr-1.5 pl-3">
        <p className="text-[13px] font-medium">Notifications</p>
        <div className="flex items-center gap-1">
          {page.unread > 0 && (
            <button type="button" onClick={() => read.mutate(undefined)} className="rounded-md px-2 py-1 text-xs font-medium text-accent hover:bg-sunken">
              Mark all as read
            </button>
          )}
          <IconButton
            label="Choose which notices you get"
            onClick={() => {
              onClose()
              navigate('/account#notices')
            }}
          >
            <Settings className="size-3.5" />
          </IconButton>
        </div>
      </div>
      {items.length === 0 ? (
        <p className="px-4 py-6 text-center text-[13px] leading-relaxed text-muted">
          No notices yet. Emudoc tells you here when a long upload is indexed, and owners and admins when the month's pages or questions run low.
        </p>
      ) : (
        <ul className="max-h-[min(28rem,70vh)] divide-y divide-line overflow-y-auto">
          {items.map((item) => (
            <li key={item.id}>
              <button type="button" onClick={() => open(item)} className="block w-full px-3 py-2.5 text-left transition hover:bg-sunken">
                <span className="flex items-start gap-2">
                  <span className={`mt-1.5 size-1.5 shrink-0 rounded-full ${item.read_at ? '' : 'bg-accent'}`} />
                  <span className="min-w-0">
                    <span className={`block text-[13px] leading-snug ${item.read_at ? '' : 'font-medium'}`}>{item.title}</span>
                    <span className="mt-0.5 line-clamp-3 block text-xs leading-relaxed whitespace-pre-line text-muted">{item.body}</span>
                    <span className="mt-1 block text-[11px] text-muted">
                      {several && `${item.organization} · `}
                      {ago(item.created_at)}
                    </span>
                  </span>
                </span>
              </button>
            </li>
          ))}
          {before !== null && (
            <li className="flex justify-center py-1.5">
              <button
                type="button"
                onClick={() => more.mutate(before)}
                disabled={more.isPending}
                className="flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium text-accent hover:bg-sunken disabled:opacity-50"
              >
                {more.isPending && <Spinner className="size-3" />}
                Show older
              </button>
            </li>
          )}
        </ul>
      )}
    </div>
  )
}

/**
 * Shows the notices that arrive while the tab is hidden as the browser's own notifications, when
 * this browser was told to and allows it. Those there when the page loaded are not new; the newest
 * seen is kept across the bell's asking.
 */
function useShownAsTheyCome(userId: number, page: NotificationPage | undefined) {
  const seen = useRef<number | null>(null)
  useEffect(() => {
    if (!page) return
    if (seen.current !== null && document.visibilityState === 'hidden' && browserNoticesOn(userId)) {
      for (const item of arrived(page.notifications, seen.current)) show(item)
    }
    seen.current = Math.max(seen.current ?? 0, page.notifications[0]?.id ?? 0)
  }, [page, userId])
}

function show(item: NotificationItem) {
  try {
    // Tagged by the notice, so that two tabs that both find it show it once.
    const shown = new window.Notification(item.title, { body: item.body, tag: `emudoc-${item.id}`, icon: '/favicon.svg' })
    shown.onclick = () => {
      window.focus()
      shown.close()
    }
  } catch {
    // Chrome on Android makes them only through a service worker, which the app has none of.
  }
}
