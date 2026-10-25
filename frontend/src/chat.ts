// Chats as the Ask tab holds them, and the answers being followed. An answer is written by the
// server's worker and read here as a stream of events; the stream keeps flowing into the query
// cache after the user moves to another chat or tab, so it is tracked here rather than in a
// component, and a chat opened while its answer is still being written picks the stream up.
// Offline, the page waits for the connection and follows on from where it was; an answer it gave
// up on is followed again from its first event on the member's word, and a stopped or failed one
// is tried again in its place. The chat's last answer is written again on the member's word, from
// its question or the question they edited, and a chat starts about what was chosen for it.

import { onlineManager } from '@tanstack/react-query'
import type { QueryClient } from '@tanstack/react-query'
import { useSyncExternalStore } from 'react'
import { ApiError, api, isPlanLimit, streamEvents } from './api.ts'
import type { AskResult, Chat, ChatDetail, ChatEvent, ChatMessage, Draft, DraftOffer, MessageStatus, PlanRefusal, ScopeIn, Source, Usage } from './api.ts'

export interface Step {
  id: string
  name: string
  input: Record<string, unknown>
  // Undefined while the tool is still running.
  summary?: string
  is_error?: boolean
}

export interface Message {
  // Unset until the server has saved the message.
  id?: number
  role: 'user' | 'assistant'
  content: string
  steps: Step[]
  sources: Record<number, Source>
  // The draft the answer offers, and once pressed the one written from it.
  draft?: DraftOffer | null
  // The questions the answer suggests asking next.
  follow_ups?: string[]
  status: MessageStatus
  thinking?: boolean
  notice?: string | null
  error?: string | null
  // The error is the organisation's plan refusing the question, shown as the way past its limit.
  refusedByPlan?: PlanRefusal
  // What the answer used, once known: from the `done` event while it is followed, from the
  // saved row when the chat is loaded.
  usage?: Usage | null
  // The browser went offline while the answer was followed: it is followed on once it is back.
  offline?: boolean
  // The page gave up following the answer, which the worker may still be writing: the member
  // may follow it again.
  lost?: boolean
  // Length of `content` when the current model turn began, to discard a turn that is retried.
  turnStart: number
}

export interface ChatData extends Chat {
  messages: Message[]
}

export const chatKey = (chatId: number | null) => ['chat', chatId] as const

// How many times in a row a dropped stream is reconnected before the answer is shown as lost,
// and how long the first wait is; each wait is a second longer than the one before. A
// connection that brought an event starts the count over, so that a long answer outlasts the
// stop of every server it is followed from, as in a deployment of a new version. Drops while
// the browser is offline count for nothing: those are waited out.
const RECONNECTS = 5
const RECONNECT_WAIT = 1000
const LOST = 'The connection to the answer was lost. Reconnect to follow it again.'
// What a proxy answers while the server behind it is away, as between one server's stop and
// the next one's start, and what a server answers that cannot serve just now: a dropped
// connection too, unlike the other refusals.
const AWAY = new Set([502, 503, 504])

function fromSaved(message: ChatMessage): Message {
  return {
    ...message,
    sources: Object.fromEntries(message.sources.map((source) => [source.id, source])),
    thinking: message.role === 'assistant' && message.status === 'streaming',
    usage: message.usage.answer ?? null,
    turnStart: 0,
  }
}

/** A saved chat, ready to render. */
export function loadChat(chat: ChatDetail): ChatData {
  return { ...chat, messages: chat.messages.map(fromSaved) }
}

/** Mark an answer as finished: nothing in it is still running. */
export function settle(message: Message): Message {
  return {
    ...message,
    thinking: false,
    offline: false,
    status: message.status === 'streaming' ? 'done' : message.status,
    steps: message.steps.map((step) => (step.summary === undefined ? { ...step, summary: '' } : step)),
  }
}

/** An answer as it starts: nothing arrived yet. */
function empty(message: Message): Message {
  return {
    ...message,
    content: '',
    steps: [],
    sources: {},
    draft: null,
    follow_ups: [],
    status: 'streaming',
    thinking: true,
    notice: null,
    error: null,
    usage: null,
    offline: false,
    lost: false,
    turnStart: 0,
  }
}

export function applyEvent(message: Message, event: ChatEvent): Message {
  switch (event.type) {
    case 'text':
      return { ...message, content: message.content + event.text, thinking: false }
    case 'thinking':
      return { ...message, thinking: true }
    case 'retry':
      return { ...message, content: message.content.slice(0, message.turnStart) }
    case 'restart':
      // The run that wrote the answer so far was lost; another starts it over.
      return empty(message)
    case 'tool_call':
      return {
        ...message,
        thinking: false,
        steps: [...message.steps, { id: event.id, name: event.name, input: event.input }],
      }
    case 'tool_result':
      return {
        ...message,
        turnStart: message.content.length,
        steps: message.steps.map((step) =>
          step.id === event.id ? { ...step, summary: event.summary, is_error: event.is_error } : step,
        ),
      }
    case 'sources':
      return {
        ...message,
        sources: { ...message.sources, ...Object.fromEntries(event.sources.map((s) => [s.id, s])) },
      }
    case 'draft':
      return {
        ...message,
        draft: { kind: event.kind, instructions: event.instructions, documents: event.documents, written: false, draft_id: null },
      }
    case 'follow_ups':
      return { ...message, follow_ups: event.questions }
    case 'notice':
      return { ...message, notice: event.message }
    case 'error':
      return { ...message, thinking: false, error: event.message }
    case 'done':
      return { ...message, thinking: false, usage: event.usage }
    case 'end':
      return { ...message, thinking: false, status: event.status }
  }
}

interface Following {
  controller: AbortController
  // Unset while the request that saves the question, or tries the answer again, is on its way: a
  // stop meanwhile reaches the server once the answer is saved, with the id it then has.
  messageId: number | null
}

// The answers being followed, by chat id.
const running = new Map<number, Following>()
// The stops on their way to the server, by chat id: a try of the stopped answer waits for its
// stop, or the server would find the answer still being written.
const stopping = new Map<number, Promise<unknown>>()
const listeners = new Set<() => void>()

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

function changed() {
  for (const listener of listeners) listener()
}

/** Whether an answer is being followed into the chat. */
export function useStreaming(chatId: number | null): boolean {
  return useSyncExternalStore(subscribe, () => chatId != null && running.has(chatId))
}

/**
 * A project's chats as its list holds them: those the member pinned, whoever started them, then
 * the rest parted by starter, the member's own, which they ask in and share, and the ones other
 * members shared with the project, which they read. Each part keeps the server's order: the
 * newest pin first, then the latest question first.
 */
export function splitChats(chats: Chat[], userId: number): { pinned: Chat[]; own: Chat[]; shared: Chat[] } {
  const rest = chats.filter((chat) => !chat.pinned)
  return {
    pinned: chats.filter((chat) => chat.pinned),
    own: rest.filter((chat) => chat.user_id === userId),
    shared: rest.filter((chat) => chat.user_id !== userId),
  }
}

/** The member's own chat whose question was asked last, pinned or not: the one that opens without a chat in the URL. */
export function latestOwn(chats: Chat[], userId: number): Chat | undefined {
  return chats
    .filter((chat) => chat.user_id === userId)
    .reduce<Chat | undefined>((latest, chat) => (latest && latest.updated_at >= chat.updated_at ? latest : chat), undefined)
}

/**
 * The list with the chat pinned or not, in the place the server will give it: a new pin first, a
 * pinned chat where it is, and one unpinned among the others by its latest question.
 */
export function withPin(chats: Chat[], chatId: number, pinned: boolean): Chat[] {
  const chat = chats.find((item) => item.id === chatId)
  if (!chat || chat.pinned === pinned) return chats
  const changed = { ...chat, pinned }
  const rest = chats.filter((item) => item.id !== chatId)
  if (pinned) return [changed, ...rest]
  const after = (item: Chat) =>
    !item.pinned && (item.updated_at < chat.updated_at || (item.updated_at === chat.updated_at && item.id < chat.id))
  const at = rest.findIndex(after)
  return at === -1 ? [...rest, changed] : [...rest.slice(0, at), changed, ...rest.slice(at)]
}

/** Whether the chat's last message is an answer still being written. */
export function isStreaming(chat: ChatData | undefined): boolean {
  const last = chat?.messages[chat.messages.length - 1]
  return last?.role === 'assistant' && last.status === 'streaming'
}

/**
 * Whether the answer, the last of its chat, may be tried again: it was stopped or failed. Not one
 * the page lost, which the worker may still be writing and is followed again instead, nor one the
 * plan refused, whose notice offers what lifts the limit.
 */
export function mayRetry(message: Message): boolean {
  return (
    message.role === 'assistant' &&
    (message.status === 'stopped' || message.status === 'failed') &&
    !message.lost &&
    !message.refusedByPlan
  )
}

/**
 * Whether the answer, the last of its chat, may be written again: it is done. Not one the page
 * lost, nor one the plan refused, whose notice offers what lifts the limit; a stopped or failed
 * one is tried again instead, which counts no question.
 */
export function mayRegenerate(message: Message): boolean {
  return message.role === 'assistant' && message.status === 'done' && !message.lost && !message.refusedByPlan
}

/**
 * The chat's last question, which its member may edit and have answered again in its place: none
 * while its answer is written, or once the page lost it.
 */
export function editableQuestion(messages: Message[]): Message | null {
  const [question, answer] = messages.slice(-2)
  if (question?.role !== 'user' || answer?.role !== 'assistant') return null
  return answer.status === 'streaming' || answer.lost ? null : question
}

/**
 * The questions to offer under the chat's last answer to ask next: those it suggests, once it is
 * done without an error; none while anything comes after it.
 */
export function followUpsOf(messages: Message[]): string[] {
  const last = messages[messages.length - 1]
  if (last === undefined || last.role !== 'assistant' || last.status !== 'done' || last.error) return []
  return last.follow_ups ?? []
}

/** End the answer with what arrived so far, on the server and here. */
export function stop(chatId: number) {
  const following = running.get(chatId)
  if (!following) return
  if (following.messageId != null) stopOnServer(chatId, following.messageId)
  following.controller.abort()
}

function stopOnServer(chatId: number, messageId: number) {
  const stopped = api
    .stopAnswer(chatId, messageId)
    .catch(() => {
      // The abort settles it here; a stop the server did not hear of ends with its run.
    })
    .finally(() => {
      if (stopping.get(chatId) === stopped) stopping.delete(chatId)
    })
  stopping.set(chatId, stopped)
}

/**
 * Take the chat's answer as followed before the request that saves it is sent, so that the page
 * shows it being written and a stop meanwhile is heard (`following`).
 */
function begin(chatId: number): Following {
  const following: Following = { controller: new AbortController(), messageId: null }
  running.set(chatId, following)
  changed()
  return following
}

/** Let go of an answer whose request was refused: there is nothing to follow. */
function letGo(chatId: number) {
  running.delete(chatId)
  changed()
}

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

/** Wait until the browser is back online, or the following is stopped. */
function backOnline(signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (onlineManager.isOnline() || signal.aborted) return resolve()
    const done = () => {
      stopListening()
      signal.removeEventListener('abort', done)
      resolve()
    }
    const stopListening = onlineManager.subscribe((online) => {
      if (online) done()
    })
    signal.addEventListener('abort', done)
  })
}

/** Following given up after the drops in a row: the member may take it up again. */
class Lost extends Error {}

type Edit = (change: (message: Message) => Message) => void

/**
 * Read the answer's events into the message until the server says it is settled, from the start
 * or past the event with id `after`.
 */
async function follow(chatId: number, messageId: number, update: Edit, signal: AbortSignal, after: number | null) {
  let drops = 0
  for (;;) {
    // Stopped before it was followed, while its question was being saved.
    signal.throwIfAborted()
    // Each connection ends with the following, and as the browser goes offline: one the network
    // took away may otherwise wait for good without an error.
    const connection = new AbortController()
    const close = () => connection.abort()
    signal.addEventListener('abort', close)
    const stopListening = onlineManager.subscribe((online) => {
      if (!online) close()
    })
    try {
      for await (const { seq, event } of streamEvents(chatId, messageId, after, connection.signal)) {
        if (seq != null) after = seq
        drops = 0
        update((message) => applyEvent(message, event))
        if (event.type === 'end') return
      }
    } catch (error) {
      // A refusal (the chat is gone, the session ended) is final; a dropped connection is not.
      if (signal.aborted || (error instanceof ApiError && !AWAY.has(error.status))) throw error
    } finally {
      signal.removeEventListener('abort', close)
      stopListening()
    }
    if (!onlineManager.isOnline()) {
      // The drop is the browser's, not the server's: waited out however long it takes, while
      // the worker goes on writing the answer, and followed on from the last event seen.
      update((message) => ({ ...message, offline: true }))
      await backOnline(signal)
      signal.throwIfAborted()
      update((message) => ({ ...message, offline: false }))
      drops = 0
      continue
    }
    if (drops >= RECONNECTS) throw new Lost(LOST)
    drops++
    await wait(RECONNECT_WAIT * drops)
  }
}

/**
 * Follow the answer with id `messageId` into the chat's query data until it is settled, from the
 * start or past the event with id `after`. A stop heard while the request that saved it was on its
 * way reaches the server now.
 */
async function run(
  queryClient: QueryClient,
  chatId: number,
  following: Following,
  messageId: number,
  update: Edit,
  after: number | null = null,
) {
  const { controller } = following
  following.messageId = messageId
  running.set(chatId, following)
  changed()
  if (controller.signal.aborted) stopOnServer(chatId, messageId)
  try {
    await follow(chatId, messageId, update, controller.signal, after)
  } catch (error) {
    const stopped = controller.signal.aborted
    update((message) => ({
      ...message,
      ...(stopped
        ? { notice: 'Stopped.', status: 'stopped' }
        : { error: (error as Error).message, status: 'failed', lost: error instanceof Lost }),
    }))
  } finally {
    update(settle)
    running.delete(chatId)
    changed()
    // The first question names the chat, and every question moves it up the list.
    queryClient.invalidateQueries({ queryKey: ['chats'] })
  }
}

function editor(queryClient: QueryClient, chatId: number) {
  const edit = (change: (messages: Message[]) => Message[]) =>
    queryClient.setQueryData<ChatData>(chatKey(chatId), (chat) => chat && { ...chat, messages: change(chat.messages) })
  const last: Edit = (change) =>
    edit((messages) => [...messages.slice(0, -1), change(messages[messages.length - 1])])
  const byId =
    (messageId: number): Edit =>
    (change) =>
      edit((messages) => messages.map((message) => (message.id === messageId ? change(message) : message)))
  return { edit, last, byId }
}

/** A refusal to answer, shown where the answer is: a plan's limit as the way past it. */
function refused(error: unknown): Partial<Message> {
  return {
    thinking: false,
    error: (error as Error).message,
    refusedByPlan: isPlanLimit(error) ? { message: error.message, limit: error.limit } : undefined,
    status: 'failed',
  }
}

/** Send a question and follow its answer into the chat's query data until it ends or is stopped. */
export async function ask(queryClient: QueryClient, chatId: number, question: string) {
  const { edit, last, byId } = editor(queryClient, chatId)
  edit((messages) => [
    ...messages,
    { role: 'user', content: question, steps: [], sources: {}, status: 'done', turnStart: 0 },
    { role: 'assistant', content: '', steps: [], sources: {}, status: 'streaming', thinking: true, turnStart: 0 },
  ])
  const following = begin(chatId)
  let saved: AskResult
  try {
    saved = await api.ask(chatId, question)
  } catch (error) {
    // A refusal (another answer still running, too many in flight, the plan's questions used
    // up) shows where the answer would be; the question stays on screen, to be tried again.
    letGo(chatId)
    last((message) => ({ ...message, ...refused(error) }))
    queryClient.invalidateQueries({ queryKey: ['chats'] })
    return
  }
  // The saved pair replaces the provisional one, so the answer can be followed by its id.
  edit((messages) => [...messages.slice(0, -2), fromSaved(saved.question), fromSaved(saved.answer)])
  await run(queryClient, chatId, following, saved.answer.id, byId(saved.answer.id))
}

/**
 * Follow an answer the server is still writing, after a reload, from another tab or once the
 * page lost it: its events are read from the start, so the message starts empty.
 */
export function resume(queryClient: QueryClient, chatId: number, messageId: number) {
  if (running.has(chatId)) return
  const update = editor(queryClient, chatId).byId(messageId)
  update(empty)
  void run(queryClient, chatId, { controller: new AbortController(), messageId }, messageId, update)
}

/**
 * Try the chat's last answer again in its place, once it was stopped or failed (`mayRetry`): the
 * server empties it and its worker writes it anew, followed from past the events the earlier tries
 * left. An answer the server never saved, its question refused, is asked again instead. A refused
 * try leaves the answer as it was, with the refusal, to be tried again later.
 */
export async function retry(queryClient: QueryClient, chatId: number) {
  if (running.has(chatId)) return
  const { edit, byId } = editor(queryClient, chatId)
  const messages = queryClient.getQueryData<ChatData>(chatKey(chatId))?.messages ?? []
  const answer = messages[messages.length - 1]
  const question = messages[messages.length - 2]
  if (answer?.role !== 'assistant' || question?.role !== 'user') return
  if (answer.id == null) {
    edit((all) => all.slice(0, -2))
    return ask(queryClient, chatId, question.content)
  }
  const update = byId(answer.id)
  const following = begin(chatId)
  update(empty)
  let after: number | null
  try {
    await stopping.get(chatId)
    after = (await api.retryAnswer(chatId, answer.id)).last_event_id
  } catch (error) {
    letGo(chatId)
    update(() => ({ ...answer, ...refused(error), status: answer.status }))
    return
  }
  await run(queryClient, chatId, following, answer.id, update, after)
}

/**
 * Write the chat's last answer again in its place (`mayRegenerate`), from its question or from
 * `question` in its place (`editableQuestion`): the server empties it, counting a question, and its
 * worker writes it anew, followed from past the events the earlier tries left. An answer the
 * server never saved, its question refused, is asked again instead. A refusal puts the question and
 * the answer back as they were, with the refusal.
 */
export async function regenerate(queryClient: QueryClient, chatId: number, question?: string) {
  if (running.has(chatId)) return
  const { edit, byId } = editor(queryClient, chatId)
  const messages = queryClient.getQueryData<ChatData>(chatKey(chatId))?.messages ?? []
  const [asked, answer] = messages.slice(-2)
  if (asked?.role !== 'user' || answer?.role !== 'assistant') return
  if (asked.id == null || answer.id == null) {
    edit((all) => all.slice(0, -2))
    return ask(queryClient, chatId, question ?? asked.content)
  }
  const askedAs = byId(asked.id)
  const update = byId(answer.id)
  const following = begin(chatId)
  if (question !== undefined) askedAs((message) => ({ ...message, content: question }))
  update(empty)
  let after: number | null
  try {
    await stopping.get(chatId)
    const written = await api.regenerateAnswer(chatId, answer.id, question)
    askedAs(() => fromSaved(written.question))
    after = written.last_event_id
  } catch (error) {
    letGo(chatId)
    askedAs(() => asked)
    update(() => ({ ...answer, ...refused(error), status: answer.status }))
    return
  }
  await run(queryClient, chatId, following, answer.id, update, after)
}

/** Put a chat just made for its first question where the page reads it: its own data and the list's top. */
export function started(queryClient: QueryClient, chat: Chat, question: string) {
  queryClient.setQueryData<ChatData>(chatKey(chat.id), { ...chat, messages: [] })
  queryClient.setQueryData<Chat[]>(['chats', chat.project_id], (chats) => [{ ...chat, title: question }, ...(chats ?? [])])
}

/**
 * Start a chat about what `scope` names with its first question, and follow its answer into it, as
 * the library's "Summarise this folder" and "Compare" do; the chat's id once the server made it,
 * with the question and its answer begun in it.
 */
export async function startChat(queryClient: QueryClient, projectId: number, scope: ScopeIn, question: string): Promise<number> {
  const chat = await api.createChat(projectId, scope)
  started(queryClient, chat, question)
  void ask(queryClient, chat.id, question)
  return chat.id
}

/**
 * Have the draft the answer offers written into the project's drafts, and keep in the chat that it was, with the draft
 * written from it; pressed again, the server answers that draft.
 */
export async function writeOffered(queryClient: QueryClient, chatId: number, messageId: number): Promise<Draft> {
  const draft = await api.writeOfferedDraft(chatId, messageId)
  editor(queryClient, chatId).byId(messageId)((message) =>
    message.draft ? { ...message, draft: { ...message.draft, written: true, draft_id: draft.id } } : message,
  )
  return draft
}
