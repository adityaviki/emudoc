// A member's own files in a project, their My files (backend/app/my_files.py): the words of their list and of a file's
// place in the library, its versions, what keeps one from being shown or served, a copy into the library, and the
// documents the agent is asked to write there outside a chat (backend/app/drafts.py), while it writes them.

import type { Document, DraftKind, MyFile, MyFileKind, MyFileVersion, MyFileWriting, MyFiles } from './api.ts'
import { formatDateTime, formatSize } from './format.ts'

/** The query of the member's files in a project, which a file added, changed or deleted asks again. */
export const myFilesKey = (projectId: number) => ['my-files', projectId] as const
/** The query of one file with every version. */
export const myFileKey = (fileId: number) => ['my-file', fileId] as const

/** The files My files keeps, for the file pickers. */
export const MY_FILE_ACCEPT = '.docx,.xlsx'
/** What each kind of file is called. */
export const KIND_WORDS: Record<MyFileKind, string> = { docx: 'Word document', xlsx: 'Excel workbook' }
/** How often an open file, or the list, is asked again while a version waits for its scan or its pages, or the agent writes a file. */
export const MY_FILE_POLL = 2000

export const MY_FILES_NOTE =
  'Your own Word documents and Excel workbooks in this project, which only you see. Copy one into the project’s library to share it with its members.'

/** Whether the version waits for its malware scan or its pages, which the page follows. */
export function waiting(version: MyFileVersion): boolean {
  return version.scan === 'pending' || version.view_state === 'queued'
}

/** Whether the version may be downloaded, shown or copied: the scan passed it, there was none, or the agent wrote it. */
export function servable(version: MyFileVersion): boolean {
  return version.scan === 'passed' || version.scan === 'skipped'
}

/** What a file is in the list: its kind, its size, its versions and when it last changed. */
export function myFileWords(file: MyFile): string {
  const versions = `${file.versions} version${file.versions === 1 ? '' : 's'}`
  return [KIND_WORDS[file.kind], formatSize(file.latest.size_bytes), versions, `changed ${formatDateTime(file.updated_at)}`].join(' · ')
}

/** A version's name in the file's list of them. */
export function myVersionName(version: MyFileVersion, newest: number): string {
  return version.number === newest ? `Version ${version.number}, the newest` : `Version ${version.number}`
}

/** What a version is: who made it, what changed, its size and when. */
export function myVersionWords(version: MyFileVersion): string {
  const by = version.by_model ? 'Written by the agent' : null
  const note = version.note || (version.by_model ? null : 'Uploaded')
  return [by, note, formatSize(version.size_bytes), formatDateTime(version.created_at)].filter(Boolean).join(' · ')
}

/** Why a version's pages are not shown, or null when they are; `views` is whether the deployment shows Office files as they look. */
export function unshownWords(version: MyFileVersion, views: boolean): string | null {
  if (version.scan === 'flagged') return version.view_error ?? 'The malware scan flagged this file. Delete it.'
  if (version.view_state === 'ready' && version.view_page_sizes.length > 0) return null
  if (version.view_state === 'failed') return version.view_error ?? 'Its pages could not be made. Try again, or download it to open it.'
  if (version.scan === 'pending') return 'Waiting for the malware scan before it can be opened…'
  if (version.view_state === 'queued') return 'Making its pages…'
  return views ? 'Its pages are not made yet.' : 'This deployment does not show Word and Excel files as they look. Download it to open it.'
}

/** Why a version is not offered to download or copy, or null when it is. */
export function heldWords(version: MyFileVersion): string | null {
  if (version.scan === 'flagged') return 'The malware scan flagged it, so it cannot be downloaded or copied.'
  if (version.scan === 'pending') return 'It waits for its malware scan before it can be downloaded or copied.'
  return null
}

/** Whether "Try again" is offered: a scan or a view that failed, never a flagged file. */
export function retryable(version: MyFileVersion): boolean {
  return version.scan !== 'flagged' && version.view_state === 'failed'
}

/** What the confirmation of a deletion asks, since nothing brings a file back. */
export function myFileDeletionQuestion(file: MyFile): string {
  const versions = file.versions === 1 ? '' : ` and its ${file.versions} versions`
  return `Delete “${file.name}”${versions} for good? Nobody can bring it back.`
}

/** What the page says once a file was copied into the library: a new document, or the next version of the one it came from. */
export function copiedWords(document: Document, asVersion: boolean): string {
  return asVersion
    ? `“${document.filename}” in the library is now version ${document.version}. Its new file is being read.`
    : `“${document.filename}” is in the library now, being read. Your file stays here.`
}

/** Whether the agent has yet to settle the writing, and My files should ask again. */
export const underWay = (writing: Pick<MyFileWriting, 'status'>) => writing.status !== 'failed'

/** Whether the list should be asked again: a file's newest version waits for its scan or its pages, or a writing is under way. */
export function following(listed: Pick<MyFiles, 'files' | 'writings'>): boolean {
  return listed.files.some((file) => waiting(file.latest)) || listed.writings.some(underWay)
}

/** How far a writing is, in a line: waiting for the worker, being written, or why it failed. */
export function writingWords(writing: MyFileWriting): string {
  if (writing.status === 'queued') return 'Waiting to be written…'
  if (writing.status === 'writing') return 'Being written…'
  return writing.error ?? 'It could not be written. Try again.'
}

/** The dispute letter a disputed document's review asks the agent for, about it alone. */
export function disputeLetter(document: Pick<Document, 'id'>): { kind: DraftKind; document_ids: number[] } {
  return { kind: 'dispute_letter', document_ids: [document.id] }
}
