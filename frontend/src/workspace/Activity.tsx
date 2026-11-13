import { useInfiniteQuery } from '@tanstack/react-query'
import { Activity, ArrowLeft } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { api } from '../api.ts'
import type { ActivityEvent } from '../api.ts'
import { activityKey, activityWords, grouped, uploadsWords } from '../comments.ts'
import { Button, EmptyState, IconButton, Loading, Notice } from '../components/ui.tsx'
import { formatDateTime } from '../format.ts'
import { useWorkspace } from './state.ts'

// How many documents of a run of uploads are named until the member asks for all of them.
const NAMED = 5

/**
 * What was done in the project lately, in the library's place, newest first: documents arriving, given versions,
 * deleted and restored, reviews, comments, runs of the cross-checks and the question sets, chats shared;
 * a run of one member's uploads as one item. Each leads to what it was about while that is there to open.
 */
export function ActivityView() {
  const workspace = useWorkspace()
  const { project } = workspace
  const heading = useRef<HTMLHeadingElement>(null)
  // Opened from a button that went with the view before it: the keyboard goes on from here.
  useEffect(() => {
    if (window.document.activeElement === window.document.body) heading.current?.focus()
  }, [])
  const activity = useInfiniteQuery({
    queryKey: activityKey(project.id),
    queryFn: ({ pageParam }) => api.activity(project.id, pageParam),
    initialPageParam: null as number | null,
    getNextPageParam: (page) => page.next_before,
  })
  const events = activity.data?.pages.flatMap((page) => page.events) ?? []
  const items = grouped(events)

  return (
    <div className="flex h-full min-h-0 flex-col bg-page">
      <div className="flex h-11 shrink-0 items-center gap-1.5 border-b border-line bg-surface px-2">
        <IconButton label="Back to the library" onClick={() => workspace.goToFolder(null)}>
          <ArrowLeft className="size-4" />
        </IconButton>
        <h2 ref={heading} tabIndex={-1} className="flex-1 truncate text-[13px] font-semibold">
          Activity
        </h2>
      </div>
      <div className="min-h-0 flex-1 space-y-2 overflow-y-auto p-3">
        <p className="text-xs text-muted">
          What was done in the project lately, kept as long as the workspace’s audit log: documents arriving and
          leaving, reviews, comments and runs.
        </p>
        {activity.isPending && <Loading />}
        {activity.isError && <Notice tone="bad">Could not load the activity: {activity.error.message}</Notice>}
        {activity.isSuccess && events.length === 0 && (
          <EmptyState icon={<Activity />} title="Nothing done yet">
            Uploads, reviews, comments and runs in this project show here as they happen.
          </EmptyState>
        )}
        {items.length > 0 && (
          <ol aria-label="Activity, newest first" className="divide-y divide-line rounded-lg border border-line bg-surface">
            {items.map((item) =>
              'uploads' in item ? (
                <Uploads key={item.uploads[0].id} uploads={item.uploads} />
              ) : (
                <li key={item.event.id} className="px-3 py-2">
                  <Line event={item.event} />
                </li>
              ),
            )}
          </ol>
        )}
        {activity.hasNextPage && (
          <Button disabled={activity.isFetchingNextPage} onClick={() => void activity.fetchNextPage()}>
            {activity.isFetchingNextPage ? 'Loading…' : 'Show more'}
          </Button>
        )}
      </div>
    </div>
  )
}

function Line({ event }: { event: ActivityEvent }) {
  const words = activityWords(event)
  return (
    <>
      <p className="text-[13px] leading-snug break-words">
        {words.who && <span className="font-medium">{words.who}</span>}
        {words.before}
        {words.target != null && <Target event={event} label={words.target} />}
        {words.after}
      </p>
      <time dateTime={event.occurred_at} className="text-[11px] text-muted">
        {formatDateTime(event.occurred_at)}
      </time>
    </>
  )
}

/** What an item was about, as a link while it is there to open. */
function Target({ event, label }: { event: ActivityEvent; label: string }) {
  const workspace = useWorkspace()
  const open = opener(event, workspace)
  if (!open) return <span className="font-medium">{label}</span>
  return (
    <button type="button" onClick={open} className="rounded text-left font-medium text-accent hover:underline">
      {label}
    </button>
  )
}

function opener(event: ActivityEvent, workspace: ReturnType<typeof useWorkspace>): (() => void) | null {
  const target = event.target_id
  if (event.document_id != null) {
    const document = event.document_id
    return () => workspace.openDocument(document, event.thread_id != null ? { thread: event.thread_id } : null)
  }
  if (target == null) return null
  if (event.action === 'cross_checks_run') return () => workspace.showRun(target)
  if (event.action === 'question_set_run') return () => workspace.showReport(target)
  if (event.action === 'chat_shared') return () => workspace.openChat(target)
  return null
}

function Uploads({ uploads }: { uploads: ActivityEvent[] }) {
  const [all, setAll] = useState(false)
  const shown = all ? uploads : uploads.slice(0, NAMED)
  const newest = uploads[0]
  return (
    <li className="px-3 py-2">
      <p className="text-[13px] leading-snug">{uploadsWords(uploads)}</p>
      <time dateTime={newest.occurred_at} className="text-[11px] text-muted">
        {formatDateTime(newest.occurred_at)}
      </time>
      <ul className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-xs">
        {shown.map((event) => (
          <li key={event.id}>
            <Target event={event} label={event.filename ?? 'a document'} />
          </li>
        ))}
      </ul>
      {uploads.length > NAMED && (
        <button type="button" className="mt-1 rounded text-xs text-muted hover:text-ink hover:underline" onClick={() => setAll((shownAll) => !shownAll)}>
          {all ? 'Show fewer' : `Show all ${uploads.length}`}
        </button>
      )}
    </li>
  )
}
