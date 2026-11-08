// A place's documents as the library holds them (a folder's, the reader's favourites or
// recent ones, or the files whose names hold some letters), in an order: the pages it has asked for, each with the tag
// the server named it by, so that asking again while a document is indexed costs a 304 for every
// page that has not changed (`list_documents` in backend/app/api/documents.py). Each place in each
// order is a query of its own, and an open document one more, so no request ever carries the
// whole project.

import { queryOptions, useQueries, useQueryClient } from '@tanstack/react-query'
import type { QueryClient, QueryKey } from '@tanstack/react-query'
import { api, ApiError, isIndexing } from '../api.ts'
import type { Document, DocumentSort, ListingPlace, Project, SortOrder } from '../api.ts'
import { unfinishedStages, waitingRetry } from '../indexing.ts'

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
 * without a known retry, or its view is being made (backend/app/conversion.py), whose pages and
 * thumbnail come once it is, else just after the soonest retry is due (when the worker takes it),
 * at most WAITING_REFRESH from now. A ready document whose optional stage waits counts, so its
 * retry is seen when it runs.
 */
export function refreshAfter(documents: Document[], now = Date.now()): number | false {
  let soonest = Infinity
  for (const document of documents) {
    const retry = waitingRetry(document)
    if (retry) soonest = Math.min(soonest, retry.at.getTime())
    else if (isIndexing(document) || document.view_state === 'queued') return REFRESH
  }
  if (soonest === Infinity) return false
  return askAgainAt(soonest, now)
}

/** When to ask again about something whose retry is due at `due`: just after, held between REFRESH and WAITING_REFRESH from now. */
export const askAgainAt = (due: number, now: number) => Math.min(Math.max(due - now + REFRESH, REFRESH), WAITING_REFRESH)

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

/** The order a listing runs in. */
export interface Arrangement {
  sort: DocumentSort
  order: SortOrder
}
/** The library's first order: by name, as the server lists when none is named. */
export const BY_NAME: Arrangement = { sort: 'name', order: 'asc' }

/** A place as a query key: a folder by its id, the root's null, the others by a word of their own, a name filter's alone `named`. */
export function placeKey(place: ListingPlace): number | string | null {
  if ('folder' in place) return place.folder
  if ('favorites' in place) return 'favorites'
  return 'recent' in place ? 'recent' : 'named'
}

/** The letters a place's file names are filtered by, or null for none. */
export const nameOf = (place: ListingPlace): string | null => ('name' in place && place.name) || null

// A name filter comes last, so that whatever asks again for a place's listings asks for its filtered ones too.
export const listingKey = (projectId: number, place: ListingPlace, arrangement: Arrangement = BY_NAME) =>
  ['documents', projectId, placeKey(place), arrangement.sort, arrangement.order, nameOf(place)] as const
/** Every order of a place's listing, for a change of what the place holds. */
export const placeListingsKey = (projectId: number, place: ListingPlace) => ['documents', projectId, placeKey(place)] as const
/** Every folder's listing of the project, for a change that touches documents wherever they are. */
export const listingsKey = (projectId: number) => ['documents', projectId] as const
export const documentKey = (id: number) => ['document', id] as const

/**
 * The place's documents again: as many pages as are held or wanted, each asked with the tag
 * of the page held for the same cursor, so that a page that has not changed is kept as it was.
 * A page that changed may end on another document, and the pages after it are then asked for
 * without a tag.
 */
export async function fetchListing(
  projectId: number,
  place: ListingPlace,
  arrangement: Arrangement,
  held: Listing | undefined,
  signal?: AbortSignal,
): Promise<Listing> {
  const pages: ListedPage[] = []
  let after: string | null = null
  while (pages.length < Math.max(held?.wanted ?? 1, 1)) {
    const previous: ListedPage | undefined = held?.pages[pages.length]
    const known = previous?.after === after ? previous : undefined
    const fetched = await api.documentPage(projectId, place, arrangement, after, known?.etag ?? null, signal)
    // Null only answers a page asked with the tag of one held, which is then still that page.
    const page: ListedPage = fetched ? { after, etag: fetched.etag, documents: fetched.documents, next_after: fetched.next_after } : known!
    pages.push(page)
    if (page.next_after == null) break
    after = page.next_after
  }
  return { pages, wanted: pages.length }
}

/** Hold one page more of the place: its listing is fetched again with the next page in it. */
export async function showMore(queryClient: QueryClient, projectId: number, place: ListingPlace, arrangement: Arrangement) {
  const queryKey = listingKey(projectId, place, arrangement)
  // A fetch under way would put back the listing it began with, and the page wanted with it.
  await queryClient.cancelQueries({ queryKey })
  queryClient.setQueryData<Listing>(queryKey, (listing) => listing && { ...listing, wanted: listing.pages.length + 1 })
  await queryClient.refetchQueries({ queryKey })
}

export const listed = (listing: Listing | undefined): Document[] => listing?.pages.flatMap((page) => page.documents) ?? []
export const hasMore = (listing: Listing | undefined) => listing?.pages.at(-1)?.next_after != null
/** Whether the next page has been asked for and not yet come. */
export const growing = (listing: Listing | undefined) => listing != null && listing.wanted > listing.pages.length

/**
 * Whether the project's count of partly indexed documents (`partly_indexed_count`, which `counted`
 * is while the project is held) is behind what a listing shows: a document listed before became
 * partly indexed or stopped being so (an upload whose summaries failed, a retry that gave up,
 * another member's "Index the rest"), or one is listed while the project counts none (indexed
 * before its folder was first opened).
 */
export function partlyCountBehind(before: Document[], after: Document[], counted: number | undefined): boolean {
  const held = new Map(before.map((document) => [document.id, unfinishedStages(document).join()]))
  const changed = after.some((document) => held.has(document.id) && held.get(document.id) !== unfinishedStages(document).join())
  return changed || (counted === 0 && after.some((document) => unfinishedStages(document).length > 0))
}

/**
 * Whether a document the listing showed being indexed has settled since, or left it: then the folders' counts may have
 * moved, and so may another listing that shows it.
 */
export function settledMeanwhile(before: Document[], after: Document[]): boolean {
  const now = new Map(after.map((document) => [document.id, document]))
  return before.some((document) => {
    if (!isIndexing(document)) return false
    const found = now.get(document.id)
    return found === undefined || !isIndexing(found) || found.folder_id !== document.folder_id
  })
}

/**
 * The listings of these places in the order arranged, in the same order; each is asked again while
 * it shows a document being indexed or waiting for a retry, and the project again once its count
 * of partly indexed documents is behind what a listing shows.
 */
export function useListings(projectId: number, places: ListingPlace[], arrangement: Arrangement) {
  const queryClient = useQueryClient()
  return useQueries({
    queries: places.map((place) =>
      queryOptions({
        queryKey: listingKey(projectId, place, arrangement),
        queryFn: async ({ signal }) => {
          const held = queryClient.getQueryData<Listing>(listingKey(projectId, place, arrangement))
          const listing = await fetchListing(projectId, place, arrangement, held, signal)
          const counted = queryClient.getQueryData<Project>(['project', projectId])?.partly_indexed_count
          if (partlyCountBehind(listed(held), listed(listing), counted)) {
            void queryClient.invalidateQueries({ queryKey: ['project', projectId], exact: true })
          }
          if (settledMeanwhile(listed(held), listed(listing))) {
            const own = JSON.stringify(listingKey(projectId, place, arrangement))
            void queryClient.invalidateQueries({ queryKey: ['folders', projectId], exact: true })
            void queryClient.invalidateQueries({
              queryKey: listingsKey(projectId),
              predicate: (query) => JSON.stringify(query.queryKey) !== own,
            })
          }
          return listing
        },
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
