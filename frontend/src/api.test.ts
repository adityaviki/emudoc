import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  ApiError,
  ROLES,
  Unreachable,
  api,
  auth,
  handleForbidden,
  handleSwitched,
  isWithin,
  mayChange,
  oauthStartUrl,
  searchParams,
} from './api.ts'

/** Stub `fetch` with a reply made afresh for every call, keeping the headers each was sent with. */
function serve(reply: () => Response): Headers[] {
  const sent: Headers[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (_url: string, init?: RequestInit) => {
      sent.push(new Headers(init?.headers))
      return reply()
    }),
  )
  return sent
}

const refused = (status: number, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify({ detail: 'Refused.' }), { status, headers: { 'Content-Type': 'application/json', ...headers } })

afterEach(() => {
  vi.unstubAllGlobals()
  handleSwitched(null)
  handleForbidden(null)
})

describe('the organisation the session works in', () => {
  it('is named by no request, which carries the app\'s header alone', async () => {
    const sent = serve(() => new Response('[]', { headers: { 'Content-Type': 'application/json' } }))
    await api.projects()
    expect(sent.map((headers) => headers.get('X-Organization'))).toEqual([null])
    expect(sent.every((headers) => headers.get('X-Requested-With') === 'fetch')).toBe(true)
  })

  it('starts the page again when a refusal names another, as a view by support ended does', async () => {
    const switched = vi.fn()
    handleSwitched(switched)
    serve(() => refused(409, { 'X-Organization': '9' }))
    await expect(api.projects()).rejects.toBeInstanceOf(ApiError)
    expect(switched).toHaveBeenCalledOnce()
  })

  it('takes any other conflict for what it is', async () => {
    const switched = vi.fn()
    handleSwitched(switched)
    serve(() => refused(409))
    await expect(api.projects()).rejects.toMatchObject({ status: 409, message: 'Refused.' })
    expect(switched).not.toHaveBeenCalled()
  })
})

describe('a request refused as not the account\'s to make', () => {
  it('has the page ask again who is signed in, since a role may have changed', async () => {
    const forbidden = vi.fn()
    handleForbidden(forbidden)
    serve(() => refused(403))
    await expect(api.deleteProject(3)).rejects.toMatchObject({ status: 403, message: 'Refused.' })
    expect(forbidden).toHaveBeenCalledOnce()
  })

  it('leaves the account\'s own routes alone, whose refusals the forms show', async () => {
    const forbidden = vi.fn()
    handleForbidden(forbidden)
    serve(() => refused(403))
    await expect(auth.deleteAccount({ password: 'not it' })).rejects.toBeInstanceOf(ApiError)
    expect(forbidden).not.toHaveBeenCalled()
  })
})

describe('a request that reached no server', () => {
  const failing = (error: Error) => vi.stubGlobal('fetch', vi.fn(async () => Promise.reject(error)))

  it("says so in the app's words, whether the browser knew it was offline or not", async () => {
    failing(new TypeError('Failed to fetch'))
    const unreachable = api.projects()
    await expect(unreachable).rejects.toBeInstanceOf(Unreachable)
    await expect(unreachable).rejects.toMatchObject({
      offline: false,
      message: 'Emudoc did not answer. Check your connection, then try again.',
    })
    // Not an ApiError, which the queries' rule would never retry below 500.
    await expect(unreachable).rejects.not.toBeInstanceOf(ApiError)

    vi.stubGlobal('navigator', { onLine: false })
    await expect(api.projects()).rejects.toMatchObject({
      offline: true,
      message: 'Your browser is offline. Try again once the connection is back.',
    })
  })

  it('leaves an abort as it is', async () => {
    const abort = new DOMException('The operation was aborted.', 'AbortError')
    failing(abort)
    await expect(api.projects()).rejects.toBe(abort)
  })
})

/** XMLHttpRequest as far as an upload uses it, answered by the test. */
class FakeRequest {
  static made: FakeRequest[] = []
  method = ''
  url = ''
  headers = new Headers()
  body: unknown = null
  responseType = ''
  status = 0
  statusText = ''
  response: Blob | null = null
  upload: { onprogress: ((event: { loaded: number; total: number }) => void) | null } = { onprogress: null }
  onload: (() => void) | null = null
  onerror: (() => void) | null = null
  onabort: (() => void) | null = null
  private answered = ''

  constructor() {
    FakeRequest.made.push(this)
  }

  open(method: string, url: string) {
    this.method = method
    this.url = url
  }

  setRequestHeader(name: string, value: string) {
    this.headers.set(name, value)
  }

  getAllResponseHeaders() {
    return this.answered
  }

  send(body: unknown) {
    this.body = body
  }

  abort() {
    this.onabort?.()
  }

  answer(status: number, body: unknown, headers: Record<string, string> = {}) {
    this.status = status
    this.response = new Blob([JSON.stringify(body)])
    this.answered = Object.entries({ 'Content-Type': 'application/json', ...headers })
      .map(([name, value]) => `${name}: ${value}\r\n`)
      .join('')
    this.onload?.()
  }
}

const file = (name: string, text = 'text') => new File([text], name)

describe('an upload, which says how far it has gone', () => {
  const sending = () => {
    FakeRequest.made = []
    vi.stubGlobal('XMLHttpRequest', FakeRequest)
  }

  it('is sent with the headers of every request, and each file its folder when one is in one', async () => {
    sending()
    const progress = vi.fn()
    const items = [
      { file: file('a.md'), folder: '' },
      { file: file('b.md'), folder: 'Invoices/2026' },
    ]
    const answer = api.upload(3, items, 12, { onProgress: progress })
    const [request] = FakeRequest.made
    expect([request.method, request.url]).toEqual(['POST', '/api/projects/3/documents?folder_id=12'])
    expect(request.headers.get('X-Requested-With')).toBe('fetch')
    const form = request.body as FormData
    expect((form.getAll('files') as File[]).map((sent) => sent.name)).toEqual(['a.md', 'b.md'])
    expect(form.getAll('paths')).toEqual(['', 'Invoices/2026'])
    request.upload.onprogress?.({ loaded: 50, total: 200 })
    expect(progress).toHaveBeenCalledWith(50, 200)
    request.answer(201, { documents: [], rejected: [] })
    await expect(answer).resolves.toEqual({ documents: [], rejected: [] })

    // Files of no folder of their own carry no paths, as before folders could be uploaded.
    void api.upload(3, [{ file: file('c.md'), folder: '' }], null, { onProgress: progress })
    const form2 = FakeRequest.made[1].body as FormData
    expect(form2.getAll('paths')).toEqual([])
    expect(FakeRequest.made[1].url).toBe('/api/projects/3/documents')
  })

  it('is refused as any request is', async () => {
    sending()
    const answer = api.upload(3, [{ file: file('a.md'), folder: '' }], null, { onProgress: vi.fn() })
    FakeRequest.made[0].answer(413, { detail: 'These files are too large.' }, { 'X-Request-ID': 'abc' })
    const refusal = await answer.catch((error: unknown) => error)
    expect(refusal).toBeInstanceOf(ApiError)
    expect(refusal).toMatchObject({ status: 413, message: 'These files are too large.' })
  })

  it('is cut off by a network that drops it, and stopped by the page', async () => {
    sending()
    const cut = api.upload(3, [{ file: file('a.md'), folder: '' }], null, { onProgress: vi.fn() })
    FakeRequest.made[0].onerror?.()
    await expect(cut).rejects.toBeInstanceOf(Unreachable)

    const stop = new AbortController()
    const stopped = api.upload(3, [{ file: file('a.md'), folder: '' }], null, { onProgress: vi.fn(), signal: stop.signal })
    stop.abort()
    await expect(stopped).rejects.toMatchObject({ name: 'AbortError' })
  })
})

describe('a sign-in through a provider', () => {
  it('carries the zone and the invitation only when there are some', () => {
    expect(oauthStartUrl('google', '/', '')).toBe('/api/auth/oauth/google/start?next=%2F')
    expect(oauthStartUrl('microsoft', '/invitation', 'Europe/Berlin', 'tok-en')).toBe(
      '/api/auth/oauth/microsoft/start?next=%2Finvitation&time_zone=Europe%2FBerlin&invitation=tok-en',
    )
  })
})

describe('the roles someone may invite to', () => {
  it('are their own and those below it', () => {
    expect(isWithin('owner', 'owner')).toBe(true)
    expect(isWithin('owner', 'admin')).toBe(false)
    expect(isWithin('admin', 'admin')).toBe(true)
    expect(isWithin('viewer', 'admin')).toBe(true)
    expect(isWithin('member', 'viewer')).toBe(false)
  })
})

describe('the roles that may change what an organisation holds', () => {
  it('are every one but a viewer\'s', () => {
    expect(ROLES.map(mayChange)).toEqual([true, true, true, false])
  })
})

describe('searchParams', () => {
  it('asks the search route for each part of the filter, a list’s each value once', () => {
    const filter = {
      types: ['invoice', 'contract'],
      after: '2024-01-01',
      before: '2024-03-31',
      reviews: ['approved' as const],
      folder_id: 7,
      mentions: ['late delivery'],
    }
    expect(searchParams('notice', filter).toString()).toBe(
      'q=notice&limit=20&types=invoice&types=contract&after=2024-01-01&before=2024-03-31&reviews=approved&folder_id=7&mentions=late+delivery',
    )
    const none = { ...filter, types: [], after: null, before: null, reviews: [], folder_id: null, mentions: [] }
    expect(searchParams('notice', none, 10).toString()).toBe('q=notice&limit=10')
  })
})
