// What the page says while the browser is offline or a request reached no server (`Unreachable`
// in api.ts): the header's line, an upload waiting for the connection or cut off on its way, and
// an answer followed on once the connection is back (chat.ts). Offline, TanStack Query holds the
// queries and mutations until the browser is back, so an upload begun then waits rather than
// fails, and the page says why.

import { Unreachable } from './api.ts'

export interface ConnectionLine {
  label: string
  /** The sentence the line's tooltip shows. */
  detail: string
}

const OFFLINE_DETAIL =
  'Your browser is offline. The page keeps what it showed; uploads start, and answers being written go on showing, once the connection is back.'

/** The header's line while the browser is offline, in place of the services' it cannot ask about; none online. */
export function describeConnection(online: boolean): ConnectionLine | null {
  return online ? null : { label: 'Offline', detail: OFFLINE_DETAIL }
}

/** What an answer says while the page waits for the connection to follow it on. */
export const ANSWER_OFFLINE = 'Your browser is offline. The answer goes on being written, and shows here once the connection is back.'

/** What the composer says while a question cannot be sent. */
export const QUESTION_OFFLINE = 'Your browser is offline: questions can be sent once the connection is back.'

const files = (n: number) => `${n.toLocaleString('en')} file${n === 1 ? '' : 's'}`

/** An upload begun offline, which TanStack Query starts once the browser is back. */
export function uploadWaiting(count: number): string {
  return `Your browser is offline: the upload of ${files(count)} starts once the connection is back.`
}

/**
 * An upload cut off on its way, whose files not sent the member may send again (`unsent` of
 * `UploadStopped` in workspace/uploads.ts): a file of the request cut off that the server stored
 * before the cut comes back as already in the project (`_already_present` in
 * backend/app/api/documents.py), so sending them again is safe. Null for an upload the server
 * refused, which the library's notice shows as it does any other refusal.
 */
export function uploadCutOff(error: Error | null, count: number): string | null {
  if (!(error instanceof Unreachable)) return null
  return `The upload of ${files(count)} did not get through. ${error.message}`
}
