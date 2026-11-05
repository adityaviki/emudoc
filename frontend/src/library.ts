// The library's ways of reading a project (backend/app/tags.py, document_filter.py, filing.py): its places and orders as
// the page names them, a document's tags, what narrows a search and what a filing rule does in words, why either as written
// cannot be used, and where the grid's keys move its cursor.

import { THUMBNAIL_TYPES } from './api.ts'
import type { Document, DocumentFilter, DocumentSort, FilingRuleInput, ListingPlace, SortOrder, Tag, TagColor } from './api.ts'
import { fieldWords } from './factTable.ts'
import { formatDate } from './format.ts'
import { scanPassed } from './indexing.ts'
import { REVIEW_LABELS } from './reviews.ts'
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

/** How the library shows a place: a tree of rows, or a grid of tiles. */
export type LibraryLayout = 'list' | 'grid'

export const TAG_COLOR_LABELS: Record<TagColor, string> = {
  grey: 'Grey',
  red: 'Red',
  orange: 'Orange',
  yellow: 'Yellow',
  green: 'Green',
  teal: 'Teal',
  blue: 'Blue',
  purple: 'Purple',
  pink: 'Pink',
}

/** The tags a document carries as its project holds them, in the order they were put on it; one gone meanwhile is left out. */
export function tagsOf(document: Pick<Document, 'tag_ids'>, tags: Tag[]): Tag[] {
  const byId = new Map(tags.map((tag) => [tag.id, tag]))
  return document.tag_ids.flatMap((id) => byId.get(id) ?? [])
}

/** Its tags in a sentence, for whoever reads the library by ear. */
export function tagsWords(carried: Tag[]): string | null {
  return carried.length ? `Tagged ${carried.map((tag) => tag.name).join(', ')}` : null
}

/**
 * The place of the library's that the view names, or null for a folder's: a tag's, the favourites or the recent documents,
 * narrowed to the files whose names hold `name`; a name alone is every such file of the project's, wherever the folder.
 */
export function placeOf(view: Pick<LibraryView, 'tag' | 'favorites' | 'recent' | 'name'>): ListingPlace | null {
  const named = view.name ? { name: view.name } : {}
  if (view.tag != null) return { tag: view.tag, ...named }
  if (view.favorites) return { favorites: true, ...named }
  if (view.recent) return { recent: true, ...named }
  return view.name ? { name: view.name } : null
}

/** Whether a place is the files of the whole project whose names hold some letters, which "All files" shows filtered. */
export const allNamed = (place: ListingPlace): place is { name: string } =>
  !('folder' in place || 'tag' in place || 'favorites' in place || 'recent' in place)

/** What a place that is not a folder is called; the files of the project a name filter finds are "All files". */
export function placeName(place: ListingPlace, tags: Tag[]): string {
  if ('favorites' in place) return 'Favourites'
  if ('recent' in place) return 'Recent'
  if ('tag' in place) return tags.find((tag) => tag.id === place.tag)?.name ?? 'A tag'
  return 'All files'
}

/** What a place that is not a folder holds, when it holds nothing. */
export function emptyPlaceWords(place: ListingPlace): { title: string; body: string } {
  if ('name' in place && place.name) {
    return {
      title: `No file’s name holds “${place.name}”`,
      body: allNamed(place)
        ? 'The filter looks for these letters, in any case, in the names of the files in every folder of the project.'
        : 'The filter looks for these letters, in any case, in the names of the files listed here.',
    }
  }
  if ('favorites' in place) {
    return { title: 'No favourites yet', body: 'Choose “Add to favourites” in a document’s menu to find it here again, wherever it is in the project.' }
  }
  if ('recent' in place) return { title: 'Nothing opened yet', body: 'The documents you open in this project are listed here, the latest first.' }
  return { title: 'No document has this tag', body: 'Choose “Tags…” in a document’s menu to give it one.' }
}

export const EMPTY_FILTER: DocumentFilter = { tags: [], every_tag: false, types: [], after: null, before: null, reviews: [], folder_id: null, mentions: [] }

/** How many searches a search may be within (MAX_MENTIONS in backend/app/schemas.py). */
export const MAX_WITHIN = 5

/** What narrows a filter that a member can take off alone: one of its parts, or one of the searches it is within by its place. */
export type FilterPart = 'tags' | 'types' | 'dates' | 'reviews' | 'folder' | number

/** Words in a list a sentence reads: `a, b or c`. */
export const listed = (words: string[], last: 'and' | 'or') =>
  words.length < 2 ? (words[0] ?? '') : `${words.slice(0, -1).join(', ')} ${last} ${words.at(-1)}`

/** Each part a filter narrows by in words, its tags and folder by their names as they are now, in the order a sentence lists them. */
export function filterParts(filter: DocumentFilter, tags: Tag[], folder: string | null): { part: FilterPart; words: string }[] {
  const parts: { part: FilterPart; words: string }[] = []
  const names = filter.tags.map((id) => tags.find((tag) => tag.id === id)?.name ?? 'a deleted tag').map((name) => `“${name}”`)
  if (names.length) parts.push({ part: 'tags', words: `tagged ${listed(names, filter.every_tag ? 'and' : 'or')}` })
  if (filter.types.length) parts.push({ part: 'types', words: `of type ${listed(filter.types, 'or')}` })
  if (filter.after && filter.before) parts.push({ part: 'dates', words: `dated ${formatDate(filter.after)} to ${formatDate(filter.before)}` })
  else if (filter.after) parts.push({ part: 'dates', words: `dated ${formatDate(filter.after)} or later` })
  else if (filter.before) parts.push({ part: 'dates', words: `dated ${formatDate(filter.before)} or earlier` })
  if (filter.reviews.length) parts.push({ part: 'reviews', words: `${listed(filter.reviews.map((state) => REVIEW_LABELS[state].toLowerCase()), 'or')} in review` })
  if (filter.folder_id != null) parts.push({ part: 'folder', words: `in ${folder ?? 'a folder that is gone'}` })
  filter.mentions.forEach((words, index) => parts.push({ part: index, words: `mentioning “${words}”` }))
  return parts
}

/** The filter without one of its parts. */
export function withoutPart(filter: DocumentFilter, part: FilterPart): DocumentFilter {
  if (typeof part === 'number') return { ...filter, mentions: filter.mentions.filter((_, index) => index !== part) }
  const emptied: Record<Exclude<FilterPart, number>, Partial<DocumentFilter>> = {
    tags: { tags: [], every_tag: false },
    types: { types: [] },
    dates: { after: null, before: null },
    reviews: { reviews: [] },
    folder: { folder_id: null },
  }
  return { ...filter, ...emptied[part] }
}

/** Whether the filter narrows anything, or matches every document of the project. */
export const narrows = (filter: DocumentFilter) => filterParts(filter, [], null).length > 0

/** What a filter matches, in words: each part it narrows by, its tags and folder by their names as they are now. */
export function filterWords(filter: DocumentFilter, tags: Tag[], folder: string | null): string {
  const parts = filterParts(filter, tags, folder).map(({ words }) => words)
  return parts.length ? `Documents ${parts.join(', ')}` : 'Every document of the project'
}

/** Why a filter's dates cannot be searched by, in the words the server would refuse them with (BACKWARDS in backend/app/document_filter.py); null when they can. */
export function datesProblem(filter: Pick<DocumentFilter, 'after' | 'before'>): string | null {
  if (filter.after && filter.before && filter.after > filter.before) return 'Its earliest date is after its latest. Choose dates the other way round.'
  return null
}

export const NEW_RULE: FilingRuleInput = { condition: 'type', field: null, value: '', action: 'folder', folder_id: null, tag_id: null, enabled: true }

/** A filing rule in a sentence, its folder and tag by their names as they are now. */
export function ruleSentence(rule: FilingRuleInput, folder: string | null, tag: string | null): string {
  const what =
    rule.condition === 'type'
      ? `A new ${rule.value ?? ''} document`
      : rule.condition === 'fact'
        ? `A new document whose ${fieldWords(rule.field ?? 'field')} holds “${rule.value ?? ''}”`
        : 'Every new document'
  const under = folder ?? 'the top of the library'
  if (rule.action === 'folder') return `${what} goes into ${under}`
  if (rule.action === 'by_year') return `${what} is filed under ${under}, a folder for its year`
  if (rule.action === 'by_month') return `${what} is filed under ${under}, a folder for its year and one for its month`
  return `${what} is tagged “${tag ?? 'a deleted tag'}”`
}

/** Why a filing rule as written cannot be saved, in the words the server would refuse it with; null when it can. */
export function ruleProblem(rule: FilingRuleInput): string | null {
  if (rule.condition === 'type' && !rule.value?.trim()) return 'Say which type of document the rule files: the type the model gives it, such as invoice.'
  if (rule.condition === 'fact' && (!rule.field?.trim() || !rule.value?.trim())) {
    return 'Say which fact the rule reads, by its field’s name, and the words its value holds.'
  }
  if (rule.action === 'folder' && rule.folder_id == null) return 'Choose the folder the rule moves documents into.'
  if (rule.action === 'tag' && rule.tag_id == null) return 'Choose the tag the rule puts on documents.'
  return null
}

/** The rule as the server takes it: only what its condition and action use. */
export function ruleInput(rule: FilingRuleInput): FilingRuleInput {
  return {
    condition: rule.condition,
    field: rule.condition === 'fact' ? (rule.field?.trim() ?? null) : null,
    value: rule.condition === 'any' ? null : (rule.value?.trim() ?? null),
    action: rule.action,
    folder_id: rule.action === 'tag' ? null : rule.folder_id,
    tag_id: rule.action === 'tag' ? rule.tag_id : null,
    enabled: rule.enabled,
  }
}

/** Whether the server draws the document a thumbnail: a PDF, a scan or an image, once its scan has passed. */
export const hasThumbnail = (document: Document) => THUMBNAIL_TYPES.has(document.mime_type) && document.status !== 'quarantined' && scanPassed(document)

/**
 * Where a key moves the grid's cursor from `index`, of `count` tiles in rows of `columns`: the arrows by a tile and by a
 * row, Home and End to the row's ends, and with Ctrl to the grid's; null for a key it does not take, or none to move to.
 */
export function gridMove(key: string, index: number, columns: number, count: number, ctrl = false): number | null {
  if (count === 0) return null
  if (index < 0) return 0
  const rowStart = index - (index % columns)
  const target = {
    ArrowLeft: index - 1,
    ArrowRight: index + 1,
    ArrowUp: index - columns,
    ArrowDown: index + columns,
    Home: ctrl ? 0 : rowStart,
    End: ctrl ? count - 1 : Math.min(rowStart + columns, count) - 1,
  }[key]
  if (target === undefined) return null
  // Down from a row above a shorter last one goes to the last tile; nothing else moves past either end.
  if (key === 'ArrowDown' && target >= count) return rowStart + columns < count ? count - 1 : null
  return target >= 0 && target < count ? target : null
}
