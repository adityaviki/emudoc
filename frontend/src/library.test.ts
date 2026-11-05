import { describe, expect, it } from 'vitest'
import type { Document, Tag } from './api.ts'
import { formatDate } from './format.ts'
import {
  EMPTY_FILTER,
  allNamed,
  emptyPlaceWords,
  filterParts,
  filterWords,
  gridMove,
  datesProblem,
  hasThumbnail,
  placeName,
  placeOf,
  narrows,
  sortWords,
  tagsOf,
  tagsWords,
  withoutPart,
} from './library.ts'

const tag = (id: number, name: string): Tag => ({ id, name, color: 'grey', document_count: 0 })
const TAGS = [tag(1, 'Urgent'), tag(2, 'Paid')]

describe('a place', () => {
  it('is named by the view, and called by its own name or the library’s', () => {
    expect(placeOf({})).toBeNull()
    expect(placeOf({ tag: 2 })).toEqual({ tag: 2 })
    expect(placeOf({ favorites: true })).toEqual({ favorites: true })
    expect(placeOf({ recent: true })).toEqual({ recent: true })
    expect(placeName({ tag: 2 }, TAGS)).toBe('Paid')
    expect(placeName({ tag: 9 }, TAGS)).toBe('A tag')
    expect(placeName({ favorites: true }, TAGS)).toBe('Favourites')
    expect(placeName({ recent: true }, TAGS)).toBe('Recent')
  })

  it('is narrowed by a name, which alone takes the files of every folder', () => {
    expect(placeOf({ name: 'inv' })).toEqual({ name: 'inv' })
    expect(placeOf({ tag: 2, name: 'inv' })).toEqual({ tag: 2, name: 'inv' })
    expect(placeOf({ recent: true, name: 'inv' })).toEqual({ recent: true, name: 'inv' })
    expect(placeOf({ name: '' })).toBeNull()
    expect(allNamed({ name: 'inv' })).toBe(true)
    expect(allNamed({ tag: 2, name: 'inv' })).toBe(false)
    expect(allNamed({ folder: null })).toBe(false)
    expect(placeName({ name: 'inv' }, TAGS)).toBe('All files')
    expect(placeName({ tag: 2, name: 'inv' }, TAGS)).toBe('Paid')
    expect(emptyPlaceWords({ name: 'inv' })).toEqual({
      title: 'No file’s name holds “inv”',
      body: 'The filter looks for these letters, in any case, in the names of the files in every folder of the project.',
    })
    expect(emptyPlaceWords({ favorites: true, name: 'inv' }).body).toBe('The filter looks for these letters, in any case, in the names of the files listed here.')
    expect(emptyPlaceWords({ favorites: true }).title).toBe('No favourites yet')
  })

  it('is listed in an order its button words', () => {
    expect(sortWords('name', 'asc')).toBe('Name, a to z')
    expect(sortWords('date', 'desc')).toBe('Document date, newest first')
    expect(sortWords('size', 'asc')).toBe('Size, smallest first')
  })
})

describe('a document’s tags', () => {
  it('are the project’s, in the order put on it, one gone left out', () => {
    const carried = tagsOf({ tag_ids: [2, 7, 1] }, TAGS)
    expect(carried.map((one) => one.name)).toEqual(['Paid', 'Urgent'])
    expect(tagsWords(carried)).toBe('Tagged Paid, Urgent')
    expect(tagsWords([])).toBeNull()
  })
})

describe('a filter', () => {
  it('says what it matches, its tags and folder by their names', () => {
    expect(filterWords(EMPTY_FILTER, TAGS, null)).toBe('Every document of the project')
    expect(filterWords({ ...EMPTY_FILTER, tags: [1, 2], every_tag: true }, TAGS, null)).toBe('Documents tagged “Urgent” and “Paid”')
    expect(
      filterWords({ ...EMPTY_FILTER, tags: [1, 9], types: ['invoice', 'credit note'], reviews: ['to_review', 'disputed'], folder_id: 3 }, TAGS, 'Invoices/2024'),
    ).toBe('Documents tagged “Urgent” or “a deleted tag”, of type invoice or credit note, to review or disputed in review, in Invoices/2024')
    expect(filterWords({ ...EMPTY_FILTER, folder_id: 3 }, TAGS, null)).toBe('Documents in a folder that is gone')
    expect(filterWords({ ...EMPTY_FILTER, after: '2024-01-15' }, TAGS, null)).toMatch(/^Documents dated .+ or later$/)
  })

  it('takes its dates the right way round', () => {
    expect(datesProblem(EMPTY_FILTER)).toBeNull()
    expect(datesProblem({ after: '2024-04-01', before: '2024-01-01' })).toMatch(/^Its earliest date is after/)
    expect(datesProblem({ after: '2024-01-01', before: '2024-03-31' })).toBeNull()
  })

  it('says the words its documents mention', () => {
    const mentioning = { ...EMPTY_FILTER, types: ['invoice'], mentions: ['late delivery', 'penalty'] }
    expect(filterWords(mentioning, TAGS, null)).toBe('Documents of type invoice, mentioning “late delivery”, mentioning “penalty”')
  })
})

describe('a search', () => {
  const filter = {
    ...EMPTY_FILTER,
    tags: [1, 2],
    every_tag: true,
    after: '2024-01-01',
    reviews: ['disputed' as const],
    folder_id: 7,
    mentions: ['late delivery', 'penalty'],
  }

  it('is narrowed by parts a member takes off one by one, each search it is within alone', () => {
    expect(filterParts(filter, TAGS, 'Invoices').map(({ part, words }) => [part, words])).toEqual([
      ['tags', 'tagged “Urgent” and “Paid”'],
      ['dates', `dated ${formatDate('2024-01-01')} or later`],
      ['reviews', 'disputed in review'],
      ['folder', 'in Invoices'],
      [0, 'mentioning “late delivery”'],
      [1, 'mentioning “penalty”'],
    ])
    expect(withoutPart(filter, 'tags')).toMatchObject({ tags: [], every_tag: false, after: '2024-01-01' })
    expect(withoutPart(filter, 'dates')).toMatchObject({ after: null, before: null })
    expect(withoutPart(filter, 'folder').folder_id).toBeNull()
    expect(withoutPart(filter, 0).mentions).toEqual(['penalty'])
    expect(withoutPart(filter, 1).mentions).toEqual(['late delivery'])
    expect(narrows(EMPTY_FILTER)).toBe(false)
    expect(narrows({ ...EMPTY_FILTER, mentions: ['x'] })).toBe(true)
  })
})

describe('the grid', () => {
  // Seven tiles in rows of three: 0 1 2 / 3 4 5 / 6.
  it('moves by a tile and by a row, and to a row’s ends or the grid’s', () => {
    expect(gridMove('ArrowRight', 2, 3, 7)).toBe(3)
    expect(gridMove('ArrowLeft', 0, 3, 7)).toBeNull()
    expect(gridMove('ArrowDown', 1, 3, 7)).toBe(4)
    expect(gridMove('ArrowUp', 1, 3, 7)).toBeNull()
    // Down from above the short last row lands on its last tile; from the last row, nowhere.
    expect(gridMove('ArrowDown', 5, 3, 7)).toBe(6)
    expect(gridMove('ArrowDown', 6, 3, 7)).toBeNull()
    expect(gridMove('Home', 4, 3, 7)).toBe(3)
    expect(gridMove('End', 3, 3, 7)).toBe(5)
    expect(gridMove('End', 6, 3, 7)).toBe(6)
    expect(gridMove('End', 0, 3, 7, true)).toBe(6)
    expect(gridMove('Home', 6, 3, 7, true)).toBe(0)
    // No cursor yet: the first tile; a key it does not take, or nothing to move in, nothing.
    expect(gridMove('ArrowDown', -1, 3, 7)).toBe(0)
    expect(gridMove('Enter', 2, 3, 7)).toBeNull()
    expect(gridMove('ArrowDown', 0, 3, 0)).toBeNull()
  })

  it('draws a thumbnail of a PDF, a scan or an image once the scan has passed', () => {
    const scanned = { stages: { scan: { status: 'done', detail: '' } }, status: 'ready' } as unknown as Document
    expect(hasThumbnail({ ...scanned, mime_type: 'application/pdf' })).toBe(true)
    expect(hasThumbnail({ ...scanned, mime_type: 'image/heic' })).toBe(true)
    expect(hasThumbnail({ ...scanned, mime_type: 'text/plain' })).toBe(false)
    expect(hasThumbnail({ ...scanned, mime_type: 'application/pdf', stages: {} })).toBe(false)
    expect(hasThumbnail({ ...scanned, mime_type: 'application/pdf', status: 'quarantined' })).toBe(false)
  })
})
