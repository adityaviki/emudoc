// The library's ways of reading a project (backend/app/api/documents.py, document_filter.py): its places and orders as
// the page names them, and what narrows a search in words and why it cannot be used as written.

import type { DocumentFilter, DocumentSort, Folder, ListingPlace, SortOrder } from './api.ts'
import { formatDate } from './format.ts'
import type { LibraryView } from './workspace/url.ts'

/** What each order is called, and how each of its directions reads. */
export const SORTS: Record<DocumentSort, { label: string; asc: string; desc: string }> = {
  name: { label: 'Name', asc: 'A to Z', desc: 'Z to A' },
  added: { label: 'Date added', asc: 'Oldest first', desc: 'Newest first' },
  date: { label: 'Document date', asc: 'Oldest first', desc: 'Newest first' },
  size: { label: 'Size', asc: 'Smallest first', desc: 'Largest first' },
}
export const SORT_NAMES = Object.keys(SORTS) as DocumentSort[]
/** The direction an order runs in when it is chosen: names from A, the rest the newest or the largest first. */
export const FIRST_ORDER: Record<DocumentSort, SortOrder> = { name: 'asc', added: 'desc', date: 'desc', size: 'desc' }

/** An order in words, for the button that changes it. */
export const sortWords = (sort: DocumentSort, order: SortOrder) => `${SORTS[sort].label}, ${SORTS[sort][order].toLowerCase()}`

/**
 * The place of the library's that the view names, or null for a folder's: the favourites or the recent documents, narrowed
 * to the files whose names hold the search's words; the words alone are every such file of the project's, wherever the folder.
 */
export function placeOf(view: Pick<LibraryView, 'favorites' | 'recent' | 'search'>): ListingPlace | null {
  const name = view.search?.q
  const named = name ? { name } : {}
  if (view.favorites) return { favorites: true, ...named }
  if (view.recent) return { recent: true, ...named }
  return name ? { name } : null
}

/** Whether a place is the files of the whole project whose names hold some letters, which "All files" shows searched. */
export const allNamed = (place: ListingPlace): place is { name: string } =>
  !('folder' in place || 'favorites' in place || 'recent' in place)

/** What a place that is not a folder is called; the files of the project whose names a search matches are "All files". */
export function placeName(place: ListingPlace): string {
  if ('favorites' in place) return 'Favourites'
  if ('recent' in place) return 'Recent'
  return 'All files'
}

/** What the search's project files say when no name holds its words, in any case: of every folder's files, a favourite's or a recent one's. */
export function noNameWords(place: ListingPlace & { name: string }): string {
  const whose = 'favorites' in place ? 'No favourite’s name' : 'recent' in place ? 'No recent file’s name' : 'No project file’s name'
  return `${whose} holds “${place.name}”.`
}

/** What a place that is not a folder holds, when it holds nothing. */
export function emptyPlaceWords(place: ListingPlace): { title: string; body: string } {
  if ('favorites' in place) {
    return { title: 'No favourites yet', body: 'Choose “Add to favourites” in a document’s menu to find it here again, wherever it is in the project.' }
  }
  return { title: 'Nothing opened yet', body: 'The documents you open in this project are listed here, the latest first.' }
}

export const EMPTY_FILTER: DocumentFilter = { types: [], after: null, before: null, folder_id: null, mentions: [] }

/** How many searches a search may be within (MAX_MENTIONS in backend/app/schemas.py). */
export const MAX_WITHIN = 5

/** What narrows a filter that a member can take off alone: one of its parts, or one of the searches it is within by its place. */
export type FilterPart = 'types' | 'dates' | 'folder' | number

/** Words in a list a sentence reads: `a, b or c`. */
export const listed = (words: string[], last: 'and' | 'or') =>
  words.length < 2 ? (words[0] ?? '') : `${words.slice(0, -1).join(', ')} ${last} ${words.at(-1)}`

/** Each part a filter narrows by in words, its folder by its path as it is now, in the order a sentence lists them. */
export function filterParts(filter: DocumentFilter, folder: string | null): { part: FilterPart; words: string }[] {
  const parts: { part: FilterPart; words: string }[] = []
  if (filter.types.length) parts.push({ part: 'types', words: `of type ${listed(filter.types, 'or')}` })
  if (filter.after && filter.before) parts.push({ part: 'dates', words: `dated ${formatDate(filter.after)} to ${formatDate(filter.before)}` })
  else if (filter.after) parts.push({ part: 'dates', words: `dated ${formatDate(filter.after)} or later` })
  else if (filter.before) parts.push({ part: 'dates', words: `dated ${formatDate(filter.before)} or earlier` })
  if (filter.folder_id != null) parts.push({ part: 'folder', words: `in ${folder ?? 'a folder that is gone'}` })
  filter.mentions.forEach((words, index) => parts.push({ part: index, words: `mentioning “${words}”` }))
  return parts
}

/** The filter without one of its parts. */
export function withoutPart(filter: DocumentFilter, part: FilterPart): DocumentFilter {
  if (typeof part === 'number') return { ...filter, mentions: filter.mentions.filter((_, index) => index !== part) }
  const emptied: Record<Exclude<FilterPart, number>, Partial<DocumentFilter>> = {
    types: { types: [] },
    dates: { after: null, before: null },
    folder: { folder_id: null },
  }
  return { ...filter, ...emptied[part] }
}

/** Whether the filter narrows anything, or matches every document of the project. */
export const narrows = (filter: DocumentFilter) => filterParts(filter, null).length > 0

/** What a filter matches, in words: each part it narrows by, its folder by its path as it is now. */
export function filterWords(filter: DocumentFilter, folder: string | null): string {
  const parts = filterParts(filter, folder).map(({ words }) => words)
  return parts.length ? `Documents ${parts.join(', ')}` : 'Every document of the project'
}

/** Why a filter's dates cannot be searched by, in the words the server would refuse them with (BACKWARDS in backend/app/document_filter.py); null when they can. */
export function datesProblem(filter: Pick<DocumentFilter, 'after' | 'before'>): string | null {
  if (filter.after && filter.before && filter.after > filter.before) return 'Its earliest date is after its latest. Choose dates the other way round.'
  return null
}

/** A folder a form offers, named by its path from the project's top. */
export interface FolderChoice {
  id: number
  label: string
}

/** The project's folders in the library's order, each by its path: "Inbox", "Inbox/Invoices"; My files' folders too. */
export function folderChoices(folders: Pick<Folder, 'id' | 'parent_id' | 'name'>[]): FolderChoice[] {
  const children = new Map<number | null, Pick<Folder, 'id' | 'parent_id' | 'name'>[]>()
  for (const folder of folders) children.set(folder.parent_id, [...(children.get(folder.parent_id) ?? []), folder])
  const found: FolderChoice[] = []
  const visit = (parentId: number | null, path: string) => {
    const sorted = [...(children.get(parentId) ?? [])].sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: 'base' }))
    for (const folder of sorted) {
      const label = path ? `${path}/${folder.name}` : folder.name
      found.push({ id: folder.id, label })
      visit(folder.id, label)
    }
  }
  visit(null, '')
  return found
}
