// What a chat is about, in words: the whole project, a folder with its subfolders, or documents
// chosen from it (backend/app/scopes.py), and the questions the library asks of a chat it starts
// about them. The server holds the agent to the scope; the page says what it is.

import type { ChatScope, ScopeIn } from './api.ts'

/** What "Summarise this folder" asks of a chat about the folder. */
export const SUMMARY_QUESTION = 'Summarise the documents in this folder: what each one is, and what they say together.'

/** What "Compare the two documents" asks of a chat about them, by their names. */
export function comparisonQuestion(first: string, second: string): string {
  return `Compare ${first} and ${second}: where do they agree, and where do they differ?`
}

/** The scope as the page shows it, given the folder's path by its id (empty for one it does not know). */
export function scopeWords(scope: ChatScope, pathOf: (folderId: number) => string): string {
  switch (scope.kind) {
    case 'project':
      return 'All documents'
    case 'folder':
      return scope.folder ? pathOf(scope.folder.id) || scope.folder.name : 'A folder no longer in the project'
    case 'documents':
      return documentsWords(scope.documents.map((document) => document.filename))
  }
}

function documentsWords(names: string[]): string {
  if (names.length === 0) return 'Documents no longer in the project'
  if (names.length <= 2) return names.join(' and ')
  return `${names.length} documents`
}

/** A scope as the server takes it, from one it showed. */
export function scopeIn(scope: ChatScope): ScopeIn {
  if (scope.kind === 'folder' && scope.folder) return { folder_id: scope.folder.id }
  if (scope.kind === 'documents' && scope.documents.length) return { document_ids: scope.documents.map((document) => document.id) }
  return {}
}

/**
 * A new chat's scope as the server would show it, from what the URL names: the folder's name, and
 * the documents' names as far as the page holds them (`nameOf`, undefined for one it does not hold,
 * named by its id meanwhile).
 */
export function scopeOf(
  about: ScopeIn | null,
  folderName: (folderId: number) => string | undefined,
  nameOf: (documentId: number) => string | undefined,
): ChatScope {
  if (about && 'folder_id' in about) {
    const name = folderName(about.folder_id)
    return { kind: 'folder', folder: name === undefined ? null : { id: about.folder_id, name }, documents: [] }
  }
  if (about && 'document_ids' in about && about.document_ids.length) {
    const documents = about.document_ids.map((id) => ({ id, filename: nameOf(id) ?? `Document ${id}` }))
    return { kind: 'documents', folder: null, documents }
  }
  return { kind: 'project', folder: null, documents: [] }
}

/** Whether two scopes name the same: what the chooser marks as chosen. */
export function sameScope(a: ScopeIn, b: ScopeIn): boolean {
  const key = (scope: ScopeIn) =>
    'folder_id' in scope ? `f${scope.folder_id}` : 'document_ids' in scope && scope.document_ids.length ? `d${[...scope.document_ids].sort((x, y) => x - y).join('-')}` : ''
  return key(a) === key(b)
}
