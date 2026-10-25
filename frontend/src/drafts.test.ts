import { describe, expect, it } from 'vitest'
import type { DraftSummary } from './api.ts'
import {
  DRAFT_KINDS,
  KIND_LABELS,
  disputeLetter,
  draftByline,
  draftDeletionQuestion,
  draftFileUrl,
  draftProblem,
  draftStatusWords,
  offerWords,
  versionWords,
  writing,
} from './drafts.ts'
import { formatDateTime } from './format.ts'

const draft: DraftSummary = {
  id: 9,
  project_id: 3,
  kind: 'dispute_letter',
  title: 'Dispute letter: INV-1.md',
  status: 'done',
  created_by: 'Ada Lovelace',
  versions: 2,
  created_at: '2026-10-24T09:30:00Z',
  updated_at: '2026-10-24T09:45:00Z',
}

describe('drafts', () => {
  it('names every kind the server knows, once', () => {
    expect(DRAFT_KINDS.map((kind) => kind.value)).toEqual(['reply_email', 'dispute_letter', 'summary_memo', 'comparison_table', 'report'])
    expect(KIND_LABELS.comparison_table).toBe('Comparison table')
  })

  it('says how far the writing is, or how many versions there are', () => {
    expect(draftStatusWords({ ...draft, status: 'queued', versions: 0 })).toBe('Waiting to write it…')
    expect(draftStatusWords({ ...draft, status: 'queued' })).toBe('Waiting to write it again…')
    expect(draftStatusWords({ ...draft, status: 'writing', versions: 0 })).toBe('Writing it…')
    expect(draftStatusWords({ ...draft, status: 'failed', versions: 0 })).toBe('It could not be written.')
    expect(draftStatusWords({ ...draft, status: 'failed', versions: 1 })).toBe('1 version · The last writing failed.')
    expect(draftStatusWords(draft)).toBe('2 versions')
    expect([writing({ status: 'queued' }), writing({ status: 'writing' }), writing({ status: 'failed' })]).toEqual([true, true, false])
  })

  it('words who asked for a draft, and who wrote each version', () => {
    expect(draftByline(draft)).toBe(`Dispute letter · By Ada Lovelace · ${formatDateTime(draft.created_at)}`)
    expect(draftByline({ ...draft, created_by: null })).toContain('By a former member')
    const at = '2026-10-24T09:40:00Z'
    expect(versionWords({ number: 1, by_model: true, author: 'Ada Lovelace', created_at: at })).toBe(
      `Version 1, written by the model for Ada Lovelace, ${formatDateTime(at)}`,
    )
    expect(versionWords({ number: 2, by_model: false, author: null, created_at: at })).toBe(
      `Version 2, saved by a former member, ${formatDateTime(at)}`,
    )
  })

  it('asks for something to go by before a new draft, and words the dispute letter of a document', () => {
    expect(draftProblem({ kind: 'report', instructions: '  ', document_ids: [] })).toBe(
      'Say what it should say, or choose the documents it is about.',
    )
    expect(draftProblem({ kind: 'report', instructions: 'The delays this year', document_ids: [] })).toBeNull()
    expect(draftProblem({ kind: 'report', instructions: '', document_ids: [4] })).toBeNull()
    expect(disputeLetter({ id: 41 })).toEqual({ kind: 'dispute_letter', instructions: '', document_ids: [41] })
  })

  it('says what deleting a draft takes', () => {
    expect(draftDeletionQuestion(draft)).toEqual({
      title: 'Delete this draft?',
      body: '“Dispute letter: INV-1.md” goes for good, with its 2 versions. The documents it is about stay.',
      action: 'Delete draft',
    })
    expect(draftDeletionQuestion({ ...draft, versions: 1 }).body).toContain('with its version.')
    expect(draftDeletionQuestion({ ...draft, versions: 0 }).body).toContain('with what was asked of it.')
  })

  it('names the draft an answer offers by its kind and documents', () => {
    const offer = { kind: 'dispute_letter' as const, instructions: '', documents: [{ id: 4, title: 'INV-1.md' }] }
    expect(offerWords(offer)).toBe('Dispute letter about INV-1.md')
    expect(offerWords({ ...offer, documents: [...offer.documents, { id: 5, title: 'b' }, { id: 6, title: 'c' }] })).toBe(
      'Dispute letter about INV-1.md and 2 more',
    )
    expect(offerWords({ ...offer, kind: 'report', documents: [] })).toBe('Report')
  })

  it('downloads a version from its own address', () => {
    expect(draftFileUrl(7, 2, 'pdf')).toBe('/api/drafts/7/versions/2/export?format=pdf')
  })
})
