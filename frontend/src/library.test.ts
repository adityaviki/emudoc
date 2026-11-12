import { describe, expect, it } from 'vitest'
import type { Document } from './api.ts'
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
  noNameWords,
  placeName,
  placeOf,
  narrows,
  sortWords,
  withoutPart,
} from './library.ts'

describe('a place', () => {
  it('is named by the view, and called by its own name or the library’s', () => {
    expect(placeOf({})).toBeNull()
    expect(placeOf({ favorites: true })).toEqual({ favorites: true })
    expect(placeOf({ recent: true })).toEqual({ recent: true })
    expect(placeName({ favorites: true })).toBe('Favourites')
    expect(placeName({ recent: true })).toBe('Recent')
  })

  it("is narrowed to the names that hold a search's words, which alone take the files of every folder", () => {
    const search = (q: string) => ({ q, filter: EMPTY_FILTER })
    expect(placeOf({ search: search('inv') })).toEqual({ name: 'inv' })
    expect(placeOf({ favorites: true, search: search('inv') })).toEqual({ favorites: true, name: 'inv' })
    expect(placeOf({ recent: true, search: search('inv') })).toEqual({ recent: true, name: 'inv' })
    // A search without words, narrowed already, names no file.
    expect(placeOf({ search: { q: '', filter: { ...EMPTY_FILTER, mentions: ['March'] } } })).toBeNull()
    expect(placeOf({ favorites: true, search: search('') })).toEqual({ favorites: true })
    expect(allNamed({ name: 'inv' })).toBe(true)
    expect(allNamed({ recent: true, name: 'inv' })).toBe(false)
    expect(allNamed({ folder: null })).toBe(false)
    expect(placeName({ name: 'inv' })).toBe('All files')
    expect(placeName({ recent: true, name: 'inv' })).toBe('Recent')
    expect(noNameWords({ name: 'inv' })).toBe('No file’s name holds “inv”.')
    expect(noNameWords({ favorites: true, name: 'inv' })).toBe('No favourite’s name holds “inv”.')
    expect(noNameWords({ recent: true, name: 'inv' })).toBe('No recent file’s name holds “inv”.')
    expect(emptyPlaceWords({ favorites: true }).title).toBe('No favourites yet')
    expect(emptyPlaceWords({ recent: true }).title).toBe('Nothing opened yet')
  })

  it('is listed in an order its button words', () => {
    expect(sortWords('name', 'asc')).toBe('Name, a to z')
    expect(sortWords('date', 'desc')).toBe('Document date, newest first')
    expect(sortWords('size', 'asc')).toBe('Size, smallest first')
  })
})

describe('a filter', () => {
  it('says what it matches, its folder by its path', () => {
    expect(filterWords(EMPTY_FILTER, null)).toBe('Every document of the project')
    expect(filterWords({ ...EMPTY_FILTER, types: ['invoice', 'credit note'], reviews: ['to_review', 'disputed'], folder_id: 3 }, 'Invoices/2024')).toBe(
      'Documents of type invoice or credit note, to review or disputed in review, in Invoices/2024',
    )
    expect(filterWords({ ...EMPTY_FILTER, folder_id: 3 }, null)).toBe('Documents in a folder that is gone')
    expect(filterWords({ ...EMPTY_FILTER, after: '2024-01-15' }, null)).toMatch(/^Documents dated .+ or later$/)
  })

  it('takes its dates the right way round', () => {
    expect(datesProblem(EMPTY_FILTER)).toBeNull()
    expect(datesProblem({ after: '2024-04-01', before: '2024-01-01' })).toMatch(/^Its earliest date is after/)
    expect(datesProblem({ after: '2024-01-01', before: '2024-03-31' })).toBeNull()
  })

  it('says the words its documents mention', () => {
    const mentioning = { ...EMPTY_FILTER, types: ['invoice'], mentions: ['late delivery', 'penalty'] }
    expect(filterWords(mentioning, null)).toBe('Documents of type invoice, mentioning “late delivery”, mentioning “penalty”')
  })
})

describe('a search', () => {
  const filter = {
    ...EMPTY_FILTER,
    types: ['invoice'],
    after: '2024-01-01',
    reviews: ['disputed' as const],
    folder_id: 7,
    mentions: ['late delivery', 'penalty'],
  }

  it('is narrowed by parts a member takes off one by one, each search it is within alone', () => {
    expect(filterParts(filter, 'Invoices').map(({ part, words }) => [part, words])).toEqual([
      ['types', 'of type invoice'],
      ['dates', `dated ${formatDate('2024-01-01')} or later`],
      ['reviews', 'disputed in review'],
      ['folder', 'in Invoices'],
      [0, 'mentioning “late delivery”'],
      [1, 'mentioning “penalty”'],
    ])
    expect(withoutPart(filter, 'types')).toMatchObject({ types: [], after: '2024-01-01' })
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

  it("draws a Word file's thumbnail from its view once the view is made", () => {
    const word = {
      stages: { scan: { status: 'done', detail: '' } },
      status: 'ready',
      mime_type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    } as unknown as Document
    expect(hasThumbnail({ ...word, view_state: 'ready' })).toBe(true)
    expect(hasThumbnail({ ...word, view_state: 'queued' })).toBe(false)
    expect(hasThumbnail({ ...word, view_state: null })).toBe(false)
  })
})
