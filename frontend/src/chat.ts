// Chats as the Ask tab holds them, and the answers in flight. An answer keeps streaming into
// the query cache after the user moves to another chat or tab, so it is tracked here rather
// than in a component.

import type { QueryClient } from '@tanstack/react-query'
import { useSyncExternalStore } from 'react'
import { streamAnswer } from './api.ts'
import type { Chat, ChatDetail, ChatEvent, Source } from './api.ts'

export interface Step {
  id: string
  name: string
  input: Record<string, unknown>
  // Undefined while the tool is still running.
  summary?: string
  is_error?: boolean
}

export interface Message {
  role: 'user' | 'assistant'
  content: string
  steps: Step[]
  sources: Record<number, Source>
  thinking?: boolean
  notice?: string | null
  error?: string | null
  // Length of `content` when the current model turn began, to discard a turn that is retried.
  turnStart: number
}

export interface ChatData extends Chat {
  messages: Message[]
}

export const chatKey = (chatId: number | null) => ['chat', chatId] as const

/** A saved chat, ready to render. */
export function loadChat(chat: ChatDetail): ChatData {
  return {
    ...chat,
    messages: chat.messages.map((message) => ({
      ...message,
      sources: Object.fromEntries(message.sources.map((source) => [source.id, source])),
      turnStart: 0,
    })),
  }
}

/** Mark an answer as finished: nothing in it is still running. */
export function settle(message: Message): Message {
  return {
    ...message,
    thinking: false,
    steps: message.steps.map((step) => (step.summary === undefined ? { ...step, summary: '' } : step)),
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
      return { ...message, thinking: false }
  }
}

// The answers being streamed, by chat id.
const running = new Map<number, AbortController>()
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

/** Whether an answer is being streamed into the chat. */
export function useStreaming(chatId: number | null): boolean {
  return useSyncExternalStore(subscribe, () => chatId != null && running.has(chatId))
}

export function stop(chatId: number) {
  running.get(chatId)?.abort()
}

/** Send a question and stream the answer into the chat's query data until it ends or is stopped. */
export async function ask(queryClient: QueryClient, chatId: number, question: string) {
  const edit = (change: (messages: Message[]) => Message[]) =>
    queryClient.setQueryData<ChatData>(chatKey(chatId), (chat) => chat && { ...chat, messages: change(chat.messages) })
  const update = (change: (message: Message) => Message) =>
    edit((messages) => [...messages.slice(0, -1), change(messages[messages.length - 1])])

  edit((messages) => [
    ...messages,
    { role: 'user', content: question, steps: [], sources: {}, turnStart: 0 },
    { role: 'assistant', content: '', steps: [], sources: {}, thinking: true, turnStart: 0 },
  ])
  const controller = new AbortController()
  running.set(chatId, controller)
  changed()
  try {
    for await (const event of streamAnswer(chatId, question, controller.signal)) {
      update((message) => applyEvent(message, event))
    }
  } catch (error) {
    const stopped = controller.signal.aborted
    update((message) => ({
      ...message,
      ...(stopped ? { notice: 'Stopped.' } : { error: (error as Error).message }),
    }))
  } finally {
    update(settle)
    running.delete(chatId)
    changed()
    // The first question names the chat, and every question moves it up the list.
    queryClient.invalidateQueries({ queryKey: ['chats'] })
  }
}
