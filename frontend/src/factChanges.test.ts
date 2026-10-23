import { describe, expect, it } from 'vitest'
import type { DocumentChanges, ExtractionTemplate, FactChanges, FieldChange } from './api.ts'
import {
  deletedReadAgain,
  fieldChangeWords,
  kindReadAgain,
  queuedWords,
  readAgainQuestion,
  readingsWords,
  savedReadAgain,
  savedWords,
  templateChangeWords,
} from './factChanges.ts'

const template = (changes: Partial<ExtractionTemplate> = {}): ExtractionTemplate => ({
  id: 4,
  doc_type: 'invoice',
  title: 'Invoice',
  description: '',
  fields: [],
  built_in: false,
  replaces_built_in: true,
  documents: 3,
  matching: 2,
  reading: 0,
  changed: 0,
  updated_at: null,
  ...changes,
})

const field = (changes: Partial<FieldChange> = {}): FieldChange => ({
  name: 'total_amount',
  before: [],
  after: [],
  before_count: 0,
  after_count: 0,
  checked: false,
  ...changes,
})

const readings = (changes: Partial<FactChanges> = {}): FactChanges => ({
  kind: 'invoice',
  template: null,
  reading: 0,
  read: 0,
  changed: 0,
  unheld: 0,
  documents: [],
  next_after: null,
  ...changes,
})

describe('reading a template\'s documents again', () => {
  it('takes those of its kind, and those of the kind the project\'s own was for before it moved', () => {
    expect(savedReadAgain(template(), template())).toEqual({ ask: { kind: 'invoice' }, kind: 'invoice', documents: 5 })
    const moved = savedReadAgain(template({ doc_type: 'bill', documents: 0, matching: 1 }), template({ documents: 6 }))
    expect(moved).toEqual({ ask: { kind: 'bill', was: 'invoice' }, kind: 'bill', documents: 7 })
    // A built-in one customised under another kind still applies to its own: nothing moved.
    const customised = savedReadAgain(template({ doc_type: 'bill', documents: 0, matching: 0 }), template({ id: null, built_in: true }))
    expect(customised).toEqual({ ask: { kind: 'bill' }, kind: 'bill', documents: 0 })
    expect(savedReadAgain(template({ documents: 1, matching: 0 }), null).documents).toBe(1)
  })

  it('takes those read for a deleted one alone, and from the changes those of its kind', () => {
    expect(deletedReadAgain(template())).toEqual({ ask: { was: 'invoice' }, kind: 'invoice', documents: 3 })
    expect(kindReadAgain(template())).toEqual({ ask: { kind: 'invoice' }, kind: 'invoice', documents: 5 })
  })

  it('asks first, naming how many, and says what it did', () => {
    const one = readAgainQuestion({ ask: { kind: 'invoice' }, kind: 'invoice', documents: 1 })
    expect(one.title).toBe('Read the invoice document again?')
    expect(one.action).toBe('Read it again')
    expect(one.body).toMatch(/^The AI model reads its summaries and facts again/)
    const many = readAgainQuestion({ ask: { kind: 'delivery note' }, kind: 'delivery note', documents: 12 })
    expect(many.title).toBe('Read the 12 delivery note documents again?')
    expect(many.action).toBe('Read them again')
    expect(queuedWords(1)).toBe('1 document is being read again.')
    expect(queuedWords(3)).toBe('3 documents are being read again.')
    expect(queuedWords(0)).toMatch(/^No document needed reading again/)
  })

  it('says once a template is saved what keeps the facts read before', () => {
    expect(savedWords(null)).toBe('Documents uploaded from now on are read for the templates as they are.')
    expect(savedWords({ ask: {}, kind: 'invoice', documents: 1 })).toMatch(/ The invoice document here keeps the facts read before until read again\.$/)
    expect(savedWords({ ask: {}, kind: 'invoice', documents: 4 })).toMatch(/ The 4 invoice documents here keep the facts/)
  })
})

describe('what a reading changed', () => {
  it('words a field found, no longer found, or read otherwise, with how many more there were', () => {
    expect(fieldChangeWords(field({ after: ['March 2026'], after_count: 1 }))).toBe('Found “March 2026”.')
    expect(fieldChangeWords(field({ before: ['30 days'], before_count: 1 }))).toBe('No longer found: “30 days”.')
    expect(fieldChangeWords(field({ before: ['EUR 1,200.00'], before_count: 1, after: ['EUR 1,300.00'], after_count: 1 }))).toBe(
      '“EUR 1,200.00” before, now “EUR 1,300.00”.',
    )
    expect(fieldChangeWords(field({ after: ['a', 'b'], after_count: 9 }))).toBe('Found “a”, “b” and 7 more.')
  })

  it('says when the template a document is read for changed', () => {
    const read = (before: string | null, after: string | null): DocumentChanges => ({
      document_id: 1,
      title: 'Invoice',
      filename: 'invoice.md',
      read_at: '2026-10-23T12:00:00Z',
      template_before: before,
      template_after: after,
      fields: [],
    })
    expect(templateChangeWords(read('invoice', 'invoice'))).toBeNull()
    expect(templateChangeWords(read('bill', 'invoice'))).toBe('Read for the invoice template now, for the bill template before.')
    expect(templateChangeWords(read(null, 'invoice'))).toBe('Read for the invoice template now, for no template before.')
    expect(templateChangeWords(read('invoice', null))).toBe('Read for no template now, for the invoice template before.')
  })

  it('says how far the reading is, and what it changed', () => {
    expect(readingsWords(readings())).toBe('No document of this kind has been read again since its first reading.')
    expect(readingsWords(readings({ reading: 2 }))).toBe('Reading 2 documents again…')
    expect(readingsWords(readings({ reading: 1, read: 3, changed: 1 }))).toBe('Reading 1 document again… 3 documents read again: 1 changed.')
    expect(readingsWords(readings({ read: 1, changed: 1, unheld: 1 }))).toBe("1 document read again: 1 changed, 1 of them against a member's check.")
  })
})
