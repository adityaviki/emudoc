// What a message from the help menu carries besides the member's words, and whom it goes to
// (backend/app/api/help.py): SUPPORT_EMAIL where the server names one, else the workspace's owners
// and admins. It carries where they were, as the page's path and the ids its query names,
// and the request the page last saw refused or failed. Never the query's words, which hold a
// search, nor anything else the page shows. Each part keeps to what the server takes
// (FeedbackIn in backend/app/schemas.py), since a part outside it would have the message refused.

import type { FailedRequest, Feedback, FeedbackKind } from '../api.ts'
import { pagePath } from '../errors.ts'

/** What the member writes about, as the menu offers it, with what to tell of each. */
export const FEEDBACK_KINDS: { value: FeedbackKind; label: string; prompt: string }[] = [
  { value: 'problem', label: 'A problem', prompt: 'What did you do, what happened, and what did you expect?' },
  { value: 'question', label: 'A question', prompt: 'What would you like to know?' },
  { value: 'idea', label: 'An idea', prompt: 'What would make Emudoc work better for you?' },
]

// The longest message (MAX_FEEDBACK_CHARS in backend/app/schemas.py).
export const MAX_FEEDBACK_CHARS = 10_000
// The query's parts that name what is open (src/workspace/url.ts), kept while each is an id,
// `new` (a chat not yet asked) or nothing (the trash): never `q`, a search's words.
const KEPT = ['folder', 'chat', 'passage', 'block', 'page', 'trash']
const ID = /^(?:\d{1,12}|new|)$/

/** Where the member is: the path (src/errors.ts), with the ids its query names. */
export function feedbackPage(pathname: string, search: string): string {
  const params = new URLSearchParams(search)
  const kept = KEPT.flatMap((key) => {
    const value = params.get(key)
    return value !== null && ID.test(value) ? [`${key}=${value}`] : []
  })
  const path = pagePath(pathname)
  return kept.length ? `${path}?${kept.join('&')}` : path
}

/** The message to send, from what the member chose and typed on the page at `pathname` and `search`. */
export function feedbackOf(kind: FeedbackKind, message: string, pathname: string, search: string, failed: FailedRequest | null): Feedback {
  return { kind, message: message.trim(), page: feedbackPage(pathname, search), failed }
}

/** What the menu says is sent with the words, so that the member knows before they send. */
export function attachedWords(failed: FailedRequest | null): string {
  const request = failed ? ', and the reference of the last request that failed here' : ''
  return `Sent with your name and address, this page's address and Emudoc's version${request}, so that whoever answers can find what you met.`
}

/** The form's heading: to SUPPORT_EMAIL, the server's address for help, or else to the workspace's owners and admins. */
export function writeHeading(supportEmail: string | null): string {
  return supportEmail ? 'Write to support' : "Write to your workspace's admins"
}

/** Where a message went (`FeedbackSent.to`): the address, or the workspace's owners and admins for none. */
export function sentTo(to: string | null): string {
  return to ?? "your workspace's admins"
}
