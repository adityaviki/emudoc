import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { ArrowLeft, BadgeCheck, ClipboardCheck, ClipboardList, Flag, UserRound } from 'lucide-react'
import { createElement, useEffect, useId, useRef, useState } from 'react'
import { api, mayChange } from '../api.ts'
import type { Document, ReviewInput, ReviewState } from '../api.ts'
import { useMe } from '../auth.ts'
import { Button, EmptyState, IconButton, Loading, Notice, Segmented } from '../components/ui.tsx'
import {
  REVIEW_LABELS,
  REVIEW_STATES,
  STATE_CHOICES,
  changedWords,
  countWords,
  emptyWords,
  reviewWords,
  reviewersKey,
  reviewsKey,
} from '../reviews.ts'
import { fileKind } from './files.ts'
import { useReviewChange } from './mutations.ts'
import { WriteDisputeLetter } from './MyFiles.tsx'
import { useWorkspace } from './state.ts'
import type { Reviews } from './url.ts'

// Each state's mark, apart from the status icon's circles beside it.
const MARKS = {
  to_review: { icon: ClipboardList, tone: 'text-warn' },
  approved: { icon: BadgeCheck, tone: 'text-good' },
  disputed: { icon: Flag, tone: 'text-bad' },
} as const

/** A document's review as a mark, named by its words, for a row that has no room for them; nothing for none. */
export function ReviewMark({
  document,
  id,
  className = 'size-3.5',
}: {
  document: Pick<Document, 'review' | 'assignee_id' | 'assignee'>
  id?: string
  className?: string
}) {
  const me = useMe()
  if (!document.review) return null
  const title = reviewWords(document, me.id)
  const mark = MARKS[document.review]
  return (
    <span id={id} role="img" aria-label={title} title={title} className="inline-flex shrink-0">
      {createElement(mark.icon, { className: `${mark.tone} ${className}` })}
    </span>
  )
}

/**
 * Where a document's review stands and, for a role that may change it, its state and its assignee to choose, each
 * saved as it is chosen, and once disputed, the way to the letter that disputes it. `onChanged`
 * follows a change the server kept, for a list the document may have left.
 */
export function ReviewControls({ document, onChanged }: { document: Document; onChanged?: (document: Document) => void }) {
  const me = useMe()
  const { project } = useWorkspace()
  const changes = mayChange(me.role)
  const id = useId()
  const change = useReviewChange(project.id)
  const reviewers = useQuery({
    queryKey: reviewersKey(project.id),
    queryFn: () => api.reviewers(project.id),
    enabled: changes,
    staleTime: 60_000,
  })
  const changed = changedWords(document)
  if (!changes) {
    return document.review ? (
      <p className="text-xs text-muted">
        {reviewWords(document, me.id)}
        {changed && ` · ${changed}`}
      </p>
    ) : null
  }

  // While a change is under way, the selects show what was chosen.
  const asked: ReviewInput = change.isPending ? change.variables.change : {}
  const state = asked.state !== undefined ? asked.state : document.review
  const assignee = asked.assignee_id !== undefined ? asked.assignee_id : document.assignee_id
  const save = (input: ReviewInput) =>
    change.mutate({ id: document.id, change: input }, { onSuccess: (kept) => onChanged?.(kept) })
  // One assigned who may no longer review it stays a choice while they are assigned.
  const choices = reviewers.data ?? []
  const kept = document.assignee_id != null && !choices.some((reviewer) => reviewer.id === document.assignee_id)
  const select = 'field h-6.5 py-0 pr-7 pl-2 text-xs'
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-xs">
      <span className="flex items-center gap-1.5">
        <label htmlFor={`${id}-state`} className="text-muted">
          Review
        </label>
        <select
          id={`${id}-state`}
          className={select}
          value={state ?? ''}
          aria-busy={change.isPending}
          onChange={(event) => save({ state: (event.target.value || null) as ReviewState | null })}
        >
          {STATE_CHOICES.map((choice) => (
            <option key={choice.value} value={choice.value}>
              {choice.label}
            </option>
          ))}
        </select>
      </span>
      <span className="flex items-center gap-1.5">
        <label htmlFor={`${id}-assignee`} className="text-muted">
          Assigned to
        </label>
        <select
          id={`${id}-assignee`}
          className={select}
          value={assignee ?? ''}
          aria-busy={change.isPending}
          onChange={(event) => save({ assignee_id: event.target.value ? Number(event.target.value) : null })}
        >
          <option value="">Nobody</option>
          {kept && <option value={document.assignee_id!}>{document.assignee ?? 'A former member'}</option>}
          {choices.map((reviewer) => (
            <option key={reviewer.id} value={reviewer.id}>
              {reviewer.id === me.id ? `${reviewer.name} (you)` : reviewer.name}
            </option>
          ))}
        </select>
      </span>
      {changed && <span className="text-muted">{changed}</span>}
      {document.review === 'disputed' && !change.isPending && <WriteDisputeLetter document={document} />}
      {change.isError && (
        <span role="alert" className="text-bad">
          {change.error.message}
        </span>
      )}
    </div>
  )
}

/**
 * The project's documents in review, in the library's place: those of one state at a time, an open review oldest
 * first and an approved one newest first, every member's or those assigned to the member, each opening its document
 * and moved where it is listed by a role that may.
 */
export function ReviewsView({ whose }: { whose: Reviews }) {
  const workspace = useWorkspace()
  const { project } = workspace
  const me = useMe()
  const heading = useRef<HTMLHeadingElement>(null)
  const [state, setState] = useState<ReviewState>('to_review')
  const mine = whose === 'mine'
  // Opened from a button that went with the view before it: the keyboard goes on from here.
  useEffect(() => {
    if (window.document.activeElement === window.document.body) heading.current?.focus()
  }, [])
  const reviews = useInfiniteQuery({
    queryKey: [...reviewsKey(project.id), state, mine],
    queryFn: ({ pageParam }) => api.reviews(project.id, state, mine, pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_after,
  })
  const first = reviews.data?.pages[0]
  const documents = reviews.data?.pages.flatMap((page) => page.documents) ?? []
  const empty = emptyWords(state, mine)
  // A change that takes a document out of the list it was moved in: the keyboard goes on from the heading.
  const left = (document: Document) => {
    if (document.review !== state || (mine && document.assignee_id !== me.id)) heading.current?.focus()
  }

  return (
    <div className="flex h-full min-h-0 flex-col bg-page">
      <div className="flex h-11 shrink-0 items-center gap-1.5 border-b border-line bg-surface px-2">
        <IconButton label="Back to the library" onClick={() => workspace.goToFolder(null)}>
          <ArrowLeft className="size-4" />
        </IconButton>
        <h2 ref={heading} tabIndex={-1} className="flex-1 truncate text-[13px] font-semibold">
          Reviews
        </h2>
      </div>
      <div className="min-h-0 flex-1 space-y-2 overflow-y-auto p-3">
        <p className="text-xs text-muted">
          Documents a member put in review, to approve or dispute, and whom each is assigned to.
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <Segmented
            value={state}
            onChange={setState}
            options={REVIEW_STATES.map((value) => ({
              value,
              label: countWords(value, first?.counts[value]),
              icon: MARKS[value].icon,
            }))}
          />
          <Button
            icon={<UserRound className="size-4" />}
            aria-pressed={mine}
            onClick={() => workspace.showReviews(mine ? 'all' : 'mine')}
          >
            Assigned to me
          </Button>
        </div>
        {reviews.isPending && <Loading />}
        {reviews.isError && <Notice tone="bad">Could not load the reviews: {reviews.error.message}</Notice>}
        {first && documents.length === 0 && (
          <EmptyState icon={<ClipboardCheck />} title={empty.title}>
            {empty.body}
          </EmptyState>
        )}
        {documents.length > 0 && (
          <ul aria-label={`${REVIEW_LABELS[state]}${mine ? ', assigned to you' : ''}`} className="space-y-2">
            {documents.map((document) => (
              <ReviewItem key={document.id} document={document} onChanged={left} />
            ))}
          </ul>
        )}
        {reviews.hasNextPage && (
          <Button disabled={reviews.isFetchingNextPage} onClick={() => void reviews.fetchNextPage()}>
            {reviews.isFetchingNextPage ? 'Loading…' : 'Show more'}
          </Button>
        )}
      </div>
    </div>
  )
}

function ReviewItem({ document, onChanged }: { document: Document; onChanged: (document: Document) => void }) {
  const workspace = useWorkspace()
  const kind = fileKind(document)
  const name = document.title || document.filename
  return (
    <li className="rounded-lg border border-line bg-surface px-3 py-2.5">
      <div className="flex min-w-0 items-center gap-2">
        {createElement(kind.icon, { className: `size-4 shrink-0 ${kind.color}`, 'aria-hidden': true })}
        <button
          type="button"
          onClick={() => workspace.openDocument(document.id)}
          title={document.filename}
          className="min-w-0 truncate rounded text-left text-[13px] font-medium hover:underline"
        >
          {name}
        </button>
      </div>
      <div className="mt-1.5">
        <ReviewControls document={document} onChanged={onChanged} />
      </div>
    </li>
  )
}
