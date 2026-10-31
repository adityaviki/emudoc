import { describe, expect, it } from 'vitest'
import type { Document, DocumentCopy, Organization } from './api.ts'
import { copyPlace, copyUrl, detailsChange, detailsForm, documentLanguageChoices, duplicatesWords, noteWords, readWords } from './details.ts'
import { formatDate } from './format.ts'

const read = { title: 'Master Services Agreement', doc_type: 'contract', doc_date: '2024-03-01' }

const document = (fields: Partial<Document> = {}): Document =>
  ({
    ...read,
    read_title: read.title,
    read_doc_type: read.doc_type,
    read_doc_date: read.doc_date,
    language: 'german',
    read_language: 'german',
    edited_details: [],
    ...fields,
  }) as Document

const copy = (fields: Partial<DocumentCopy> = {}): DocumentCopy => ({
  id: 7,
  project_id: 3,
  project_name: 'Archive',
  folder: '2024/March',
  folder_id: 12,
  filename: 'msa.pdf',
  ...fields,
})

describe('the details dialog', () => {
  it('sends the details whose fields changed, and nothing of the rest', () => {
    const shown = document()
    expect(detailsChange(shown, detailsForm(shown), new Set())).toEqual({})
    expect(detailsChange(shown, { ...detailsForm(shown), title: '  Acme services ', doc_date: '' }, new Set())).toEqual({
      title: 'Acme services',
      doc_date: null,
    })
  })

  it('takes what the reading found again only for a detail a member set', () => {
    const set = document({ title: 'Acme services', edited_details: ['title'] })
    const form = { ...detailsForm(set), title: 'Typed meanwhile' }
    expect(detailsChange(set, form, new Set(['title', 'doc_type']))).toEqual({ as_read: ['title'] })
  })

  it('says what the reading found under a detail the member set', () => {
    const set = document({ doc_date: '2024-04-02', read_doc_date: '2024-03-01', read_title: null })
    expect(readWords(set, 'doc_date')).toBe(`You set this. The reading found “${formatDate('2024-03-01')}”.`)
    expect(readWords(set, 'title')).toBe('You set this. The reading found none.')
  })

  it('sends a language chosen, the organisation’s as null, and says what the reading found of it', () => {
    const shown = document()
    expect(detailsChange(shown, { ...detailsForm(shown), language: 'dutch' }, new Set())).toEqual({ language: 'dutch' })
    expect(detailsChange(shown, { ...detailsForm(shown), language: '' }, new Set())).toEqual({ language: null })
    expect(readWords(document({ language: 'dutch' }), 'language')).toBe('You set this. The reading found German.')
    expect(readWords(document({ read_language: null }), 'language')).toBe(
      'You set this. The reading could not tell its language, so it follows the organisation’s.',
    )
    expect(readWords(document({ read_language: 'simple' }), 'language')).toBe(
      'You set this. The reading found a language Emudoc searches by its words as written.',
    )
  })

  it('offers the organisation’s language first, as it is now, and the one held until the languages load', () => {
    const organization = (language: string | null) => ({ language }) as Organization
    const languages = { languages: ['dutch', 'english', 'german'], default: 'english' }
    expect(documentLanguageChoices(languages, organization('german'), '').map((choice) => choice.label)).toEqual([
      'The organisation’s, now German',
      'Dutch',
      'English',
      'German',
    ])
    expect(documentLanguageChoices(languages, organization(null), '')[0]).toEqual({ value: '', label: 'The organisation’s, now English' })
    expect(documentLanguageChoices(undefined, organization(null), 'dutch')).toEqual([
      { value: '', label: 'The organisation’s' },
      { value: 'dutch', label: 'Dutch' },
    ])
  })
})

describe('a note', () => {
  it('names who changed it last and when', () => {
    const at = '2026-10-26T09:30:00Z'
    expect(noteWords({ text: 'Paid.', changed_by: 'Bob', changed_at: at })).toBe(`Changed by Bob on ${formatDate(at)}`)
    expect(noteWords({ text: 'Paid.', changed_by: null, changed_at: at })).toBe(`Changed on ${formatDate(at)}`)
    expect(noteWords({ text: '', changed_by: null, changed_at: null })).toBe('')
  })
})

describe('a copy in another project', () => {
  it('is placed by its project and folder and opened there', () => {
    expect(copyPlace(copy())).toBe('Archive / 2024/March')
    expect(copyPlace(copy({ folder: '', folder_id: null }))).toBe('Archive')
    expect(copyUrl(copy())).toBe('/projects/3/documents/7?folder=12')
  })

  it('is counted on the duplicates page', () => {
    expect(duplicatesWords({ files: 0, copy_bytes: 0 })).toBe('No file is in more than one of the projects you see.')
    expect(duplicatesWords({ files: 1, copy_bytes: 2048 })).toBe(
      'One file is in more than one of the projects you see. The copies past the first take 2 kB.',
    )
    expect(duplicatesWords({ files: 1200, copy_bytes: 5e6 })).toMatch(/^1,200 files are .* take 5 MB\.$/)
  })
})
