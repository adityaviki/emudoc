// The trash's words (backend/app/trash.py): what each thing in it was and who deleted it, and
// what a deletion that put things there says. The pages give the date, worded in the browser's
// own zone, so that these stay pure.

import type { ProjectTrash, Trashed, TrashedProject } from './api.ts'
import type { Question } from './components/Confirm.tsx'

/** A project's trash, and the organisation's trash of projects. */
export const trashKey = (projectId: number) => ['trash', projectId] as const
export const TRASHED_PROJECTS_KEY = ['trash', 'projects'] as const

const count = (n: number, noun: string) => `${n} ${noun}${n === 1 ? '' : 's'}`

const deleted = (by: string | null, on: string) => (by ? `Deleted by ${by} on ${on}` : `Deleted on ${on}`)

/** What something in a project's trash is called there: a chat before its first question has no title. */
export function trashedName(item: Trashed): string {
  return item.kind === 'chat' && !item.name ? 'New chat' : item.name
}

/** The line under it: where it was, what went with a folder, and who deleted it and when. */
export function describeTrashed(item: Trashed, on: string): string {
  const parts: string[] = []
  if (item.place) parts.push(`In ${item.place}`)
  if (item.kind === 'folder') parts.push(item.documents ? count(item.documents, 'file') : 'No files')
  // A chat in the trash is the asker's own.
  parts.push(deleted(item.kind === 'chat' ? null : item.deleted_by, on))
  return parts.join(' · ')
}

export function describeTrashedProject(project: TrashedProject, on: string): string {
  return `${count(project.document_count, 'document')} · ${deleted(project.deleted_by, on)}`
}

/** What the library says once a deletion put things in the trash, beside the way to undo it. */
export function movedToTrash(names: string[]): string {
  return names.length === 1 ? `“${names[0]}” is in the trash.` : `${names.length} items are in the trash.`
}

const NO_UNDO = 'This cannot be undone.'

// "a", "a and b", "a, b and c".
const listed = (parts: string[]) => (parts.length > 1 ? `${parts.slice(0, -1).join(', ')} and ${parts.at(-1)}` : parts[0])

/**
 * What is asked before something in a project's trash is deleted for good, with what goes with
 * it: a folder takes everything under it, what was deleted from it before as well.
 */
export function purgeQuestion(item: Trashed): Question {
  const name = trashedName(item)
  const from = item.place ? ` from ${item.place}` : ''
  const files = item.documents === 1 ? 'the file' : `the ${item.documents} files`
  const what = {
    document: `The file “${name}”${from} is deleted for good, with its pages and what was read from it.`,
    folder: item.documents
      ? `The folder “${name}”${from} is deleted for good with everything in it: ${files} deleted with it, and anything deleted from it earlier.`
      : `The folder “${name}”${from} is deleted for good with everything in it, anything deleted from it earlier included.`,
    chat: `The chat “${name}” is deleted for good, with its answers.`,
  }[item.kind]
  return { title: `Delete “${name}” for good?`, body: `${what} ${NO_UNDO}`, action: 'Delete for good' }
}

/**
 * What is asked before the trash is emptied: what it holds by kind while the list holds all of
 * it, else how much, since the list holds the newest alone.
 */
export function emptyingQuestion(trash: ProjectTrash): Question {
  const { items, total } = trash
  let what: string
  if (total > items.length) {
    what = `All ${total} items in the trash are deleted for good, those deleted before the ones listed included.`
  } else {
    const of = (kind: Trashed['kind']) => items.filter((item) => item.kind === kind)
    const folders = of('folder')
    const inFolders = folders.reduce((sum, folder) => sum + folder.documents, 0)
    const parts: string[] = []
    if (of('document').length) parts.push(count(of('document').length, 'file'))
    if (folders.length) {
      const held = inFolders ? ` with ${count(inFolders, 'file')} in ${folders.length === 1 ? 'it' : 'them'}` : ''
      parts.push(`${count(folders.length, 'folder')}${held}`)
    }
    if (of('chat').length) parts.push(count(of('chat').length, 'chat'))
    what = `Everything in the trash is deleted for good: ${listed(parts)}.`
  }
  return { title: 'Empty the trash?', body: `${what} ${NO_UNDO}`, action: 'Empty trash' }
}

/** What is asked before a project in the trash is deleted for good, everything in it with it. */
export function projectPurgeQuestion(project: TrashedProject): Question {
  const n = project.document_count
  const documents = n ? [n === 1 ? 'its document' : `its ${n} documents`] : []
  const parts = [...documents, 'its folders', "every member's chats in it", 'its own trash']
  return {
    title: `Delete “${project.name}” for good?`,
    body: `The project is deleted for good with everything in it: ${listed(parts)}. ${NO_UNDO}`,
    action: 'Delete for good',
  }
}
