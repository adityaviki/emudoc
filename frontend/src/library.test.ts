import { describe, expect, it } from 'vitest'
import type { Document, SmartFolder, Tag } from './api.ts'
import {
  EMPTY_FILTER,
  NEW_RULE,
  filterWords,
  gridMove,
  hasThumbnail,
  placeName,
  placeOf,
  ruleInput,
  ruleProblem,
  ruleSentence,
  smartFolderProblem,
  sortWords,
  tagsOf,
  tagsWords,
} from './library.ts'

const tag = (id: number, name: string): Tag => ({ id, name, color: 'grey', document_count: 0 })
const TAGS = [tag(1, 'Urgent'), tag(2, 'Paid')]

describe('a place', () => {
  it('is named by the view, and called by its own name or the library’s', () => {
    expect(placeOf({})).toBeNull()
    expect(placeOf({ tag: 2 })).toEqual({ tag: 2 })
    expect(placeOf({ smart: 4 })).toEqual({ smart: 4 })
    expect(placeOf({ favorites: true })).toEqual({ favorites: true })
    expect(placeOf({ recent: true })).toEqual({ recent: true })
    const smart = { id: 4, name: 'Unpaid invoices' } as SmartFolder
    expect(placeName({ tag: 2 }, TAGS, [])).toBe('Paid')
    expect(placeName({ tag: 9 }, TAGS, [])).toBe('A tag')
    expect(placeName({ smart: 4 }, TAGS, [smart])).toBe('Unpaid invoices')
    expect(placeName({ favorites: true }, TAGS, [])).toBe('Favourites')
    expect(placeName({ recent: true }, TAGS, [])).toBe('Recent')
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

describe('a smart folder', () => {
  it('says what it lists, its tags and folder by their names', () => {
    expect(filterWords(EMPTY_FILTER, TAGS, null)).toBe('Every document of the project')
    expect(filterWords({ ...EMPTY_FILTER, tags: [1, 2], every_tag: true }, TAGS, null)).toBe('Documents tagged “Urgent” and “Paid”')
    expect(
      filterWords({ ...EMPTY_FILTER, tags: [1, 9], types: ['invoice', 'credit note'], reviews: ['to_review', 'disputed'], folder_id: 3 }, TAGS, 'Invoices/2024'),
    ).toBe('Documents tagged “Urgent” or “a deleted tag”, of type invoice or credit note, to review or disputed in review, in Invoices/2024')
    expect(filterWords({ ...EMPTY_FILTER, folder_id: 3 }, TAGS, null)).toBe('Documents in a folder that is gone')
    expect(filterWords({ ...EMPTY_FILTER, after: '2024-01-15' }, TAGS, null)).toMatch(/^Documents dated .+ or later$/)
  })

  it('is saved with a name, and dates the right way round', () => {
    expect(smartFolderProblem('  ', EMPTY_FILTER)).toBe('Give the smart folder a name.')
    expect(smartFolderProblem('Q1', { ...EMPTY_FILTER, after: '2024-04-01', before: '2024-01-01' })).toMatch(/^Its earliest date is after/)
    expect(smartFolderProblem('Q1', { ...EMPTY_FILTER, after: '2024-01-01', before: '2024-03-31' })).toBeNull()
  })
})

describe('a filing rule', () => {
  it('reads as a sentence', () => {
    expect(ruleSentence({ ...NEW_RULE, value: 'invoice', folder_id: 3 }, 'Invoices', null)).toBe('A new invoice document goes into Invoices')
    expect(ruleSentence({ ...NEW_RULE, condition: 'fact', field: 'supplier_name', value: 'Acme', action: 'by_month' }, null, null)).toBe(
      'A new document whose supplier name holds “Acme” is filed under the top of the library, a folder for its year and one for its month',
    )
    expect(ruleSentence({ ...NEW_RULE, condition: 'any', value: null, action: 'tag', tag_id: 1 }, null, 'Urgent')).toBe('Every new document is tagged “Urgent”')
    expect(ruleSentence({ ...NEW_RULE, condition: 'any', action: 'by_year' }, 'Archive', null)).toBe(
      'Every new document is filed under Archive, a folder for its year',
    )
  })

  it('is refused for what it lacks, as the server would, and sent with only what it uses', () => {
    expect(ruleProblem(NEW_RULE)).toMatch(/^Say which type of document/)
    expect(ruleProblem({ ...NEW_RULE, condition: 'fact', field: 'supplier', value: ' ' })).toMatch(/^Say which fact/)
    expect(ruleProblem({ ...NEW_RULE, value: 'invoice' })).toBe('Choose the folder the rule moves documents into.')
    expect(ruleProblem({ ...NEW_RULE, value: 'invoice', action: 'tag' })).toBe('Choose the tag the rule puts on documents.')
    expect(ruleProblem({ ...NEW_RULE, condition: 'any', action: 'by_year' })).toBeNull()
    expect(ruleInput({ ...NEW_RULE, condition: 'any', field: 'left', value: 'over', action: 'tag', folder_id: 3, tag_id: 1 })).toEqual({
      ...NEW_RULE,
      condition: 'any',
      field: null,
      value: null,
      action: 'tag',
      folder_id: null,
      tag_id: 1,
    })
    expect(ruleInput({ ...NEW_RULE, value: ' invoice ', field: 'x', folder_id: 3, tag_id: 1 })).toMatchObject({ field: null, value: 'invoice', folder_id: 3, tag_id: null })
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
