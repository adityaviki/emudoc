// The documents chosen together in the library, which its menu tags, indexes again and exports at once
// (backend/app/selection.py): which tags they carry, what a change of them sends, and what the library says after.

import type { Document, DocumentsReprocessed, Tag } from './api.ts'
import { EXPORT_DAYS } from './exports.ts'

/** How many documents one change takes at once, a page of the library (MAX_CHOSEN in backend/app/selection.py). */
export const MAX_CHOSEN = 100

/** How many tags a document carries at most (MAX_DOCUMENT_TAGS in backend/app/tags.py). */
export const MAX_DOCUMENT_TAGS = 20

/** Whether a tag is on every chosen document, on some of them, or on none. */
export type TagState = 'all' | 'some' | 'none'

/** Tags to put on every chosen document and to take off every one; a tag in neither is left as each carries it. */
export interface TagsChange {
  add: number[]
  remove: number[]
}

/** Where each of the project's tags stands among the documents. */
export function tagStates(documents: Document[], tags: Tag[]): Map<number, TagState> {
  return new Map(
    tags.map((tag) => {
      const carrying = documents.filter((document) => document.tag_ids.includes(tag.id)).length
      return [tag.id, carrying === 0 ? 'none' : carrying === documents.length ? 'all' : 'some']
    }),
  )
}

/**
 * Where a tag goes when its box is pressed, from where it stands now and where it stood when the dialog opened:
 * on every document or off every one, and for one only some carried, back to that after both.
 */
export function nextState(now: TagState, was: TagState): TagState {
  if (now === 'all') return 'none'
  if (now === 'none' && was === 'some') return 'some'
  return 'all'
}

/** The change from where the tags stood to where they stand now; a tag made in the dialog stood on none. */
export function tagsChange(was: ReadonlyMap<number, TagState>, now: ReadonlyMap<number, TagState>): TagsChange {
  const add: number[] = []
  const remove: number[] = []
  for (const [id, state] of now) {
    const before = was.get(id) ?? 'none'
    if (state === 'all' && before !== 'all') add.push(id)
    if (state === 'none' && before !== 'none') remove.push(id)
  }
  return { add, remove }
}

/** The tags a document carries after the change: those taken off gone, those put on after the ones it keeps (`retagged` in backend/app/tags.py). */
export function retagged(tagIds: number[], change: TagsChange): number[] {
  const kept = tagIds.filter((id) => !change.remove.includes(id))
  return [...kept, ...change.add.filter((id) => !kept.includes(id))]
}

/** The names of the documents the change would give more tags than a document carries, which the server would refuse it for. */
export function overfull(documents: Document[], change: TagsChange): string[] {
  return documents.filter((document) => retagged(document.tag_ids, change).length > MAX_DOCUMENT_TAGS).map((document) => document.filename)
}

/** Why the change cannot be saved, by the documents it would give too many tags. */
export function overfullWords(names: string[]): string {
  const [first] = names
  const others = names.length - 1
  const which = others === 0 ? `“${first}” would` : `“${first}” and ${others} other${others === 1 ? '' : 's'} would`
  return `${which} carry more than ${MAX_DOCUMENT_TAGS} tags, which is as many as a document carries. Take some off, or leave ${others === 0 ? 'it' : 'them'} out of the choice.`
}

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
