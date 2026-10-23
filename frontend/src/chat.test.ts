import { QueryClient, onlineManager } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { applyEvent, ask, chatKey, isStreaming, loadChat, mayRetry, resume, retry, settle, splitChats, stop } from './chat.ts'
import type { ChatData, Message } from './chat.ts'
import type { AskResult, ChatDetail, ChatEvent, ChatMessage, RetryResult, Source } from './api.ts'

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

type Reply = (init: RequestInit) => Response | Promise<Response>

const json = (body: unknown, status = 200): Reply => () => Response.json(body, { status })
const events = (...chunks: string[]): Reply => (init) => new Response(stream(chunks, init.signal))
const pending = (...chunks: string[]): Reply => (init) => new Response(stream(chunks, init.signal, true))
// A request that reached no server, as fetch rejects it.
const dropped: Reply = () => {
  throw new TypeError('Failed to fetch')
}

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
  onlineManager.setOnline(true)
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

/** Move the clock through the waits between reconnections until the page has made `count` requests. */
async function untilCalls(count: number) {
  for (let step = 0; calls.length < count; step++) {
    if (step > 100) throw new Error(`${calls.length} requests after 100 seconds, not ${count}.`)
    await vi.advanceTimersByTimeAsync(1000)
  }
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

  it('gives up after five drops in a row, and follows the answer again from its first event when asked to', async () => {
    serve(json(asked(), 201), ...Array.from({ length: 6 }, () => events()))
    await through(ask(client, 1, 'Notice period?'))

    expect(messages()[1]).toMatchObject({
      error: 'The connection to the answer was lost. Reconnect to follow it again.',
      status: 'failed',
      lost: true,
      thinking: false,
    })
    expect(calls).toHaveLength(7)

    serve(events(sse([0, { type: 'text', text: 'Ninety days.' }], [null, { type: 'end', status: 'done' }])))
    resume(client, 1, 11)
    expect(messages()[1]).toMatchObject({ status: 'streaming', error: null, lost: false, thinking: true })
    await vi.waitFor(() => expect(messages()[1].status).toBe('done'))

    expect(messages()[1]).toMatchObject({ content: 'Ninety days.', error: null, lost: false })
    expect(calls).toHaveLength(8)
    expect(calls[7].headers.has('Last-Event-ID')).toBe(false)
  })

  it('waits while the browser is offline, however long, and follows on from the last event once it is back', async () => {
    serve(json(asked(), 201), pending(sse([0, { type: 'text', text: 'Ninety ' }])))
    const asking = ask(client, 1, 'Notice period?')
    await vi.waitFor(() => expect(messages()[1].content).toBe('Ninety '))

    // Going offline ends the open connection, which the network might otherwise leave hanging.
    onlineManager.setOnline(false)
    await vi.waitFor(() => expect(messages()[1]).toMatchObject({ offline: true, status: 'streaming' }))
    await vi.advanceTimersByTimeAsync(60_000)
    expect(calls).toHaveLength(2)

    serve(events(sse([1, { type: 'text', text: 'days.' }], [null, { type: 'end', status: 'done' }])))
    onlineManager.setOnline(true)
    await through(asking)

    expect(messages()[1]).toMatchObject({ content: 'Ninety days.', status: 'done', offline: false, error: null })
    expect(calls[2].headers.get('Last-Event-ID')).toBe('0')
  })

  it('counts none of the drops while the browser is offline', async () => {
    // Four drops in a row; then, offline, a connection that cannot be made waits too.
    serve(json(asked(), 201), ...Array.from({ length: 4 }, () => events()))
    const asking = ask(client, 1, 'Notice period?')
    await untilCalls(5)
    onlineManager.setOnline(false)
    serve(dropped)
    await untilCalls(6)
    await vi.waitFor(() => expect(messages()[1].offline).toBe(true))

    // Back online, five more drops in a row are borne before the answer would be lost.
    serve(...Array.from({ length: 5 }, () => events()), events(sse([0, { type: 'text', text: 'Ninety days.' }], [null, { type: 'end', status: 'done' }])))
    onlineManager.setOnline(true)
    await through(asking)

    expect(messages()[1]).toMatchObject({ content: 'Ninety days.', status: 'done', error: null })
    expect(calls).toHaveLength(12)
  })

  it('stops an answer waiting for the connection', async () => {
    serve(json(asked(), 201), pending(sse([0, { type: 'text', text: 'Ninety' }])))
    const asking = ask(client, 1, 'Notice period?')
    await vi.waitFor(() => expect(messages()[1].content).toBe('Ninety'))
    onlineManager.setOnline(false)
    await vi.waitFor(() => expect(messages()[1].offline).toBe(true))

    // The stop's request reaches nothing; the answer stops here all the same.
    serve(dropped)
    stop(1)
    await through(asking)

    expect(messages()[1]).toMatchObject({ content: 'Ninety', notice: 'Stopped.', status: 'stopped', offline: false })
    expect(calls.map((call) => `${call.method} ${call.url}`)).toEqual([
      'POST /api/chats/1/messages',
      'GET /api/chats/1/messages/11/events',
      'POST /api/chats/1/messages/11/stop',
    ])
  })

  it('takes a refusal while following as final', async () => {
    serve(json(asked(), 201), json({ detail: 'Chat not found' }, 404))
    await through(ask(client, 1, 'Notice period?'))

    expect(messages()[1]).toMatchObject({ error: 'Chat not found', status: 'failed' })
    expect(calls).toHaveLength(2)
  })

  it("shows a question that reached no server in the app's words, the question kept on screen", async () => {
    serve(dropped)
    await through(ask(client, 1, 'Notice period?'))

    const [question, answer] = messages()
    expect(question.content).toBe('Notice period?')
    expect(answer).toMatchObject({
      error: 'Emudoc did not answer. Check your connection, then try again.',
      status: 'failed',
      thinking: false,
    })
    expect(answer.lost).toBeUndefined()
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

  it('stops an answer whose question is still being saved, on the server once it has an id', async () => {
    let save = () => {}
    const stored = new Promise<void>((resolve) => (save = resolve))
    serve(async () => {
      await stored
      return Response.json(asked(), { status: 201 })
    })
    const asking = ask(client, 1, 'Notice period?')
    stop(1)
    serve(json({ ...saved(11, 'assistant', '', 'stopped'), notice: 'Stopped.' }))
    save()
    await through(asking)

    expect(messages()[1]).toMatchObject({ id: 11, notice: 'Stopped.', status: 'stopped', thinking: false })
    // Nothing of the answer was followed: the stop went out as soon as the answer had an id.
    expect(calls.map((call) => `${call.method} ${call.url}`)).toEqual([
      'POST /api/chats/1/messages',
      'POST /api/chats/1/messages/11/stop',
    ])
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

describe('mayRetry', () => {
  it('offers to try again an answer that was stopped or failed, not one lost, refused by the plan or finished', () => {
    const answer = (change: Partial<Message>): Message => ({ ...answering, thinking: false, ...change })
    expect(mayRetry(answer({ status: 'stopped', notice: 'Stopped.' }))).toBe(true)
    expect(mayRetry(answer({ status: 'failed', error: 'The model is not answering.' }))).toBe(true)
    expect(mayRetry(answer({ status: 'failed', lost: true }))).toBe(false)
    const refusal = { message: 'The Free plan allows 50 questions a month.', limit: 'questions_per_month' as const }
    expect(mayRetry(answer({ status: 'failed', refusedByPlan: refusal }))).toBe(false)
    expect(mayRetry(answer({ status: 'done' }))).toBe(false)
    expect(mayRetry(answer({ status: 'streaming' }))).toBe(false)
    expect(mayRetry({ ...answer({ status: 'failed' }), role: 'user' })).toBe(false)
  })
})

describe('retry', () => {
  const stopped = { ...saved(11, 'assistant', 'Ninety', 'stopped'), notice: 'Stopped.' }
  const retried = (lastEventId: number | null): RetryResult => ({
    answer: saved(11, 'assistant', '', 'streaming'),
    last_event_id: lastEventId,
  })

  beforeEach(() => client.setQueryData(chatKey(1), loadChat(chat([saved(10, 'user', 'Notice period?'), stopped]))))

  it('empties the answer in its place and follows the new try past the events the earlier one left', async () => {
    serve(
      json(retried(3)),
      events(sse([4, { type: 'restart' }], [5, { type: 'text', text: 'Ninety days.' }], [null, { type: 'end', status: 'done' }])),
    )
    const trying = retry(client, 1)
    // At once, before the server has answered: nothing of the stopped try is left on screen.
    expect(messages()[1]).toMatchObject({ content: '', notice: null, status: 'streaming', thinking: true })
    await through(trying)

    expect(messages()).toHaveLength(2)
    expect(messages()[1]).toMatchObject({ id: 11, content: 'Ninety days.', notice: null, error: null, status: 'done' })
    expect(calls.map((call) => `${call.method} ${call.url}`)).toEqual([
      'POST /api/chats/1/messages/11/retry',
      'GET /api/chats/1/messages/11/events',
    ])
    expect(calls[1].headers.get('Last-Event-ID')).toBe('3')
  })

  it('waits for the stop of the answer to reach the server before trying it again', async () => {
    client.setQueryData(chatKey(1), loadChat(chat()))
    serve(json(asked(), 201), pending(sse([0, { type: 'text', text: 'Ninety' }])))
    const asking = ask(client, 1, 'Notice period?')
    await vi.waitFor(() => expect(messages()[1].content).toBe('Ninety'))
    // The server is slow to answer the stop, and the member tries the answer again meanwhile.
    let answerStop = () => {}
    const stopAnswered = new Promise<void>((resolve) => (answerStop = resolve))
    serve(async () => {
      await stopAnswered
      return Response.json({ ...saved(11, 'assistant', 'Ninety', 'stopped'), notice: 'Stopped.' })
    })
    stop(1)
    await through(asking)
    serve(
      json(retried(0)),
      events(sse([1, { type: 'restart' }], [2, { type: 'text', text: 'Ninety days.' }], [null, { type: 'end', status: 'done' }])),
    )
    const trying = retry(client, 1)
    await vi.advanceTimersByTimeAsync(5000)
    expect(calls).toHaveLength(3)

    answerStop()
    await through(trying)
    expect(calls.slice(2).map((call) => `${call.method} ${call.url}`)).toEqual([
      'POST /api/chats/1/messages/11/stop',
      'POST /api/chats/1/messages/11/retry',
      'GET /api/chats/1/messages/11/events',
    ])
    expect(messages()[1]).toMatchObject({ content: 'Ninety days.', status: 'done' })
  })

  it('follows the new try from the start when the earlier one left no event', async () => {
    serve(json(retried(null)), events(sse([0, { type: 'text', text: 'Ninety days.' }], [null, { type: 'end', status: 'done' }])))
    await through(retry(client, 1))

    expect(messages()[1]).toMatchObject({ content: 'Ninety days.', status: 'done' })
    expect(calls[1].headers.has('Last-Event-ID')).toBe(false)
  })

  it('leaves the answer as it was with the refusal of the try, which may be tried again later', async () => {
    const busy = 'Too many questions are being answered right now. Wait a moment and try again.'
    serve(json({ detail: busy }, 429))
    await through(retry(client, 1))

    expect(messages()[1]).toMatchObject({ content: 'Ninety', notice: 'Stopped.', error: busy, status: 'stopped', thinking: false })
    expect(mayRetry(messages()[1])).toBe(true)

    const limit = 'The Free plan allows USD 1.00 of model use a day.'
    serve(json({ detail: limit, limit: 'daily_spend_micros' }, 402))
    await through(retry(client, 1))
    expect(messages()[1]).toMatchObject({ content: 'Ninety', refusedByPlan: { message: limit, limit: 'daily_spend_micros' } })
    expect(mayRetry(messages()[1])).toBe(false)
  })

  it('asks a question the server refused before saving it again', async () => {
    client.setQueryData(chatKey(1), loadChat(chat()))
    serve(json({ detail: 'Too many questions are being answered right now.' }, 429))
    await through(ask(client, 1, 'Notice period?'))
    expect(messages()[1]).toMatchObject({ status: 'failed' })
    expect(messages()[1].id).toBeUndefined()

    serve(json(asked(), 201), events(sse([0, { type: 'text', text: 'Ninety days.' }], [null, { type: 'end', status: 'done' }])))
    await through(retry(client, 1))

    expect(messages()).toHaveLength(2)
    expect(messages()[0]).toMatchObject({ id: 10, content: 'Notice period?' })
    expect(messages()[1]).toMatchObject({ id: 11, content: 'Ninety days.', status: 'done', error: null })
    expect(calls[1]).toMatchObject({ method: 'POST', url: '/api/chats/1/messages', body: { content: 'Notice period?' } })
  })
})
