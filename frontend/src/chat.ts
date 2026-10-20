// Chats as the Ask tab holds them, and the answers being followed. An answer is written by the
// server's worker and read here as a stream of events; the stream keeps flowing into the query
// cache after the user moves to another chat or tab, so it is tracked here rather than in a
// component, and a chat opened while its answer is still being written picks the stream up.

import type { QueryClient } from '@tanstack/react-query'
import { useSyncExternalStore } from 'react'
import { ApiError, api, isPlanLimit, streamEvents } from './api.ts'
import type { Chat, ChatDetail, ChatEvent, ChatMessage, MessageStatus, Source, Usage } from './api.ts'

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
  status: MessageStatus
  thinking?: boolean
  notice?: string | null
  error?: string | null
  // The error is the organisation's plan refusing the question, shown as the way to upgrade.
  refusedByPlan?: boolean
  // What the answer used, once known: from the `done` event while it is followed, from the
  // saved row when the chat is loaded.
  usage?: Usage | null
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
// stop of every server it is followed from, as in a deployment of a new version.
const RECONNECTS = 5
const RECONNECT_WAIT = 1000
const LOST = 'The connection to the answer was lost. Reload the page to see it.'
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
    status: message.status === 'streaming' ? 'done' : message.status,
    steps: message.steps.map((step) => (step.summary === undefined ? { ...step, summary: '' } : step)),
  }
}

/** An answer as it starts: nothing arrived yet. */
function empty(message: Message): Message {
  return { ...message, content: '', steps: [], sources: {}, thinking: true, notice: null, error: null, usage: null, turnStart: 0 }
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
  // Unset until the question was saved and the answer has an id to stop.
  messageId: number | null
}

// The answers being followed, by chat id.
const running = new Map<number, Following>()
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
 * A project's chats as its list holds them, parted by starter: the member's own, which they ask
 * in and share, and the ones other members shared with the project, which they read. Each part
 * keeps the server's order, the latest question first.
 */
export function splitChats(chats: Chat[], userId: number): { own: Chat[]; shared: Chat[] } {
  return {
    own: chats.filter((chat) => chat.user_id === userId),
    shared: chats.filter((chat) => chat.user_id !== userId),
  }
}

/** Whether the chat's last message is an answer still being written. */
export function isStreaming(chat: ChatData | undefined): boolean {
  const last = chat?.messages[chat.messages.length - 1]
  return last?.role === 'assistant' && last.status === 'streaming'
}

/** End the answer with what arrived so far, on the server and here. */
export function stop(chatId: number) {
  const following = running.get(chatId)
  if (!following) return
  if (following.messageId != null) {
    api.stopAnswer(chatId, following.messageId).catch(() => {
      // The abort below settles it here; a stop the server did not hear of ends with its run.
    })
  }
  following.controller.abort()
}

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

type Edit = (change: (message: Message) => Message) => void

/** Read the answer's events into the message until the server says it is settled. */
async function follow(chatId: number, messageId: number, update: Edit, signal: AbortSignal) {
  let after: number | null = null
  let drops = 0
  for (;;) {
    try {
      for await (const { seq, event } of streamEvents(chatId, messageId, after, signal)) {
        if (seq != null) after = seq
        drops = 0
        update((message) => applyEvent(message, event))
        if (event.type === 'end') return
      }
    } catch (error) {
      // A refusal (the chat is gone, the session ended) is final; a dropped connection is not.
      if (signal.aborted || (error instanceof ApiError && !AWAY.has(error.status))) throw error
    }
    if (drops >= RECONNECTS) throw new Error(LOST)
    drops++
    await wait(RECONNECT_WAIT * drops)
  }
}

/** Follow the answer with id `messageId` into the chat's query data until it is settled. */
async function run(queryClient: QueryClient, chatId: number, following: Following, update: Edit) {
  const { controller } = following
  running.set(chatId, following)
  changed()
  try {
    if (following.messageId == null) throw new Error('The question was not saved.')
    await follow(chatId, following.messageId, update, controller.signal)
  } catch (error) {
    const stopped = controller.signal.aborted
    update((message) => ({
      ...message,
      ...(stopped ? { notice: 'Stopped.', status: 'stopped' } : { error: (error as Error).message, status: 'failed' }),
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

/** Send a question and follow its answer into the chat's query data until it ends or is stopped. */
export async function ask(queryClient: QueryClient, chatId: number, question: string) {
  const { edit, last, byId } = editor(queryClient, chatId)
  edit((messages) => [
    ...messages,
    { role: 'user', content: question, steps: [], sources: {}, status: 'done', turnStart: 0 },
    { role: 'assistant', content: '', steps: [], sources: {}, status: 'streaming', thinking: true, turnStart: 0 },
  ])
  const following: Following = { controller: new AbortController(), messageId: null }
  let update: Edit = last
  try {
    const saved = await api.ask(chatId, question, following.controller.signal)
    // The saved pair replaces the provisional one, so the answer can be followed by its id.
    edit((messages) => [...messages.slice(0, -2), fromSaved(saved.question), fromSaved(saved.answer)])
    following.messageId = saved.answer.id
    update = byId(saved.answer.id)
  } catch (error) {
    // A refusal (another answer still running, too many in flight, the plan's questions used
    // up) shows where the answer would be; the question stays on screen for the user to send
    // again.
    if (!following.controller.signal.aborted) {
      last((message) => ({
        ...message,
        thinking: false,
        error: (error as Error).message,
        refusedByPlan: isPlanLimit(error),
        status: 'failed',
      }))
      queryClient.invalidateQueries({ queryKey: ['chats'] })
      return
    }
  }
  await run(queryClient, chatId, following, update)
}

/**
 * Follow an answer the server is still writing, after a reload or from another tab: its
 * events are read from the start, so the message starts empty.
 */
export function resume(queryClient: QueryClient, chatId: number, messageId: number) {
  if (running.has(chatId)) return
  const update = editor(queryClient, chatId).byId(messageId)
  update(empty)
  void run(queryClient, chatId, { controller: new AbortController(), messageId }, update)
}
