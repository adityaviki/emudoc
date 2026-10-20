import { QueryClient } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { applyEvent, ask, chatKey, isStreaming, loadChat, resume, settle, splitChats, stop } from './chat.ts'
import type { ChatData, Message } from './chat.ts'
import type { AskResult, ChatDetail, ChatEvent, ChatMessage, Source } from './api.ts'

const SOURCE: Source = {
  id: 812,
  document_id: 41,
  document_title: 'Service contract',
  context: 'Service contract › 4 Delivery',
  page_start: 3,
  page_end: 3,
  excerpt: 'The notice period is 90 days.',
}
const USAGE = { model: 'google:gemini', calls: 2, input_tokens: 900, output_tokens: 40, cost_micros: 1200 }

function saved(id: number, role: 'user' | 'assistant', content: string, status: ChatMessage['status'] = 'done'): ChatMessage {
  return { id, role, content, steps: [], sources: [], notice: null, error: null, status, usage: {}, created_at: '2026-10-17T09:00:00Z' }
}

function chat(messages: ChatMessage[] = []): ChatDetail {
  return { id: 1, project_id: 3, user_id: 1, started_by: 'Ada', title: 'Notice', visibility: 'private', created_at: '2026-10-17T09:00:00Z', updated_at: '2026-10-17T09:00:00Z', messages }
}

const answering: Message = { role: 'assistant', content: '', steps: [], sources: {}, status: 'streaming', thinking: true, turnStart: 0 }

const fold = (events: ChatEvent[], from: Message = answering) => events.reduce(applyEvent, from)

describe('applyEvent', () => {
  it('builds an answer from its tool calls, sources and text', () => {
    const message = fold([
      { type: 'tool_call', id: 'call_1', name: 'search', input: { query: 'notice' } },
      { type: 'sources', sources: [SOURCE] },
      { type: 'tool_result', id: 'call_1', summary: '1 passage', is_error: false },
      { type: 'text', text: 'The notice period ' },
      { type: 'text', text: 'is 90 days [P812].' },
      { type: 'done', usage: USAGE },
      { type: 'end', status: 'done' },
    ])
    expect(message).toMatchObject({
      content: 'The notice period is 90 days [P812].',
      steps: [{ id: 'call_1', name: 'search', input: { query: 'notice' }, summary: '1 passage', is_error: false }],
      sources: { 812: SOURCE },
      usage: USAGE,
      status: 'done',
      thinking: false,
    })
  })

  it('drops the text of a model turn that is tried again, and keeps what came before it', () => {
    const message = fold([
      { type: 'text', text: 'Searching. ' },
      { type: 'tool_call', id: 'call_1', name: 'search', input: {} },
      { type: 'tool_result', id: 'call_1', summary: '', is_error: false },
      { type: 'text', text: 'Half an ans' },
      { type: 'retry' },
      { type: 'text', text: 'The answer.' },
    ])
    expect(message.content).toBe('Searching. The answer.')
  })

  it('starts over when the run writing the answer was lost', () => {
    const message = fold([
      { type: 'text', text: 'Half' },
      { type: 'tool_call', id: 'call_1', name: 'search', input: {} },
      { type: 'sources', sources: [SOURCE] },
      { type: 'notice', message: 'Taking longer than usual.' },
      { type: 'restart' },
    ])
    expect(message).toMatchObject({ content: '', steps: [], sources: {}, notice: null, thinking: true })
  })

  it('shows an error and a notice beside the text', () => {
    const message = fold([
      { type: 'text', text: 'So far.' },
      { type: 'notice', message: 'Stopped.' },
      { type: 'error', message: 'The model is not answering.' },
    ])
    expect(message).toMatchObject({ content: 'So far.', notice: 'Stopped.', error: 'The model is not answering.', thinking: false })
  })
})

describe('settle', () => {
  it('ends the steps still running and an answer still streaming', () => {
    const message = settle(fold([{ type: 'tool_call', id: 'call_1', name: 'search', input: {} }]))
    expect(message.steps[0].summary).toBe('')
    expect(message).toMatchObject({ status: 'done', thinking: false })
    expect(settle({ ...answering, status: 'stopped' }).status).toBe('stopped')
  })
})

describe('loadChat', () => {
  it('keys the sources by id and marks an answer still being written', () => {
    const answer = { ...saved(2, 'assistant', 'Half', 'streaming'), sources: [SOURCE], usage: { answer: USAGE } }
    const loaded = loadChat(chat([saved(1, 'user', 'Notice period?'), answer]))
    expect(loaded.messages[0]).toMatchObject({ role: 'user', thinking: false, usage: null })
    expect(loaded.messages[1]).toMatchObject({ sources: { 812: SOURCE }, thinking: true, usage: USAGE })
    expect(isStreaming(loaded)).toBe(true)
    expect(isStreaming(loadChat(chat([saved(1, 'user', 'Notice period?'), saved(2, 'assistant', 'Done.')])))).toBe(false)
    expect(isStreaming(undefined)).toBe(false)
  })
})

describe('splitChats', () => {
  it("parts the member's own chats from those others shared, each in the server's order", () => {
    const by = (id: number, user_id: number) => ({ ...chat(), id, user_id })
    const { own, shared } = splitChats([by(5, 2), by(4, 1), by(3, 2), by(2, 1)], 1)
    expect(own.map((item) => item.id)).toEqual([4, 2])
    expect(shared.map((item) => item.id)).toEqual([5, 3])
    expect(splitChats([], 1)).toEqual({ own: [], shared: [] })
  })
})

// The answer's events as the server writes them (backend/app/answers.py, `sse`): an id line
// for each stored event, none for `end`.
function sse(...events: [number | null, ChatEvent][]): string {
  return events.map(([seq, event]) => `${seq == null ? '' : `id: ${seq}\n`}data: ${JSON.stringify(event)}\n\n`).join('')
}

function stream(chunks: string[], signal?: AbortSignal | null, open = false): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder()
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk))
      // A connection left open stays open until the page aborts it, as fetch's would.
      if (open) signal?.addEventListener('abort', () => controller.error(new DOMException('Aborted', 'AbortError')))
      else controller.close()
    },
  })
}

interface Call {
  url: string
  method: string
  headers: Headers
  body: unknown
}

type Reply = (init: RequestInit) => Response

const json = (body: unknown, status = 200): Reply => () => Response.json(body, { status })
const events = (...chunks: string[]): Reply => (init) => new Response(stream(chunks, init.signal))
const pending = (...chunks: string[]): Reply => (init) => new Response(stream(chunks, init.signal, true))

let calls: Call[]
let replies: Reply[]
let client: QueryClient

// The server, as a queue of replies taken in order; what the page sent is kept in `calls`.
function serve(...queued: Reply[]) {
  replies.push(...queued)
}

beforeEach(() => {
  calls = []
  replies = []
  client = new QueryClient()
  vi.stubGlobal('fetch', async (url: string, init: RequestInit = {}) => {
    const reply = replies.shift()
    if (!reply) throw new Error(`Nothing to answer ${init.method ?? 'GET'} ${url} with.`)
    calls.push({
      url,
      method: init.method ?? 'GET',
      headers: new Headers(init.headers),
      body: typeof init.body === 'string' ? JSON.parse(init.body) : init.body,
    })
    return reply(init)
  })
  vi.useFakeTimers({ toFake: ['setTimeout'] })
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

/** Wait for `promise`, moving the clock through the waits between reconnections. */
async function through<T>(promise: Promise<T>): Promise<T> {
  let done = false
  promise.then(
    () => (done = true),
    () => (done = true),
  )
  for (let step = 0; !done; step++) {
    if (step > 100) throw new Error('The answer was still being followed after 100 seconds.')
    await vi.advanceTimersByTimeAsync(1000)
  }
  return promise
}

const messages = () => client.getQueryData<ChatData>(chatKey(1))!.messages

function asked(question = 'Notice period?'): AskResult {
  return { question: saved(10, 'user', question), answer: saved(11, 'assistant', '', 'streaming') }
}

describe('ask', () => {
  beforeEach(() => client.setQueryData(chatKey(1), loadChat(chat())))

  it('saves the question, then follows its answer until the server says it is settled', async () => {
    serve(
      json(asked(), 201),
      events(
        sse([0, { type: 'tool_call', id: 'call_1', name: 'search', input: { query: 'notice' } }]),
        // A keep-alive comment, and an event cut across two reads.
        ': keep-alive\n\n',
        sse([1, { type: 'sources', sources: [SOURCE] }]).slice(0, 20),
        sse([1, { type: 'sources', sources: [SOURCE] }]).slice(20),
        sse([2, { type: 'text', text: 'Ninety days [P812].' }], [3, { type: 'done', usage: USAGE }], [null, { type: 'end', status: 'done' }]),
      ),
    )
    await through(ask(client, 1, 'Notice period?'))

    const [question, answer] = messages()
    expect(question).toMatchObject({ id: 10, role: 'user', content: 'Notice period?' })
    expect(answer).toMatchObject({
      id: 11,
      content: 'Ninety days [P812].',
      sources: { 812: SOURCE },
      status: 'done',
      thinking: false,
      usage: USAGE,
    })
    expect(answer.steps[0]).toMatchObject({ name: 'search', summary: '' })

    const [post, follow] = calls
    expect([post.method, post.url, post.body]).toEqual(['POST', '/api/chats/1/messages', { content: 'Notice period?' }])
    expect([follow.method, follow.url]).toEqual(['GET', '/api/chats/1/messages/11/events'])
    // Every request says it comes from the page (refuse_cross_site in backend/app/api/deps.py).
    expect(calls.every((call) => call.headers.get('X-Requested-With') === 'fetch')).toBe(true)
    expect(follow.headers.has('Last-Event-ID')).toBe(false)
  })

  it('follows again from the last event it saw when the connection drops or a proxy says the server is away', async () => {
    serve(
      json(asked(), 201),
      events(sse([0, { type: 'text', text: 'Ninety ' }], [1, { type: 'text', text: 'days' }])),
      json({ detail: 'Bad gateway' }, 502),
      events(sse([2, { type: 'text', text: '.' }], [null, { type: 'end', status: 'done' }])),
    )
    await through(ask(client, 1, 'Notice period?'))

    expect(messages()[1]).toMatchObject({ content: 'Ninety days.', status: 'done', error: null })
    expect(calls.slice(2).map((call) => call.headers.get('Last-Event-ID'))).toEqual(['1', '1'])
  })

  it('gives up after five drops in a row', async () => {
    serve(json(asked(), 201), ...Array.from({ length: 6 }, () => events()))
    await through(ask(client, 1, 'Notice period?'))

    expect(messages()[1]).toMatchObject({
      error: 'The connection to the answer was lost. Reload the page to see it.',
      status: 'failed',
      thinking: false,
    })
    expect(calls).toHaveLength(7)
  })

  it('takes a refusal while following as final', async () => {
    serve(json(asked(), 201), json({ detail: 'Chat not found' }, 404))
    await through(ask(client, 1, 'Notice period?'))

    expect(messages()[1]).toMatchObject({ error: 'Chat not found', status: 'failed' })
    expect(calls).toHaveLength(2)
  })

  it('shows a refusal of the question where the answer would be, a plan limit as such', async () => {
    const limit = 'The Free plan allows 50 questions a month. Upgrade your plan to ask more.'
    serve(json({ detail: limit, limit: 'questions_per_month' }, 402))
    await through(ask(client, 1, 'Notice period?'))

    const [question, answer] = messages()
    expect(question.content).toBe('Notice period?')
    expect(question.id).toBeUndefined()
    expect(answer).toMatchObject({
      error: limit,
      refusedByPlan: { message: limit, limit: 'questions_per_month' },
      status: 'failed',
      thinking: false,
    })
    expect(calls).toHaveLength(1)
  })

  it('stops an answer on the server and keeps what arrived', async () => {
    serve(json(asked(), 201), pending(sse([0, { type: 'text', text: 'Ninety' }])))
    const asking = ask(client, 1, 'Notice period?')
    await vi.waitFor(() => expect(messages()[1].content).toBe('Ninety'))

    serve(json(saved(11, 'assistant', 'Ninety', 'stopped')))
    stop(1)
    await through(asking)

    expect(messages()[1]).toMatchObject({ content: 'Ninety', notice: 'Stopped.', status: 'stopped', thinking: false })
    expect(calls.map((call) => `${call.method} ${call.url}`)).toEqual([
      'POST /api/chats/1/messages',
      'GET /api/chats/1/messages/11/events',
      'POST /api/chats/1/messages/11/stop',
    ])
  })
})

describe('resume', () => {
  it('follows an answer still being written from its first event', async () => {
    const answer = saved(11, 'assistant', 'Ninety', 'streaming')
    client.setQueryData(chatKey(1), loadChat(chat([saved(10, 'user', 'Notice period?'), answer])))
    serve(events(sse([0, { type: 'text', text: 'Ninety days.' }], [null, { type: 'end', status: 'done' }])))
    resume(client, 1, 11)
    // A second look while the first is following starts nothing.
    resume(client, 1, 11)
    await vi.waitFor(() => expect(messages()[1].status).toBe('done'))

    expect(messages()[1]).toMatchObject({ content: 'Ninety days.', status: 'done', thinking: false })
    expect(calls).toHaveLength(1)
    expect(calls[0].headers.has('Last-Event-ID')).toBe(false)
  })
})
