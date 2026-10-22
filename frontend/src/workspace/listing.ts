// A folder's documents as the library holds them: the pages it has asked for, each with the
// tag the server named it by, so that asking again while a document is indexed costs a 304
// for every page that has not changed (`list_documents` in backend/app/api/documents.py).
// Each folder is a query of its own, and an open document one more, so no request ever
// carries the whole project.

import { queryOptions, useQueries, useQueryClient } from '@tanstack/react-query'
import type { QueryClient, QueryKey } from '@tanstack/react-query'
import { api, ApiError, isIndexing } from '../api.ts'
import type { Document } from '../api.ts'
import { waitingRetry } from '../indexing.ts'

/** How often a folder or a document being indexed is asked about again, in milliseconds. */
export const REFRESH = 1500
/**
 * The longest a folder or a document waiting for a retry goes unasked, in milliseconds: the
 * day's spending limit may hold it for hours, while another member's "Index again" or a
 * deletion would change it sooner.
 */
export const WAITING_REFRESH = 60_000

/**
 * When to ask again about a folder or a document that shows these documents, in milliseconds,
 * or false for not until something else asks: every REFRESH while one is indexed or queued
 * without a known retry, else just after the soonest retry is due (when the worker takes it),
 * at most WAITING_REFRESH from now. A ready document whose optional stage waits counts, so its
 * retry is seen when it runs.
 */
export function refreshAfter(documents: Document[], now = Date.now()): number | false {
  let soonest = Infinity
  for (const document of documents) {
    const retry = waitingRetry(document)
    if (retry) soonest = Math.min(soonest, retry.at.getTime())
    else if (isIndexing(document)) return REFRESH
  }
  if (soonest === Infinity) return false
  return Math.min(Math.max(soonest - now + REFRESH, REFRESH), WAITING_REFRESH)
}

export interface ListedPage {
  /** The cursor the page was asked for with; null for the first. */
  after: string | null
  etag: string | null
  documents: Document[]
  next_after: string | null
}

export interface Listing {
  pages: ListedPage[]
  /** How many pages to hold: one more than there are while the next is being asked for. */
  wanted: number
}

export const listingKey = (projectId: number, folderId: number | null) => ['documents', projectId, folderId] as const
/** Every folder's listing of the project, for a change that touches documents wherever they are. */
export const listingsKey = (projectId: number) => ['documents', projectId] as const
export const documentKey = (id: number) => ['document', id] as const

/**
 * The folder's documents again: as many pages as are held or wanted, each asked with the tag
 * of the page held for the same cursor, so that a page that has not changed is kept as it was.
 * A page that changed may end on another document, and the pages after it are then asked for
 * without a tag.
 */
export async function fetchListing(
  projectId: number,
  folderId: number | null,
  held: Listing | undefined,
  signal?: AbortSignal,
): Promise<Listing> {
  const pages: ListedPage[] = []
  let after: string | null = null
  while (pages.length < Math.max(held?.wanted ?? 1, 1)) {
    const previous: ListedPage | undefined = held?.pages[pages.length]
    const known = previous?.after === after ? previous : undefined
    const fetched = await api.documentPage(projectId, folderId, after, known?.etag ?? null, signal)
    // Null only answers a page asked with the tag of one held, which is then still that page.
    const page: ListedPage = fetched ? { after, etag: fetched.etag, documents: fetched.documents, next_after: fetched.next_after } : known!
    pages.push(page)
    if (page.next_after == null) break
    after = page.next_after
  }
  return { pages, wanted: pages.length }
}

/** Hold one page more of the folder: its listing is fetched again with the next page in it. */
export async function showMore(queryClient: QueryClient, projectId: number, folderId: number | null) {
  const queryKey = listingKey(projectId, folderId)
  // A fetch under way would put back the listing it began with, and the page wanted with it.
  await queryClient.cancelQueries({ queryKey })
  queryClient.setQueryData<Listing>(queryKey, (listing) => listing && { ...listing, wanted: listing.pages.length + 1 })
  await queryClient.refetchQueries({ queryKey })
}

export const listed = (listing: Listing | undefined): Document[] => listing?.pages.flatMap((page) => page.documents) ?? []
export const hasMore = (listing: Listing | undefined) => listing?.pages.at(-1)?.next_after != null
/** Whether the next page has been asked for and not yet come. */
export const growing = (listing: Listing | undefined) => listing != null && listing.wanted > listing.pages.length

/** The listings of these folders, in the same order; each is asked again while it shows a document being indexed or waiting for a retry. */
export function useListings(projectId: number, folderIds: (number | null)[]) {
  const queryClient = useQueryClient()
  return useQueries({
    queries: folderIds.map((folderId) =>
      queryOptions({
        queryKey: listingKey(projectId, folderId),
        queryFn: ({ signal }) =>
          fetchListing(projectId, folderId, queryClient.getQueryData<Listing>(listingKey(projectId, folderId)), signal),
        refetchInterval: (query) => refreshAfter(listed(query.state.data)),
      }),
    ),
  })
}

/**
 * One document, asked again while it is being indexed or waits for a retry. A listing that
 * holds it shows it at once while it is fetched, so a document opened from the library opens
 * without a wait.
 */
export function documentQuery(queryClient: QueryClient, projectId: number, id: number) {
  return queryOptions({
    queryKey: documentKey(id),
    queryFn: () => api.document(id),
    placeholderData: () => findListed(queryClient, projectId, id),
    refetchInterval: (query) => refreshAfter(query.state.data ? [query.state.data] : []),
  })
}

/** Whether a document's query failed because it is no longer there (deleted, here or elsewhere). */
export const isGone = (error: Error | null) => error instanceof ApiError && error.status === 404

export function findListed(queryClient: QueryClient, projectId: number, id: number): Document | undefined {
  for (const [, listing] of queryClient.getQueriesData<Listing>({ queryKey: listingsKey(projectId) })) {
    const found = listed(listing).find((document) => document.id === id)
    if (found) return found
  }
  return undefined
}

/** Change the documents of every listing of the project held, as an optimistic update does. */
export function editListings(queryClient: QueryClient, projectId: number, change: (documents: Document[]) => Document[]) {
  queryClient.setQueriesData<Listing>(
    { queryKey: listingsKey(projectId) },
    (listing) => listing && { ...listing, pages: listing.pages.map((page) => ({ ...page, documents: change(page.documents) })) },
  )
}

/** The listings as they are, for putting back when the server refuses a change. */
export async function snapshotListings(queryClient: QueryClient, projectId: number) {
  await queryClient.cancelQueries({ queryKey: listingsKey(projectId) })
  return queryClient.getQueriesData<Listing>({ queryKey: listingsKey(projectId) })
}

export function restore(queryClient: QueryClient, snapshot: [QueryKey, unknown][] | undefined) {
  for (const [queryKey, data] of snapshot ?? []) queryClient.setQueryData(queryKey, data)
}
