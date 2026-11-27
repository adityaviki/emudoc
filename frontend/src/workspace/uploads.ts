// An upload as the library sends it: what cannot go is left out before anything is sent (a type
// Emudoc does not read, a file larger than one may be, what a folder holds hidden), and the rest
// goes in as few requests as the server's limits allow (`UploadLimits`, from GET /api/status),
// one after another, so that a folder of more files than one request may carry goes whole. Each
// request is one upload on the server, with its own notice (backend/app/notices.py). How far it
// has gone, per file and as a whole, is the panel's (UploadPanel.tsx), which alone follows it,
// and once it is sent, how far its documents are indexed, asked of the server for its uploads
// together (GET /api/projects/{id}/uploads) until none is being indexed or waits for a retry.

import { ACCEPTED_EXTENSIONS } from '../api.ts'
import type { Document, Progress, UploadItem, UploadLimits, UploadResult, UploadStatus } from '../api.ts'
import { formatSize } from '../format.ts'
import { REFRESH, askAgainAt } from './listing.ts'

type Rejected = UploadResult['rejected'][number]

const MB = 1024 * 1024

/** What a deployment allows until its status says: the server's defaults (backend/app/config.py). */
export const DEFAULT_LIMITS: UploadLimits = { files: 100, request_bytes: 500 * MB, file_bytes: 100 * MB, extensions: ACCEPTED_EXTENSIONS }

// The extension a photo the camera took is saved with, by its type; a phone names every one
// image.jpg, so the page names it by when it was taken (`photoName`).
const PHOTO_TYPES: Record<string, string> = { 'image/jpeg': '.jpg', 'image/png': '.png', 'image/webp': '.webp', 'image/heic': '.heic', 'image/heif': '.heif' }

/** The name a photo taken from the library is uploaded under, "Photo 2026-10-25 at 14.32.07.jpg", in the browser's own time. */
export function photoName(file: File, at: Date): string {
  const two = (n: number) => String(n).padStart(2, '0')
  const day = `${at.getFullYear()}-${two(at.getMonth() + 1)}-${two(at.getDate())}`
  const time = `${two(at.getHours())}.${two(at.getMinutes())}.${two(at.getSeconds())}`
  return `Photo ${day} at ${time}${PHOTO_TYPES[file.type] ?? suffixOf(file.name)}`
}

// Files a folder holds that its owner does not see: hidden by a leading dot, or Windows's own.
const SYSTEM_FILES = new Set(['thumbs.db', 'ehthumbs.db', 'desktop.ini'])

/** A file's name with the folders it is in, as the server names one it rejects. */
export const pathOf = ({ file, folder }: UploadItem) => (folder ? `${folder}/${file.name}` : file.name)

/** Whether an item is one of the files a folder holds that its owner does not see, which an upload leaves out unsaid. */
export const hidden = (item: UploadItem) =>
  item.folder !== '' &&
  (SYSTEM_FILES.has(item.file.name.toLowerCase()) || pathOf(item).split('/').some((name) => name.startsWith('.')))

// As Python's `Path.suffix`: a name that starts with its only dot has none.
const suffixOf = (name: string) => {
  const dot = name.lastIndexOf('.')
  return dot > 0 ? name.slice(dot).toLowerCase() : ''
}

// The server's own sentences for the files it would reject (backend/app/api/documents.py).
const UNSUPPORTED = `Unsupported type. Use ${[...ACCEPTED_EXTENSIONS].sort().join(', ')}.`
const UNREAD = 'Your browser could not read it from your computer.'

/**
 * The files to send, and those left out with why, named by their paths: of a type Emudoc does not
 * read, larger than one file or one request may be, or unreadable (`unread`, a dropped folder's).
 * What a folder holds hidden is left out unsaid.
 */
export function screen(items: UploadItem[], limits: UploadLimits, unread: string[] = []): { items: UploadItem[]; left: Rejected[] } {
  const largest = Math.min(limits.file_bytes, limits.request_bytes)
  const left: Rejected[] = unread.map((filename) => ({ filename, reason: UNREAD }))
  const kept: UploadItem[] = []
  for (const item of items) {
    if (hidden(item)) continue
    const reason = !ACCEPTED_EXTENSIONS.includes(suffixOf(item.file.name))
      ? UNSUPPORTED
      : item.file.size > largest
        ? `Larger than ${Math.floor(largest / MB)} MB.`
        : null
    if (reason) left.push({ filename: pathOf(item), reason })
    else kept.push(item)
  }
  return { items: kept, left }
}

/** The items in the requests that carry them, in order, each of at most `files` files and `request_bytes` bytes. */
export function batches(items: UploadItem[], limits: UploadLimits): UploadItem[][] {
  const requests: UploadItem[][] = []
  let bytes = 0
  for (const item of items) {
    const last = requests.at(-1)
    if (last && last.length < limits.files && bytes + item.file.size <= limits.request_bytes) {
      last.push(item)
      bytes += item.file.size
    } else {
      requests.push([item])
      bytes = item.file.size
    }
  }
  return requests
}

/** How far an upload has gone. */
export interface UploadState {
  items: UploadItem[]
  /** How many of the items, from the first, are in requests the server has answered. */
  answered: number
  /** How many come after them in the request on its way. */
  sending: number
  /** The bytes of that request's body sent and in all, its multipart lines included; 0 of 0 until the browser says. */
  loaded: number
  total: number
}

/** What an upload's requests stored and rejected over all of them, and the uploads they were on the server. */
export interface Sent {
  documents: Document[]
  rejected: Rejected[]
  uploads: number[]
}

/** Why an upload stopped before its last request: the error, what went in before it, and the files not sent. */
export class UploadStopped extends Error {
  error: Error
  result: Sent
  unsent: UploadItem[]

  constructor(error: Error, result: Sent, unsent: UploadItem[]) {
    super(error.message)
    this.error = error
    this.result = result
    this.unsent = unsent
  }
}

/** Whether a request was stopped by the page, as the panel's Stop does. */
export const isAbort = (error: unknown) => error instanceof DOMException && error.name === 'AbortError'

/**
 * Sends the items request by request through `send`, reporting how far each has gone, and
 * answers what the server stored and rejected over all of them. A request that fails stops the
 * rest: `UploadStopped`, with that request's files and the later ones as `unsent`.
 */
export async function sendInBatches(
  items: UploadItem[],
  limits: UploadLimits,
  send: (batch: UploadItem[], onProgress: Progress) => Promise<UploadResult>,
  report: (state: UploadState) => void,
): Promise<Sent> {
  const result: Sent = { documents: [], rejected: [], uploads: [] }
  let answered = 0
  for (const batch of batches(items, limits)) {
    const state: UploadState = { items, answered, sending: batch.length, loaded: 0, total: 0 }
    report(state)
    try {
      const done = await send(batch, (loaded, total) => report({ ...state, loaded, total }))
      result.documents.push(...done.documents)
      result.rejected.push(...done.rejected)
      if (done.upload_id != null) result.uploads.push(done.upload_id)
    } catch (error) {
      throw new UploadStopped(error instanceof Error ? error : new Error(String(error)), result, items.slice(answered))
    }
    answered += batch.length
  }
  return result
}

const bytesOf = (items: UploadItem[]) => items.reduce((sum, { file }) => sum + file.size, 0)

/**
 * Each file's share sent, 0 to 1, in order. A request's body holds its files one after another,
 * so the share of its body sent is taken from them in turn, its multipart lines spread over them.
 */
export function sharesOf(state: UploadState): number[] {
  const end = state.answered + state.sending
  const body = state.total ? state.loaded / state.total : 0
  let left = body * bytesOf(state.items.slice(state.answered, end))
  return state.items.map(({ file }, index) => {
    if (index < state.answered) return 1
    if (index >= end || body === 0) return 0
    const share = file.size ? Math.min(1, left / file.size) : left > 0 || body === 1 ? 1 : 0
    left = Math.max(0, left - file.size)
    return share
  })
}

/** The upload as a whole: the share of its bytes sent, as a percentage, and whether the server is storing the request on its way. */
export function wholeOf(state: UploadState): { percent: number; sent: number; storing: boolean; line: string; bytes: string } {
  const shares = sharesOf(state)
  const all = bytesOf(state.items)
  const done = state.items.reduce((sum, { file }, index) => sum + file.size * shares[index], 0)
  const sent = shares.filter((share) => share === 1).length
  const storing = state.total > 0 && state.loaded >= state.total
  const count = state.items.length
  const percent = Math.floor(100 * (all ? done / all : sent / count))
  const line = storing && sent === count
    ? `Sent ${count === 1 ? 'the file' : `all ${count.toLocaleString('en')} files`}; Emudoc is storing ${count === 1 ? 'it' : 'them'}.`
    : `Sent ${sent.toLocaleString('en')} of ${files(count)}.`
  return { percent, sent, storing, line, bytes: `${formatSize(Math.round(done))} of ${formatSize(all)}` }
}

/** One file's share in words. */
export const shareWords = (share: number) => (share === 1 ? 'Sent' : share === 0 ? 'Waiting' : `${Math.floor(100 * share)} %`)

export const files = (n: number) => `${n.toLocaleString('en')} file${n === 1 ? '' : 's'}`

/** What the library says while an upload is under way and another is dropped or chosen. */
export const UPLOAD_UNDER_WAY = 'An upload is under way. Upload more once it is done.'

/** What it says of a folder with nothing to send in it. */
export const NOTHING_TO_UPLOAD = 'There is nothing to upload: the folder holds no files, or hidden ones alone.'

/** What the library says once the member stopped an upload. */
export const uploadStopped = (unsent: number) =>
  `The upload was stopped: the last ${files(unsent)} did not go in.`

/** The followed uploads' query, of every upload of the project followed. */
export const followedKey = (projectId: number) => ['uploads', projectId] as const

/** How many uploads one request asks about (MAX_FOLLOWED in backend/app/api/documents.py). */
export const FOLLOWED_A_REQUEST = 100

/** The uploads' ids in the requests that ask about them. */
export const followedBatches = (uploads: number[]): number[][] =>
  Array.from({ length: Math.ceil(uploads.length / FOLLOWED_A_REQUEST) }, (_, n) =>
    uploads.slice(n * FOLLOWED_A_REQUEST, (n + 1) * FOLLOWED_A_REQUEST),
  )

const COUNTS = ['documents', 'ready', 'partly_indexed', 'indexing', 'retrying', 'failed'] as const

/** The answers about the uploads of each request, as one: the counts added, the soonest retry, the failures one after another. */
export function summed(statuses: UploadStatus[]): UploadStatus {
  const whole: UploadStatus = {
    documents: 0,
    ready: 0,
    partly_indexed: 0,
    indexing: 0,
    retrying: 0,
    retry_at: null,
    failed: 0,
    failures: [],
  }
  for (const status of statuses) {
    for (const count of COUNTS) whole[count] += status[count]
    if (status.retry_at && (!whole.retry_at || new Date(status.retry_at) < new Date(whole.retry_at))) {
      whole.retry_at = status.retry_at
    }
    whole.failures.push(...status.failures)
  }
  return whole
}

/** Whether none of an upload's documents is being indexed or waits for a retry. */
export const settled = (status: UploadStatus) => status.indexing === 0 && status.retrying === 0

/**
 * When to ask again how far an upload is indexed, in milliseconds, as the library asks about a
 * folder (`refreshAfter` in listing.ts): every REFRESH while a document is being indexed, else
 * just after the soonest retry is due; never once it is settled.
 */
export function followAfter(status: UploadStatus, now = Date.now()): number | false {
  if (settled(status)) return false
  if (status.indexing > 0 || !status.retry_at) return REFRESH
  return askAgainAt(new Date(status.retry_at).getTime(), now)
}

const count = (n: number) => n.toLocaleString('en')

/**
 * Where an upload's documents stand, in words: its title, "12 of 40 ready; 26 being indexed; 2
 * could not be indexed.", the share of them settled one way or the other, and the failures the
 * list leaves out.
 */
export function standingOf(status: UploadStatus, into: string) {
  const done = settled(status)
  const { documents, ready, partly_indexed: partly, indexing, retrying, failed } = status
  const title = done ? `${files(documents)} uploaded into ${into}` : `Indexing ${files(documents)} uploaded into ${into}`
  const parts = documents
    ? [
        `${count(ready)} of ${count(documents)} ready${partly ? `, ${count(partly)} of them partly indexed` : ''}`,
        indexing ? `${count(indexing)} being indexed` : '',
        retrying ? `${count(retrying)} waiting for a retry` : '',
        failed ? `${count(failed)} could not be indexed` : '',
      ]
    : ['They are all in the trash now']
  const unlisted = failed - status.failures.length
  return {
    title,
    line: `${parts.filter(Boolean).join('; ')}.`,
    percent: documents ? Math.floor((100 * (ready + failed)) / documents) : 100,
    settled: done,
    more: unlisted > 0 ? `${count(unlisted)} more could not be indexed: the library marks each.` : null,
  }
}

/**
 * The state's store: it changes with every few kilobytes sent, which only the panel follows, so
 * that the library around it is not drawn again for each.
 */
export class UploadProgress {
  private state: UploadState | null = null
  private listeners = new Set<() => void>()

  get = () => this.state

  set = (state: UploadState | null) => {
    this.state = state
    for (const listener of this.listeners) listener()
  }

  subscribe = (listener: () => void) => {
    this.listeners.add(listener)
    return () => {
      this.listeners.delete(listener)
    }
  }
}
