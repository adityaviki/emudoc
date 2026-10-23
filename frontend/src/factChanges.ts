// What a template's change did to the facts of its documents, once they were read again
// (backend/app/fact_changes.py, `POST /api/projects/{id}/templates/read-again`, `GET
// /api/projects/{id}/fact-changes`): which documents to read again and what that asks, and what
// each document's last reading changed, field by field, as the library's "What changed" view
// words it.

import type { DocumentChanges, ExtractionTemplate, FactChanges, FieldChange, ReadAgainInput } from './api.ts'
import type { Question } from './components/Confirm.tsx'

/** Every list of the project's readings, whatever its kind: what reading its documents again makes stale. */
export const factChangesKey = (projectId: number) => ['fact-changes', projectId] as const

export const changesKey = (projectId: number, kind: string) => [...factChangesKey(projectId), kind] as const

/** How often the view asks again while documents of its kind are being read. */
export const READING_POLL = 3000

const count = (n: number, noun: string) => `${n} ${noun}${n === 1 ? '' : 's'}`

/** Which of the project's documents a change of a template concerns, and how many: those of its kind now, and, when the project's own template moved to another kind or went, those read for the kind before. */
export interface ReadAgain {
  ask: ReadAgainInput
  /** The kind whose changes the view then shows. */
  kind: string
  documents: number
}

/** The documents to read again once a template is saved: those read for it or of its kind, and those of the kind the project's own template was for before, if it moved. */
export function savedReadAgain(saved: ExtractionTemplate, before: ExtractionTemplate | null): ReadAgain {
  const moved = before && !before.built_in && before.doc_type !== saved.doc_type ? before : null
  return {
    ask: moved ? { kind: saved.doc_type, was: moved.doc_type } : { kind: saved.doc_type },
    kind: saved.doc_type,
    documents: saved.documents + saved.matching + (moved?.documents ?? 0),
  }
}

/** The documents to read again once the project's own template is deleted: those read for it, which the built-in template of its kind, or none, then reads. */
export function deletedReadAgain(template: ExtractionTemplate): ReadAgain {
  return { ask: { was: template.doc_type }, kind: template.doc_type, documents: template.documents }
}

/** The documents to read again from the view of a kind's changes: those of its kind, as its template stands. */
export function kindReadAgain(template: ExtractionTemplate): ReadAgain {
  return { ask: { kind: template.doc_type }, kind: template.doc_type, documents: template.documents + template.matching }
}

/** What reading them again asks first: the model reads each, and what it changes is listed. */
export function readAgainQuestion({ kind, documents }: ReadAgain): Question {
  const them = documents === 1 ? 'it' : 'them'
  return {
    title: documents === 1 ? `Read the ${kind} document again?` : `Read the ${documents} ${kind} documents again?`,
    body: `The AI model reads ${documents === 1 ? 'its' : 'their'} summaries and facts again, for the templates as they are now, and "What changed" then lists what each reading changed of its template's fields. A fact a member checked keeps the check when it is read the same. It counts against the day's use of the model.`,
    action: `Read ${them} again`,
  }
}

/** What the templates view says once a template is saved or deleted: those uploaded from now on are read for it, those here once read again. */
export function savedWords(read: ReadAgain | null): string {
  const later = 'Documents uploaded from now on are read for the templates as they are.'
  if (!read?.documents) return later
  const here = read.documents === 1 ? `The ${read.kind} document here keeps` : `The ${read.documents} ${read.kind} documents here keep`
  return `${later} ${here} the facts read before until read again.`
}

/** What the view says once they are queued. */
export function queuedWords(queued: number): string {
  if (queued === 0) return 'No document needed reading again: each is being indexed already.'
  return `${count(queued, 'document')} ${queued === 1 ? 'is' : 'are'} being read again.`
}

/** A few values, quoted, and how many more there were. */
function values(shown: string[], total: number): string {
  const quoted = shown.map((value) => `“${value}”`).join(', ')
  return total > shown.length ? `${quoted} and ${total - shown.length} more` : quoted
}

/** What a reading changed of a field: what it found that the reading before had not, what it no longer found, or both. */
export function fieldChangeWords(change: FieldChange): string {
  const before = values(change.before, change.before_count)
  const after = values(change.after, change.after_count)
  if (!change.before_count) return `Found ${after}.`
  if (!change.after_count) return `No longer found: ${before}.`
  return `${before} before, now ${after}.`
}

/** What a field whose member's value the new reading did not find adds: the check is the cell's, to settle. */
export const CHECK_DISAGREES =
  'A member checked the value before, and the new reading disagrees: the facts table shows their check in its cell until it is settled.'

/** Whether and how the template a document is read for changed with its last reading; null when it did not. */
export function templateChangeWords({ template_before: before, template_after: after }: DocumentChanges): string | null {
  if (before === after) return null
  const as = (kind: string | null) => (kind ? `the ${kind} template` : 'no template')
  return `Read for ${as(after)} now, for ${as(before)} before.`
}

/** The view's status line: how many of the kind's documents are still being read, how many were read again and changed, and how many of those disagree with a member's check. */
export function readingsWords(changes: FactChanges): string {
  const parts: string[] = []
  if (changes.reading) parts.push(`Reading ${count(changes.reading, 'document')} again…`)
  if (!changes.read) {
    if (!changes.reading) parts.push('No document of this kind has been read again since its first reading.')
    return parts.join(' ')
  }
  const disagree = changes.unheld ? `, ${changes.unheld} of them against a member's check` : ''
  parts.push(`${count(changes.read, 'document')} read again: ${changes.changed} changed${disagree}.`)
  return parts.join(' ')
}
