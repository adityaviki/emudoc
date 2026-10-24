// Documents' reviews as the page words them (backend/app/reviews.py): where a document stands, whom it is assigned
// to and who moved it, the choices of the controls that move it, and the reviews view's counts and empty states.
//
// A review is a member's word on the document as a whole: put in review, then approved or disputed. It is not the
// facts table's "Unchecked" (src/factTable.ts), the documents with a value nobody checked, which is work towards it.

import type { Document, ReviewState } from './api.ts'
import { formatDate } from './format.ts'

/** Each state as the page names it, in the server's order. */
export const REVIEW_LABELS: Record<ReviewState, string> = {
  to_review: 'To review',
  approved: 'Approved',
  disputed: 'Disputed',
}

export const REVIEW_STATES = Object.keys(REVIEW_LABELS) as ReviewState[]

/** Where the reviews view's pages are kept, which a change to any document's review invalidates. */
export const reviewsKey = (projectId: number) => ['reviews', projectId] as const
/** The members a project's documents may be assigned to. */
export const reviewersKey = (projectId: number) => ['reviewers', projectId] as const

type Reviewed = Pick<Document, 'review' | 'assignee_id' | 'assignee' | 'review_changer' | 'review_changed_at'>

/** Where a document's review stands in a few words: its state, and whom it is assigned to, "you" for the member's own. */
export function reviewWords(document: Pick<Document, 'review' | 'assignee_id' | 'assignee'>, myId?: number): string {
  if (!document.review) return 'Not in review'
  const state = REVIEW_LABELS[document.review]
  if (document.assignee_id == null) return state
  const whom = document.assignee_id === myId ? 'you' : (document.assignee ?? 'a former member')
  return `${state}, assigned to ${whom}`
}

/** Who moved it to its state and when: by an automation or an account since gone, the day alone. */
export function changedWords(document: Reviewed): string | null {
  if (!document.review || !document.review_changed_at) return null
  const done = document.review === 'to_review' ? 'Put in review' : REVIEW_LABELS[document.review]
  const by = document.review_changer ? ` by ${document.review_changer}` : ''
  return `${done}${by} on ${formatDate(document.review_changed_at)}`
}

/** The state select's choices, "Not in review" first, as an empty value. */
export const STATE_CHOICES: { value: ReviewState | ''; label: string }[] = [
  { value: '', label: 'Not in review' },
  ...REVIEW_STATES.map((state) => ({ value: state, label: REVIEW_LABELS[state] })),
]

/** What the library row's menu offers a document's review: the states it is not in, and out of review when it is in one. */
export function stateMoves(review: ReviewState | null): { state: ReviewState | null; label: string }[] {
  if (!review) return [{ state: 'to_review', label: 'Put in review' }]
  const moves: { state: ReviewState | null; label: string }[] = []
  if (review !== 'approved') moves.push({ state: 'approved', label: 'Approve' })
  if (review !== 'disputed') moves.push({ state: 'disputed', label: 'Dispute' })
  if (review !== 'to_review') moves.push({ state: 'to_review', label: 'Back to review' })
  moves.push({ state: null, label: 'Take out of review' })
  return moves
}

/** A segment of the reviews view: the state and how many there are of it. */
export function countWords(state: ReviewState, count: number | undefined): string {
  return count == null ? REVIEW_LABELS[state] : `${REVIEW_LABELS[state]} (${count})`
}

/** What the reviews view says when it lists none of a state. */
export function emptyWords(state: ReviewState, mine: boolean): { title: string; body: string } {
  const whose = mine ? ' assigned to you' : ''
  if (state === 'to_review') {
    return {
      title: `Nothing to review${whose}`,
      body: 'Put a document in review from its menu in the library or its tab, and assign it to a member to ask them to look at it.',
    }
  }
  return {
    title: `No ${state === 'approved' ? 'approved' : 'disputed'} documents${whose}`,
    body:
      state === 'approved'
        ? 'A document approved in its review is listed here, the latest first.'
        : 'A document disputed in its review, with whoever sent it, is listed here until it is approved or taken out of review.',
  }
}
