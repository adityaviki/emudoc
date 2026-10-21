import { useInfiniteQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft } from 'lucide-react'
import { useContext, useEffect, useId, useState } from 'react'
import { Link } from 'react-router'
import { api, auth } from '../api.ts'
import type { ChangesDay } from '../api.ts'
import { MeContext, setSignedIn } from '../auth.ts'
import { Markdown } from '../components/Markdown.tsx'
import { Button, Loading, Notice } from '../components/ui.tsx'
import { dayWords, unreadPerDay, unreadWords } from './changes.ts'

/**
 * What's new at /whats-new: what changed for members, a week of days at a time, the newest first
 * (the changelog's entries for them, backend/app/changelog.py). Public, like the help pages. A
 * member who opens it has read it, so the help menu's mark goes, while the entries that were new
 * as the page opened stay marked until they leave it; support's view of an account reads it
 * without saying so.
 */
export function WhatsNew() {
  const me = useContext(MeContext)
  const queryClient = useQueryClient()
  const [unread] = useState(me?.changes_unread ?? 0)
  const changes = useInfiniteQuery({
    queryKey: ['changelog'],
    queryFn: ({ pageParam }) => api.changelog(pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_before,
  })
  const shown = changes.isSuccess
  useEffect(() => {
    if (!shown || !me || me.changes_unread === 0 || me.support_view) return
    void auth.readChanges().then(
      (answer) => setSignedIn(queryClient, answer),
      () => {},
    )
  }, [shown, me, queryClient])

  if (changes.isPending) return <Loading />
  if (changes.isError) return <Notice tone="bad">{changes.error.message}</Notice>
  const days = changes.data.pages.flatMap((page) => page.days)
  const marked = unreadPerDay(days, unread)

  return (
    <div className="w-full max-w-2xl">
      <Link to="/help" className="mb-3 inline-flex items-center gap-1 text-[13px] text-muted hover:text-ink">
        <ArrowLeft className="size-3.5" />
        All help
      </Link>
      <h1 className="text-lg font-semibold tracking-tight">What's new</h1>
      <p className="mt-1 text-[13px] text-muted">
        What changed in Emudoc, the newest first.
        {unread > 0 && ` ${unread === 1 ? 'One change is' : `${unread} changes are`} new since you last looked.`}
      </p>
      {days.length ? (
        <div className="mt-4 divide-y divide-line overflow-hidden rounded-xl border border-line bg-surface shadow-xs">
          {days.map((day, index) => (
            <Day key={day.last} day={day} unread={marked[index]} />
          ))}
        </div>
      ) : (
        <p className="mt-6 text-center text-[13px] text-muted">Nothing to tell yet.</p>
      )}
      {changes.hasNextPage && (
        <div className="mt-4 text-center">
          <Button onClick={() => changes.fetchNextPage()} disabled={changes.isFetchingNextPage}>
            Show earlier
          </Button>
        </div>
      )}
    </div>
  )
}

/** A day's entries, the first `unread` of them marked new. */
function Day({ day, unread }: { day: ChangesDay; unread: number }) {
  const heading = useId()
  return (
    <section aria-labelledby={heading} className="px-6 py-5">
      <h2 id={heading} className="text-sm font-semibold">
        {dayWords(day)}
        {unread > 0 && <span className="ml-2 text-xs font-medium text-accent">{unreadWords(unread)}</span>}
      </h2>
      <ul className="mt-3 space-y-3 text-sm">
        {day.entries.map((entry, index) => (
          <li key={index} className={`border-l-2 pl-3 ${index < unread ? 'border-accent' : 'border-line'}`}>
            {index < unread && <span className="sr-only">New: </span>}
            <Markdown text={entry} followLinks />
          </li>
        ))}
      </ul>
    </section>
  )
}
