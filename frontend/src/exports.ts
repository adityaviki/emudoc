// A project's exports as the projects page shows them (backend/app/export.py): which are still being made, and
// what each one is now, in words.

import type { ProjectExport } from './api.ts'
import { formatSize } from './format.ts'

/** How long an export is kept once it is ready (EXPORT_DAYS in backend/app/export.py). */
export const EXPORT_DAYS = 7

/** The projects page's query that opens a project's exports, which the notice leads to (EXPORT_QUERY there). */
export const EXPORT_QUERY = 'export'

/** Whether any of the exports is still being made, which the panel asks about again until none is. */
export const making = (exports: ProjectExport[]) => exports.some((made) => made.status === 'queued')

/** What one export is now: being made, ready with what it holds until a day `day` words, or why it failed; one of documents chosen in the library says so. */
export function describeExport(made: ProjectExport, day: (value: string) => string): string {
  const chosen = made.chosen === null ? '' : 'Chosen documents: '
  if (made.status === 'queued') return made.chosen === null ? 'Being made…' : 'Chosen documents, being made…'
  if (made.status === 'failed' || made.expires_at === null) return `${chosen}${made.error ?? 'It could not be made.'}`
  const documents = `${made.documents.toLocaleString('en')} document${made.documents === 1 ? '' : 's'}`
  return `${chosen}${documents}, ${formatSize(made.size_bytes)}, until ${day(made.expires_at)}`
}
