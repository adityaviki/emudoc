// The trash's words (backend/app/trash.py): what each thing in it was and who deleted it, and
// what a deletion that put things there says. The pages give the date, worded in the browser's
// own zone, so that these stay pure.

import type { Trashed, TrashedProject } from './api.ts'

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
