// A member's own files in a project, their My files (backend/app/my_files.py): the words of their list in the library
// and of a file's place, its versions, what keeps one from being shown or served, and a copy into the project's files;
// the folders they keep them in, as a tree, and what a folder uploaded whole sends of what it holds.

import { queryOptions } from '@tanstack/react-query'
import type { Question } from './components/Confirm.tsx'
import { api } from './api.ts'
import type { Document, MyFile, MyFileKind, MyFileVersion, MyFiles, MyFolder, UploadItem } from './api.ts'
import { formatDate, formatDateTime, formatSize } from './format.ts'
import { hidden, pathOf } from './workspace/uploads.ts'

/** The query of the member's files in a project, which a file added, changed or deleted asks again. */
export const myFilesKey = (projectId: number) => ['my-files', projectId] as const
/** The query of one file with every version. */
export const myFileKey = (fileId: number) => ['my-file', fileId] as const

/** The longest name a file takes (`NAME_CHARS` in backend/app/my_files.py). */
export const MY_FILE_NAME_CHARS = 300
/** The files My files keeps, for the file pickers. */
export const MY_FILE_ACCEPT = '.docx,.xlsx'
/** What each kind of file is called. */
export const KIND_WORDS: Record<MyFileKind, string> = { docx: 'Word document', xlsx: 'Excel workbook' }
/** How often an open file, or the list, is asked again while a version waits for its scan or its pages. */
export const MY_FILE_POLL = 2000

/** Said on an open file, whose tab sits among the project's documents'. */
export const MY_FILE_NOTE = 'In My files: only you see it.'

/** One file with every version, asked again while its newest waits; its tab and its view share it. */
export function myFileQuery(fileId: number) {
  return queryOptions({
    queryKey: myFileKey(fileId),
    queryFn: () => api.myFile(fileId),
    refetchInterval: (query) => (query.state.data && waiting(query.state.data.latest) ? MY_FILE_POLL : false),
  })
}

/** How many files the member keeps here, and the most a member may once they come near it. */
export function myFilesCount(count: number, limit: number): string {
  if (count >= limit * 0.9) return `${count} of ${limit} files`
  return `${count} file${count === 1 ? '' : 's'}`
}

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

/** What a file's row says beside its name: the agent, when it wrote the newest version, the versions past the first, and the day it changed. */
export function myFileLine(file: MyFile): string {
  const versions = file.versions > 1 ? `${file.versions} versions` : null
  return [file.latest.by_model ? 'By the agent' : null, versions, formatDate(file.updated_at)].filter(Boolean).join(' · ')
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

/** What a copy into the project's files does, said before it is made. */
export const COPY_WORDS = 'Its newest version becomes a document every member of the project sees, read and indexed as any upload. Your file stays here.'

/** What "Copy to project files…" in a file's menu asks, with where the copy goes chosen under it. */
export function copyQuestion(file: MyFile): Question {
  return { title: `Copy “${file.name}” to project files?`, body: COPY_WORDS, action: 'Copy' }
}

/** What the page says once a file was copied to the project's files: a new document, or the next version of the one it came from. */
export function copiedWords(document: Document, asVersion: boolean): string {
  return asVersion
    ? `“${document.filename}” in project files is now version ${document.version}. Its new file is being read.`
    : `“${document.filename}” is in project files now, being read. Your file stays here.`
}

/** Whether the list should be asked again: a file's newest version waits for its scan or its pages. */
export function following(listed: Pick<MyFiles, 'files'>): boolean {
  return listed.files.some((file) => waiting(file.latest))
}

/**
 * The files a search of the library finds in My files: those whose names hold its words in any case, as the project's
 * files' names are matched (the listing's `name=`, backend/app/api/documents.py), in the list's order; none without words.
 */
export function myFilesNamed(files: MyFile[], words: string): MyFile[] {
  const sought = words.trim().toLocaleLowerCase()
  return sought ? files.filter((file) => file.name.toLocaleLowerCase().includes(sought)) : []
}

/** Where a file a search found is in My files: the path of its folder, "Letters / 2026", or "My files" at the top. */
export function myFileWhere(file: Pick<MyFile, 'folder_id'>, folders: MyFolder[]): string {
  const byId = new Map(folders.map((folder) => [folder.id, folder]))
  const path: string[] = []
  const seen = new Set<number>()
  for (let at = file.folder_id == null ? undefined : byId.get(file.folder_id); at && !seen.has(at.id); at = at.parent_id == null ? undefined : byId.get(at.parent_id)) {
    seen.add(at.id)
    path.unshift(at.name)
  }
  return path.length ? path.join(' / ') : 'My files'
}

/** What a search's My files say when no file's name there holds its words. */
export const noMyFileNameWords = (words: string) => `No file’s name in My files holds “${words.trim()}”.`

/** A folder of My files with what it holds, as the list shows it: its folders by name, then its files, the one changed last first. */
export interface MyFolderNode {
  folder: MyFolder
  folders: MyFolderNode[]
  files: MyFile[]
}

/** The member's folders and files as a tree: what is at the top, each folder holding its own. */
export interface MyFilesTree {
  folders: MyFolderNode[]
  files: MyFile[]
}

const byName = (a: MyFolderNode, b: MyFolderNode) => a.folder.name.localeCompare(b.folder.name, undefined, { numeric: true, sensitivity: 'base' })

/** The list's folders and files as a tree, the folders by name as the library sorts its own and the files in the list's order; a file or a folder whose folder is not in the list sits at the top. */
export function myFilesTree(listed: Pick<MyFiles, 'files' | 'folders'>): MyFilesTree {
  const nodes = new Map(listed.folders.map((folder) => [folder.id, { folder, folders: [], files: [] } as MyFolderNode]))
  const tree: MyFilesTree = { folders: [], files: [] }
  for (const node of nodes.values()) {
    const parent = node.folder.parent_id == null ? undefined : nodes.get(node.folder.parent_id)
    ;(parent ?? tree).folders.push(node)
  }
  for (const file of listed.files) {
    const parent = file.folder_id == null ? undefined : nodes.get(file.folder_id)
    ;(parent ?? tree).files.push(file)
  }
  const sort = (node: MyFilesTree) => {
    node.folders.sort(byName)
    node.folders.forEach(sort)
  }
  sort(tree)
  return tree
}

/** How many files and folders a folder holds, at every depth. */
export function heldBy(node: MyFolderNode): { files: number; folders: number } {
  return node.folders.reduce(
    (held, child) => {
      const inner = heldBy(child)
      return { files: held.files + inner.files, folders: held.folders + 1 + inner.folders }
    },
    { files: node.files.length, folders: 0 },
  )
}

const counted = (n: number, one: string) => `${n} ${one}${n === 1 ? '' : 's'}`

/** What the confirmation of a folder's deletion asks, since nothing brings it back nor what it holds. */
export function myFolderDeletionQuestion(node: MyFolderNode): Question {
  const { files, folders } = heldBy(node)
  const held = [files ? counted(files, 'file') : null, folders ? counted(folders, 'folder') : null].filter(Boolean).join(' and ')
  return {
    title: 'Delete this folder?',
    body: held
      ? `Delete “${node.folder.name}” and the ${held} in it for good? Nobody can bring them back.`
      : `Delete the empty folder “${node.folder.name}”?`,
    action: held ? 'Delete for good' : 'Delete',
  }
}

/** The top of My files, the first place "Move to…" offers a file. */
export const TOP_OF_MY_FILES = 'My files, at the top'

/** The kinds of file My files keeps, by their extensions. */
const KEPT = MY_FILE_ACCEPT.split(',')

/** What of a choice of files, or a folder's, My files is sent: Word documents and Excel workbooks, each with the folder it is in below the one chosen; the rest named by their paths, and what a folder holds hidden left out unsaid. */
export function myFileUploads(items: UploadItem[]): { items: UploadItem[]; left: string[] } {
  const kept: UploadItem[] = []
  const left: string[] = []
  for (const item of items) {
    if (hidden(item)) continue
    const name = item.file.name.toLowerCase()
    if (KEPT.some((suffix) => name.endsWith(suffix))) kept.push(item)
    else left.push(pathOf(item))
  }
  return { items: kept, left }
}

/** How many of the files named a notice lists before it counts the rest. */
const NAMED = 5

/** Why files of a choice or a folder were not sent: My files keeps Word documents and Excel workbooks alone. */
export function leftOutWords(left: string[]): string {
  const named = left.slice(0, NAMED).join(', ')
  const more = left.length > NAMED ? ` and ${left.length - NAMED} more` : ''
  return `My files keeps Word documents and Excel workbooks, so ${left.length === 1 ? 'this was' : 'these were'} left out: ${named}${more}.`
}

/** Said while files go into My files. */
export function addingWords(total: number): string {
  return total === 1 ? 'Adding the file…' : `Adding ${total} files…`
}
