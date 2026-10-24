import { describe, expect, it } from 'vitest'
import type { Document } from './api.ts'
import { formatDate } from './format.ts'
import { STATE_CHOICES, changedWords, countWords, emptyWords, reviewWords, stateMoves } from './reviews.ts'

const reviewed = (fields: Partial<Document> = {}) => ({
  review: null,
  assignee_id: null,
  assignee: null,
  review_changer: null,
  review_changed_at: null,
  ...fields,
})

describe("a document's review", () => {
  it('says where it stands and whom it is assigned to, the member themselves as you', () => {
    expect(reviewWords(reviewed())).toBe('Not in review')
    expect(reviewWords(reviewed({ review: 'to_review' }))).toBe('To review')
    expect(reviewWords(reviewed({ review: 'disputed', assignee_id: 4, assignee: 'Bob' }))).toBe('Disputed, assigned to Bob')
    expect(reviewWords(reviewed({ review: 'approved', assignee_id: 4, assignee: 'Bob' }), 4)).toBe('Approved, assigned to you')
    expect(reviewWords(reviewed({ review: 'approved', assignee_id: 4, assignee: null }))).toBe('Approved, assigned to a former member')
  })

  it('says who moved it to its state and when, the day alone for an automation or an account gone', () => {
    const at = '2026-10-24T09:00:00Z'
    expect(changedWords(reviewed())).toBeNull()
    expect(changedWords(reviewed({ review: 'to_review', review_changer: 'Ada', review_changed_at: at }))).toBe(
      `Put in review by Ada on ${formatDate(at)}`,
    )
    expect(changedWords(reviewed({ review: 'approved', review_changer: 'Bob', review_changed_at: at }))).toBe(
      `Approved by Bob on ${formatDate(at)}`,
    )
    expect(changedWords(reviewed({ review: 'disputed', review_changed_at: at }))).toBe(`Disputed on ${formatDate(at)}`)
  })

  it('offers each state to choose, none first, and from the menu the moves it is not in', () => {
    expect(STATE_CHOICES.map((choice) => choice.label)).toEqual(['Not in review', 'To review', 'Approved', 'Disputed'])
    expect(STATE_CHOICES[0].value).toBe('')
    expect(stateMoves(null)).toEqual([{ state: 'to_review', label: 'Put in review' }])
    expect(stateMoves('to_review').map((move) => move.label)).toEqual(['Approve', 'Dispute', 'Take out of review'])
    expect(stateMoves('approved').map((move) => move.state)).toEqual(['disputed', 'to_review', null])
    expect(stateMoves('disputed').map((move) => move.label)).toEqual(['Approve', 'Back to review', 'Take out of review'])
  })
})

describe('the reviews view', () => {
  it('counts each state once the server says how many', () => {
    expect(countWords('to_review', undefined)).toBe('To review')
    expect(countWords('approved', 3)).toBe('Approved (3)')
  })

  it('says what an empty state means, the member’s own when asked', () => {
    expect(emptyWords('to_review', false).title).toBe('Nothing to review')
    expect(emptyWords('to_review', true).title).toBe('Nothing to review assigned to you')
    expect(emptyWords('approved', false).title).toBe('No approved documents')
    expect(emptyWords('disputed', true).title).toBe('No disputed documents assigned to you')
  })
})
