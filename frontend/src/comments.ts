// A document's comments and a project's activity as the page words them (backend/app/comments.py,
// backend/app/activity.py): a comment's words with the members it mentions, what the box under "@" offers and writes,
// where a thread's quote was, and each thing done in a project as a sentence.
//
// A comment keeps a mention as `<@id>`, which the page shows as the member's name as it is now. The box shows the
// name the member chose after "@" and writes the token for it as the comment is sent; a name typed by hand mentions
// nobody.

import type { ActivityEvent, ConnectorProvider, Mention, Mentionable, Thread } from './api.ts'
import type { Question } from './components/Confirm.tsx'
import { SERVICE_NAMES } from './connectors.ts'
import { KIND_LABELS } from './drafts.ts'
import { REVIEW_LABELS } from './reviews.ts'

/** Where a document's threads are kept. */
export const threadsKey = (documentId: number) => ['threads', documentId] as const
/** The members a comment in a project may mention. */
export const mentionableKey = (projectId: number) => ['mentionable', projectId] as const
/** Where a project's activity is kept, which a comment, a review or an upload makes stale. */
export const activityKey = (projectId: number) => ['activity', projectId] as const

/** The longest a comment may be (COMMENT_LENGTH in backend/app/schemas.py), and the words a thread quotes. */
export const COMMENT_LENGTH = 5_000
export const QUOTE_LENGTH = 500

const MENTION = /<@(\d+)>/g

/** A comment's words in order: text as written, and each member it mentions by name, null for an account gone. */
export type Piece = { text: string } | { mention: number; name: string | null }

export function pieces(text: string, mentions: Mention[]): Piece[] {
  const names = new Map(mentions.map((mention) => [mention.id, mention.name]))
  const out: Piece[] = []
  let last = 0
  for (const found of text.matchAll(MENTION)) {
    if (found.index > last) out.push({ text: text.slice(last, found.index) })
    const id = Number(found[1])
    out.push({ mention: id, name: names.get(id) ?? null })
    last = found.index + found[0].length
  }
  if (last < text.length) out.push({ text: text.slice(last) })
  return out
}

/** A mention as the page writes it: "@" and the name, "@someone" for an account gone. */
export const mentionWords = (name: string | null) => `@${name ?? 'someone'}`

/**
 * The words a member typed, with each "@name" of a member they chose from the list written as that member's token.
 * Longer names first, so that "@Ann Lee" is not taken for "@Ann"; a name is whole when no letter or digit follows it.
 */
export function stored(typed: string, chosen: Mentionable[]): string {
  let text = typed
  for (const member of [...chosen].sort((a, b) => b.name.length - a.name.length)) {
    const name = new RegExp(`@${escaped(member.name)}(?![\\p{L}\\p{N}])`, 'gu')
    text = text.replace(name, `<@${member.id}>`)
  }
  return text
}

/** A stored comment as its box shows it to be changed: each token as "@name", and the members it mentions, chosen already. */
export function typed(text: string, mentions: Mention[]): { text: string; chosen: Mentionable[] } {
  const chosen: Mentionable[] = []
  const written = text.replace(MENTION, (_, id: string) => {
    const name = mentions.find((mention) => mention.id === Number(id))?.name
    // A former member's mention stays a token, which the box sends back as it was.
    if (name == null) return `<@${id}>`
    if (!chosen.some((member) => member.id === Number(id))) chosen.push({ id: Number(id), name })
    return `@${name}`
  })
  return { text: written, chosen }
}

/** What the member is typing after an "@" at the caret, from where the "@" is: null when the caret is not in one. */
export function mentionAt(text: string, caret: number): { start: number; query: string } | null {
  const before = text.slice(0, caret)
  const found = /(?:^|\s)@([^@\n]{0,40})$/.exec(before)
  if (!found) return null
  return { start: caret - found[1].length - 1, query: found[1] }
}

/** The members whose name, or a word of it, starts with what was typed, the first `limit` of them. */
export function matching(members: Mentionable[], query: string, limit = 6): Mentionable[] {
  const asked = query.toLocaleLowerCase()
  return members
    .filter((member) => {
      const name = member.name.toLocaleLowerCase()
      return name.startsWith(asked) || name.split(/\s+/).some((word) => word.startsWith(asked))
    })
    .slice(0, limit)
}

/** The box's words once the member chose a name for the "@" at `start`: "@name " in place of what they typed, and the caret after it. */
export function withMention(text: string, start: number, caret: number, member: Mentionable): { text: string; caret: number } {
  const written = `@${member.name} `
  return { text: text.slice(0, start) + written + text.slice(caret), caret: start + written.length }
}

/** The words chosen in the document as a thread quotes them: on one line, cut short at a word with an ellipsis. */
export function quoted(words: string): string {
  const line = words.split(/\s+/).filter(Boolean).join(' ')
  if (line.length <= QUOTE_LENGTH) return line
  const cut = line.slice(0, QUOTE_LENGTH - 1)
  return `${cut.includes(' ') ? cut.slice(0, cut.lastIndexOf(' ')) : cut}…`
}

/** What a new thread is about, with the words its form shows for it: a passage, a block's, the words chosen; none of these for the whole document. */
export interface ThreadAbout {
  passage_id?: number
  block?: number
  quote?: string
  shown?: string
}

/** The words chosen in the document's view inside `root`, with the block they start in (each carries `data-block`); null for none chosen there. */
export function chosenIn(root: HTMLElement | null): ThreadAbout | null {
  const selection = window.getSelection()
  if (!root || !selection || selection.isCollapsed || selection.rangeCount === 0) return null
  const range = selection.getRangeAt(0)
  if (!root.contains(range.commonAncestorContainer)) return null
  const words = quoted(selection.toString())
  if (!words) return null
  const start = range.startContainer
  const element = start instanceof Element ? start : start.parentElement
  const block = element?.closest('[data-block]')?.getAttribute('data-block')
  return { quote: words, shown: words, ...(block != null && { block: Number(block) }) }
}

/** Where a thread's quote was: its page, and the version it was in once the document has another. */
export function quoteWhere(thread: Pick<Thread, 'page' | 'version' | 'passage_id'>, version: number): string | null {
  const parts: string[] = []
  if (thread.page != null) parts.push(`page ${thread.page}`)
  if (thread.version !== version) parts.push(`version ${thread.version}`)
  else if (thread.passage_id == null && thread.page == null) return null
  return parts.length ? parts.join(', ') : null
}

/** How many threads are open and resolved, as the panel's heading and its toggle say. */
export function threadCounts(threads: Thread[]): { open: number; resolved: number } {
  const resolved = threads.filter((thread) => thread.resolved_at != null).length
  return { open: threads.length - resolved, resolved }
}

export const openWords = (open: number) => (open ? `${open} open` : 'none open')

/** An item of a project's activity as a sentence: who, the words before what it is about, that thing's name (a link where it is there to open), and the words after. */
export interface ActivityWords {
  who: string | null
  before: string
  target: string | null
  after: string
}

const VERBS: Partial<Record<ActivityEvent['action'], string>> = {
  document_uploaded: 'uploaded',
  document_version_uploaded: 'gave a new version of',
  document_deleted: 'deleted',
  document_restored: 'restored',
}

export function activityWords(event: ActivityEvent): ActivityWords {
  const who = event.actor ?? (event.automation ? `The automation “${event.automation}”` : null)
  const name = event.filename
  const said = (before: string, target: string | null, after = ''): ActivityWords => ({
    who: who ?? 'A former member',
    before,
    target,
    after,
  })
  switch (event.action) {
    case 'document_uploaded':
      if (event.via === 'email') return { who: null, before: '', target: name, after: ' arrived by email' }
      if (event.via) return { who: null, before: '', target: name, after: ` arrived from ${service(event.via)}` }
      return said(` ${VERBS[event.action]} `, name)
    case 'document_version_uploaded':
    case 'document_deleted':
    case 'document_restored':
      return said(` ${VERBS[event.action]} `, name)
    case 'folder_deleted':
    case 'folder_restored':
      return said(
        ` ${event.action === 'folder_deleted' ? 'deleted' : 'restored'} the folder `,
        event.folder,
        event.documents ? ` with ${documentsWords(event.documents)}` : '',
      )
    case 'document_review_changed':
      if (event.review == null) return said(' took ', name, ' out of review')
      if (event.review === 'to_review') return said(' put ', name, ' in review')
      return said(` ${REVIEW_LABELS[event.review].toLocaleLowerCase()} `, name)
    case 'document_assigned':
      return event.assignee
        ? said(' assigned ', name, ` to ${event.assignee}`)
        : said(' left ', name, ' assigned to nobody')
    case 'comment_added':
      return said(event.reply ? ' replied on ' : ' commented on ', name)
    case 'thread_resolved':
      return said(' resolved a thread on ', name)
    case 'draft_created':
      return said(' asked for a draft: ', event.kind ? KIND_LABELS[event.kind] : 'a draft')
    case 'cross_checks_run':
      return said(' ran the cross-checks ', event.names.length ? event.names.join(', ') : null)
    case 'question_set_run':
      return said(' ran the question set ', event.name, event.questions != null ? `, ${questionsWords(event.questions)}` : '')
    case 'chat_shared':
      return said(' shared ', 'a chat', ' with the project')
  }
}

const documentsWords = (count: number) => `${count.toLocaleString()} document${count === 1 ? '' : 's'}`
const questionsWords = (count: number) => `${count.toLocaleString()} question${count === 1 ? '' : 's'}`
const service = (via: string) => SERVICE_NAMES[via as ConnectorProvider] ?? via

/** Consecutive uploads by the same member, or by the same way in, within this long, are one item of the activity. */
export const UPLOADS_TOGETHER = 10 * 60_000

/** A project's activity as its list shows it: each item alone, or a run of uploads together, newest first. */
export type ActivityItem = { event: ActivityEvent } | { uploads: ActivityEvent[] }

export function grouped(events: ActivityEvent[]): ActivityItem[] {
  const items: ActivityItem[] = []
  for (const event of events) {
    const last = items.at(-1)
    const previous = last && ('uploads' in last ? last.uploads.at(-1)! : last.event)
    if (
      event.action === 'document_uploaded' &&
      previous?.action === 'document_uploaded' &&
      previous.actor === event.actor &&
      previous.via === event.via &&
      Date.parse(previous.occurred_at) - Date.parse(event.occurred_at) <= UPLOADS_TOGETHER
    ) {
      if (last && 'uploads' in last) last.uploads.push(event)
      else items[items.length - 1] = { uploads: [previous, event] }
    } else {
      items.push({ event })
    }
  }
  return items
}

/** A run of uploads in a sentence: who, and how many. */
export function uploadsWords(uploads: ActivityEvent[]): string {
  const first = uploads[0]
  const count = documentsWords(uploads.length)
  if (first.via === 'email') return `${count} arrived by email`
  if (first.via) return `${count} arrived from ${service(first.via)}`
  return `${first.actor ?? 'A former member'} uploaded ${count}`
}

function escaped(words: string): string {
  return words.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

/** What deleting a comment asks first: for good, and its thread with it when it is the last. */
export function deleteQuestion(last: boolean): Question {
  return {
    title: 'Delete this comment?',
    body: last
      ? 'It is deleted for good, and its thread with it, since it is the thread’s last comment.'
      : 'It is deleted for good. The thread’s other comments stay.',
    action: 'Delete the comment',
  }
}
