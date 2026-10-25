// A document's versions (backend/app/versions.py): the words of its list on the document's tab, and of a new
// version given to it.

import { ACCEPTED_EXTENSIONS } from './api.ts'
import type { Document, DocumentVersion } from './api.ts'
import { formatDateTime, formatSize } from './format.ts'

/** The query of a document's versions, which a new version asks again. */
export const versionsKey = (documentId: number) => ['versions', documentId] as const

/** What a new version may be: a file the server reads, never one that brings others, a message or an archive, which only an upload opens. */
export const VERSION_EXTENSIONS = ACCEPTED_EXTENSIONS.filter((extension) => extension !== '.eml' && extension !== '.zip')

/** A version's name in its list: its number, and whether it is the document's file now. */
export function versionName(version: DocumentVersion): string {
  return version.current ? `Version ${version.number}, the current one` : `Version ${version.number}`
}

/** What a version was: its file's name and size, its pages, and who brought it in when. */
export function versionWords(version: DocumentVersion): string {
  const pages = version.page_count ? `${version.page_count} page${version.page_count === 1 ? '' : 's'}` : null
  const by = version.uploaded_by ? ` by ${version.uploaded_by}` : ''
  return [version.filename, formatSize(version.size_bytes), pages, `uploaded${by} on ${formatDateTime(version.uploaded_at)}`]
    .filter(Boolean)
    .join(' · ')
}

/** Why an earlier version's file or text is not offered. */
export function versionHeld(version: DocumentVersion): string | null {
  if (!version.served) return 'It was replaced before the malware scan passed it, so it is not served.'
  if (!version.has_text) return 'It was replaced before its text was read.'
  return null
}

/** What the library says once a document was given its new version, which is read from now on. */
export function givenWords(document: Document): string {
  return `“${document.filename}” is now version ${document.version}. Its new file is being read; the earlier one is under Versions on its tab.`
}

/** What the versions say of a document that never had another file. */
export const ONLY_VERSION =
  'This is the only version. Upload a new version when the file changes: the document keeps its chats, its review and its checks, and its earlier file stays here to open.'
