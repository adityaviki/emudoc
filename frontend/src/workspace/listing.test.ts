import { QueryClient } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { BY_NAME, REFRESH, WAITING_REFRESH, editListings, fetchListing, findListed, listed, listingKey, partlyCountBehind, refreshAfter, settledMeanwhile, showMore } from './listing.ts'
import type { ListedPage, Listing } from './listing.ts'
import type { Document } from '../api.ts'

function doc(id: number, filename: string, folderId: number | null = null): Document {
  return {
    id,
    project_id: 3,
    folder_id: folderId,
    filename,
    mime_type: 'text/plain',
    size_bytes: 10,
    status: 'ready',
    stages: {},
    error: null,
    page_count: null,
    title: null,
    doc_type: null,
    doc_date: null,
    read_title: null,
    read_doc_type: null,
    read_doc_date: null,
    language: null,
    read_language: null,
    edited_details: [],
    summary: null,
    template: null,
    usage: {},
    created_at: '2026-10-19T09:00:00Z',
    version: 1,
    review: null,
    assignee_id: null,
    assignee: null,
    review_changer: null,
    review_changed_at: null,
    tag_ids: [],
  }
}

const page = (after: string | null, etag: string, documents: Document[], nextAfter: string | null): ListedPage => ({
  after,
  etag,
  documents,
  next_after: nextAfter,
})

interface Call {
  url: string
  tag: string | null
}

type Reply = () => Response

// A page as list_documents sends it, with its tag; and the answer to a tag that still names the page.
const sent = (etag: string, documents: Document[], nextAfter: string | null): Reply => () =>
  Response.json({ documents, next_after: nextAfter }, { headers: { ETag: etag } })
const unchanged = (etag: string): Reply => () => new Response(null, { status: 304, headers: { ETag: etag } })

let calls: Call[]
let replies: Reply[]
let client: QueryClient

beforeEach(() => {
  calls = []
  replies = []
  client = new QueryClient()
  vi.stubGlobal('fetch', async (url: string, init: RequestInit = {}) => {
    const reply = replies.shift()
    if (!reply) throw new Error(`Nothing to answer ${url} with.`)
    calls.push({ url, tag: new Headers(init.headers).get('If-None-Match') })
    return reply()
  })
})

afterEach(() => {
  vi.unstubAllGlobals()
})

const [a, b, c, d] = [doc(1, 'a.txt'), doc(2, 'b.txt'), doc(3, 'c.txt'), doc(4, 'd.txt')]
const ROOT = { folder: null }
const SEVEN = { folder: 7 }

describe('fetchListing', () => {
  it('asks for the first page without a tag, and again with the tag it was given', async () => {
    replies.push(sent('"one"', [a, b], null))
    const first = await fetchListing(3, ROOT, BY_NAME, undefined)
    expect(calls).toEqual([{ url: '/api/projects/3/documents', tag: null }])
    expect(first).toEqual({ pages: [page(null, '"one"', [a, b], null)], wanted: 1 })

    // Nothing changed: the page held is kept as it was, the same object, so nothing renders again.
    replies.push(unchanged('"one"'))
    const again = await fetchListing(3, ROOT, BY_NAME, first)
    expect(calls[1]).toEqual({ url: '/api/projects/3/documents', tag: '"one"' })
    expect(again.pages[0]).toBe(first.pages[0])
  })

  it('asks for each page held with its own tag, and for the pages after one that ends elsewhere afresh', async () => {
    const held: Listing = { pages: [page(null, '"p1"', [a, b], 'after-b'), page('after-b', '"p2"', [c, d], null)], wanted: 2 }
    // The first page lost "b.txt", so it ends on "c.txt" now, and the second starts after it.
    replies.push(sent('"p1-new"', [a, c], 'after-c'), sent('"p2-new"', [d], null))
    const listing = await fetchListing(3, SEVEN, BY_NAME, held)
    expect(calls).toEqual([
      { url: '/api/projects/3/documents?folder_id=7', tag: '"p1"' },
      { url: '/api/projects/3/documents?folder_id=7&after=after-c', tag: null },
    ])
    expect(listed(listing)).toEqual([a, c, d])
  })

  it('keeps a later page that has not changed when an earlier one has but ends where it did', async () => {
    const second = page('after-b', '"p2"', [c, d], null)
    const held: Listing = { pages: [page(null, '"p1"', [a, b], 'after-b'), second], wanted: 2 }
    const renamed = { ...a, filename: 'a2.txt' }
    replies.push(sent('"p1-new"', [renamed, b], 'after-b'), unchanged('"p2"'))
    const listing = await fetchListing(3, SEVEN, BY_NAME, held)
    expect(calls[1]).toEqual({ url: '/api/projects/3/documents?folder_id=7&after=after-b', tag: '"p2"' })
    expect(listing.pages[1]).toBe(second)
    expect(listed(listing)).toEqual([renamed, b, c, d])
  })

  it('asks for a place that is not a folder, and an order that is not the first, by name', async () => {
    replies.push(sent('"t"', [b, a], null))
    await fetchListing(3, { tag: 5 }, { sort: 'size', order: 'desc' }, undefined)
    replies.push(sent('"f"', [a], null), sent('"r"', [b], null), sent('"s"', [], null), sent('"n"', [], null), sent('"t"', [], null))
    await fetchListing(3, { favorites: true }, { sort: 'name', order: 'desc' }, undefined)
    await fetchListing(3, { recent: true }, BY_NAME, undefined)
    await fetchListing(3, { tag: 2 }, { sort: 'date', order: 'asc' }, undefined)
    await fetchListing(3, { name: 'inv 2' }, BY_NAME, undefined)
    await fetchListing(3, { tag: 2, name: 'inv' }, BY_NAME, undefined)
    expect(calls.map((call) => call.url)).toEqual([
      '/api/projects/3/documents?tag=5&sort=size&order=desc',
      '/api/projects/3/documents?favorites=true&order=desc',
      '/api/projects/3/documents?recent=true',
      '/api/projects/3/documents?tag=2&sort=date',
      '/api/projects/3/documents?name=inv+2',
      '/api/projects/3/documents?tag=2&name=inv',
    ])
    // Each place in each order is held apart, a folder by its id as before, a name filter last, under its place's.
    expect(listingKey(3, { tag: 5 }, { sort: 'size', order: 'desc' })).toEqual(['documents', 3, 'tag:5', 'size', 'desc', null])
    expect(listingKey(3, SEVEN)).toEqual(['documents', 3, 7, 'name', 'asc', null])
    expect(listingKey(3, { name: 'inv' })).toEqual(['documents', 3, 'named', 'name', 'asc', 'inv'])
    expect(listingKey(3, { recent: true, name: 'inv' })).toEqual(['documents', 3, 'recent', 'name', 'asc', 'inv'])
  })

  it('stops at the last page, however many were held', async () => {
    const held: Listing = { pages: [page(null, '"p1"', [a, b], 'after-b'), page('after-b', '"p2"', [c], null)], wanted: 2 }
    replies.push(sent('"p1-new"', [a], null))
    const listing = await fetchListing(3, ROOT, BY_NAME, held)
    expect(calls).toHaveLength(1)
    expect(listing).toEqual({ pages: [page(null, '"p1-new"', [a], null)], wanted: 1 })
  })
})

describe('showMore', () => {
  it('holds the next page, asking for the ones held with their tags', async () => {
    const queryKey = listingKey(3, SEVEN)
    const queryFn = ({ signal }: { signal: AbortSignal }) => fetchListing(3, SEVEN, BY_NAME, client.getQueryData<Listing>(queryKey), signal)
    replies.push(sent('"p1"', [a, b], 'after-b'))
    await client.fetchQuery({ queryKey, queryFn })

    replies.push(unchanged('"p1"'), sent('"p2"', [c], null))
    await showMore(client, 3, SEVEN, BY_NAME)
    expect(calls.slice(1)).toEqual([
      { url: '/api/projects/3/documents?folder_id=7', tag: '"p1"' },
      { url: '/api/projects/3/documents?folder_id=7&after=after-b', tag: null },
    ])
    expect(client.getQueryData<Listing>(queryKey)).toMatchObject({ wanted: 2 })
    expect(listed(client.getQueryData<Listing>(queryKey))).toEqual([a, b, c])
  })
})

describe('settledMeanwhile', () => {
  const indexing = { ...doc(5, 'new.pdf'), status: 'processing' as const }
  it('is a document shown being indexed now ready, gone, or in another folder', () => {
    expect(settledMeanwhile([indexing, a], [{ ...indexing, status: 'ready' }, a])).toBe(true)
    expect(settledMeanwhile([indexing], [])).toBe(true)
    expect(settledMeanwhile([indexing], [{ ...indexing, folder_id: 7 }])).toBe(true)
  })

  it('is nothing while it is still indexed, nor for a ready one that went', () => {
    expect(settledMeanwhile([indexing], [indexing])).toBe(false)
    expect(settledMeanwhile([a, b], [a])).toBe(false)
  })
})

describe('editListings', () => {
  it('changes a document on whichever listing of the project holds it, and finds it there', () => {
    client.setQueryData<Listing>(listingKey(3, ROOT), { pages: [page(null, '"r"', [a], null)], wanted: 1 })
    client.setQueryData<Listing>(listingKey(3, SEVEN), { pages: [page(null, '"f"', [b, c], null)], wanted: 1 })
    client.setQueryData<Listing>(listingKey(4, ROOT), { pages: [page(null, '"o"', [doc(2, 'other.txt')], null)], wanted: 1 })

    editListings(client, 3, (documents) => documents.flatMap((document) => (document.id === 2 ? [] : [document])))
    expect(listed(client.getQueryData<Listing>(listingKey(3, SEVEN)))).toEqual([c])
    // Another project's listing is left alone, though the ids are the same.
    expect(listed(client.getQueryData<Listing>(listingKey(4, ROOT)))).toHaveLength(1)
    expect(findListed(client, 3, 3)).toEqual(c)
    expect(findListed(client, 3, 2)).toBeUndefined()
  })
})

describe('refreshAfter', () => {
  const NOW = Date.parse('2026-10-23T12:00:00Z')
  const inMinutes = (minutes: number) => new Date(NOW + minutes * 60_000).toISOString()
  const waiting = (id: number, status: Document['status'], retryAt: string, stage: 'parse' | 'enrich' = 'enrich'): Document => ({
    ...doc(id, `${id}.pdf`),
    status,
    stages: { [stage]: { status: 'failed', detail: 'The model is busy.', retry_at: retryAt, deferred: false } },
  })

  it('asks every REFRESH while a document is indexed or queued, and not at all once they are settled', () => {
    expect(refreshAfter([a, { ...b, status: 'processing' }], NOW)).toBe(REFRESH)
    expect(refreshAfter([{ ...a, status: 'queued' }], NOW)).toBe(REFRESH)
    expect(refreshAfter([a, { ...b, status: 'failed' }], NOW)).toBe(false)
    expect(refreshAfter([], NOW)).toBe(false)
  })

  it('waits for the soonest retry to come due, ready documents among them, under a ceiling', () => {
    expect(refreshAfter([waiting(1, 'queued', inMinutes(2), 'parse'), a], NOW)).toBe(WAITING_REFRESH)
    expect(refreshAfter([waiting(1, 'ready', inMinutes(0.5)), waiting(2, 'queued', inMinutes(8), 'parse')], NOW)).toBe(30_000 + REFRESH)
    // Due and not taken yet: the worker takes it any moment.
    expect(refreshAfter([waiting(1, 'ready', inMinutes(-1))], NOW)).toBe(REFRESH)
    // Anything indexed meanwhile is asked about as often as ever.
    expect(refreshAfter([waiting(1, 'ready', inMinutes(8)), { ...b, status: 'processing' }], NOW)).toBe(REFRESH)
  })

  it('takes a queued document without a known retry, from before the server said when, for one indexed', () => {
    const untimed = { ...waiting(1, 'queued', inMinutes(2), 'parse'), stages: { parse: { status: 'failed', detail: 'Busy.', retry_at: null, deferred: false } } } satisfies Document
    expect(refreshAfter([untimed], NOW)).toBe(REFRESH)
  })
})

describe('partlyCountBehind', () => {
  const skipped = (document: Document): Document => ({
    ...document,
    stages: { enrich: { status: 'skipped', detail: 'Summaries and facts need the AI model.', retry_at: null, deferred: false } },
  })

  it('tells when a document listed before became partly indexed or stopped being so, for the project to count again', () => {
    const indexing = { ...a, status: 'processing' } satisfies Document
    expect(partlyCountBehind([indexing, b], [skipped(a), b], 0)).toBe(true)
    expect(partlyCountBehind([skipped(a), b], [{ ...skipped(a), status: 'queued' }, b], 1)).toBe(true)
    expect(partlyCountBehind([indexing, b], [a, b], 0)).toBe(false)
    expect(partlyCountBehind([skipped(a)], [skipped(a)], 1)).toBe(false)
    // A document gone is the deletion's to count.
    expect(partlyCountBehind([skipped(a)], [], 1)).toBe(false)
  })

  it('tells when a folder first listed shows one the project counts none of, indexed before it was opened', () => {
    expect(partlyCountBehind([], [skipped(a)], 0)).toBe(true)
    expect(partlyCountBehind([], [skipped(a)], 1)).toBe(false)
    expect(partlyCountBehind([], [a], 0)).toBe(false)
    // Nor while the project is not held, which asks for its counts as it comes.
    expect(partlyCountBehind([], [skipped(a)], undefined)).toBe(false)
  })
})
