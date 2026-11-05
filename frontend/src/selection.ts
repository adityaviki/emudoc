// The documents chosen together in the library, which its menu indexes again and exports at once
// (backend/app/selection.py): how many it takes, and what the library says after.

import type { DocumentsReprocessed } from './api.ts'
import { EXPORT_DAYS } from './exports.ts'

/** How many documents one change takes at once, a page of the library (MAX_CHOSEN in backend/app/selection.py). */
export const MAX_CHOSEN = 100

const documents = (count: number) => `${count.toLocaleString('en')} document${count === 1 ? '' : 's'}`

/** What the library says once the chosen documents are queued: how many, and those left because they were being indexed already. */
export function reprocessedWords({ documents: queued, skipped }: DocumentsReprocessed, rest: boolean): string {
  const left =
    skipped === 0 ? '' : `${documents(skipped)} being indexed already ${skipped === 1 ? 'was left as it is' : 'were left as they are'}.`
  if (queued.length === 0) return skipped ? 'They are being indexed already, so nothing more was queued.' : 'None of them is partly indexed any more.'
  const done = rest ? `Indexing the rest of ${documents(queued.length)}.` : `Indexing ${documents(queued.length)} again.`
  return left ? `${done} ${left}` : done
}

/** What the library says once an export of the chosen documents is asked for. */
export function exportWords(count: number): string {
  return `An export of ${documents(count)} is being made. You are told when it is ready, and the project’s exports on the projects page list it for ${EXPORT_DAYS} days.`
}
