// A document's details as its members set them (backend/app/details.py), its note (backend/app/notes.py) and its
// copies in the organisation's other projects (backend/app/duplicates.py): what the document tab, its details dialog
// and the duplicates page say, and the change the dialog sends.

import type { Document, DocumentCopy, DocumentDetail, DocumentDetailsInput, DocumentNote, DuplicatesPage, Languages, Organization } from './api.ts'
import { formatDate, formatSize } from './format.ts'
import { WORDS_AS_WRITTEN, languageName } from './organizationSettings.ts'
import type { Choice } from './organizationSettings.ts'
import { buildWorkspaceUrl } from './workspace/url.ts'

export const DETAILS: DocumentDetail[] = ['title', 'doc_type', 'doc_date', 'language']

export const DETAIL_LABELS: Record<DocumentDetail, string> = { title: 'Title', doc_type: 'Type', doc_date: 'Date', language: 'Language' }

export const noteKey = (documentId: number) => ['note', documentId] as const
export const copiesKey = (documentId: number) => ['copies', documentId] as const
export const DUPLICATES_KEY = ['duplicates'] as const

/** The details as the dialog's fields hold them, '' for none. */
export type DetailsForm = Record<DocumentDetail, string>

export function detailsForm(document: Document): DetailsForm {
  return { title: document.title ?? '', doc_type: document.doc_type ?? '', doc_date: document.doc_date ?? '', language: document.language ?? '' }
}

/** What the reading found of a detail, as a field would hold it. */
export function readValue(document: Document, detail: DocumentDetail): string {
  const read = { title: document.read_title, doc_type: document.read_doc_type, doc_date: document.read_doc_date, language: document.read_language }
  return read[detail] ?? ''
}

/**
 * The languages a document may be searched in, its organisation's first as the choice of none ('', which follows the
 * organisation's when it changes); until they load, the one the field holds, so the select shows it.
 */
export function documentLanguageChoices(languages: Languages | undefined, organization: Organization, held: string): Choice[] {
  const own = organization.language ?? languages?.default
  const first = { value: '', label: own ? `The organisation’s, now ${languageName(own)}` : 'The organisation’s' }
  const names = languages?.languages ?? (held ? [held] : [])
  return [first, ...names.map((name) => ({ value: name, label: languageName(name) }))]
}

/**
 * The change the dialog asks for: each detail whose field it changed, as the member's (null for none), and those it
 * takes from the reading again, which only a detail a member set needs; nothing of a field left as it was, so that a
 * detail nobody set keeps following the readings.
 */
export function detailsChange(document: Document, form: DetailsForm, asRead: ReadonlySet<DocumentDetail>): DocumentDetailsInput {
  const change: DocumentDetailsInput = {}
  const shown = detailsForm(document)
  for (const detail of DETAILS) {
    if (asRead.has(detail)) continue
    const value = form[detail].trim()
    if (value !== shown[detail]) change[detail] = value || null
  }
  const taken = DETAILS.filter((detail) => asRead.has(detail) && document.edited_details.includes(detail))
  if (taken.length) change.as_read = taken
  return change
}

/** What the dialog says under a detail a member set: what the reading found in its place. */
export function readWords(document: Document, detail: DocumentDetail): string {
  const read = readValue(document, detail)
  if (detail === 'language') {
    if (!read) return 'You set this. The reading could not tell its language, so it follows the organisation’s.'
    if (read === WORDS_AS_WRITTEN) return 'You set this. The reading found a language Emudoc searches by its words as written.'
    return `You set this. The reading found ${languageName(read)}.`
  }
  if (!read) return 'You set this. The reading found none.'
  return `You set this. The reading found “${detail === 'doc_date' ? formatDate(read) : read}”.`
}

/** Who changed the note last and when; '' for no note. */
export function noteWords(note: DocumentNote): string {
  if (!note.changed_at) return ''
  const when = formatDate(note.changed_at)
  return note.changed_by ? `Changed by ${note.changed_by} on ${when}` : `Changed on ${when}`
}

/** Where a copy is: its project, and its folder in it. */
export function copyPlace(copy: DocumentCopy): string {
  return copy.folder ? `${copy.project_name} / ${copy.folder}` : copy.project_name
}

/** The copy opened in its project, its folder shown in the library. */
export function copyUrl(copy: DocumentCopy): string {
  return buildWorkspaceUrl({
    projectId: copy.project_id,
    library: { folderId: copy.folder_id },
    document: { id: copy.id, focus: null },
    search: null,
    chatId: null,
    about: null,
  })
}

/** What the duplicates page lists, over every page of it. */
export function duplicatesWords(page: Pick<DuplicatesPage, 'files' | 'copy_bytes'>): string {
  if (page.files === 0) return 'No file is in more than one of the projects you see.'
  const files = page.files === 1 ? 'One file is' : `${page.files.toLocaleString('en')} files are`
  return `${files} in more than one of the projects you see. The copies past the first take ${formatSize(page.copy_bytes)}.`
}
