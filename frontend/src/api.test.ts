import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, api, handleSwitched, isWithin, oauthStartUrl, showOrganization } from './api.ts'

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
  showOrganization(null)
  handleSwitched(null)
})

describe('the organisation the page shows', () => {
  it('is named in every request once the page knows it, and not before', async () => {
    const sent = serve(() => new Response('[]', { headers: { 'Content-Type': 'application/json' } }))
    await api.projects()
    showOrganization(7)
    await api.projects()
    expect(sent.map((headers) => headers.get('X-Organization'))).toEqual([null, '7'])
    expect(sent.every((headers) => headers.get('X-Requested-With') === 'fetch')).toBe(true)
  })

  it('starts the page again when another tab switched the session elsewhere', async () => {
    const switched = vi.fn()
    handleSwitched(switched)
    showOrganization(7)
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
