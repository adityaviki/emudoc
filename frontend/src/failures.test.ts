import { describe, expect, it } from 'vitest'
import { failureWords, libraryPlace } from './failures.ts'
import { EMPTY_FILTER } from './library.ts'

const searched = (q: string) => ({ q, filter: EMPTY_FILTER })

describe('failureWords', () => {
  it('has the page reloaded, as nothing below the header is left to try again', () => {
    expect(failureWords('page')).toEqual({ title: 'This page ran into an error.', advice: 'Reload it to go on.', retry: false })
  })

  it('names the pane that broke and offers it again, the rest of the page going on', () => {
    expect(failureWords('library').title).toBe('The library ran into an error.')
    expect(failureWords('document').title).toBe('This document ran into an error.')
    expect(failureWords('chat').title).toBe('This chat ran into an error.')
    for (const part of ['library', 'document', 'chat'] as const) {
      expect(failureWords(part).retry).toBe(true)
      expect(failureWords(part).advice).toBe('The rest of the page still works: try again, or reload the page.')
    }
  })
})

describe('libraryPlace', () => {
  it('is the same for the same place, so a broken library stays broken while nothing moves', () => {
    expect(libraryPlace({ folderId: 4, search: searched('invoice') })).toBe(libraryPlace({ folderId: 4, search: searched('invoice') }))
    expect(libraryPlace({ folderId: null })).toBe(libraryPlace({ folderId: null }))
  })

  it('changes with the folder, a place, the search and what narrows it, and the trash, each of which starts it afresh', () => {
    const places = [
      libraryPlace({ folderId: null }),
      libraryPlace({ folderId: 4 }),
      libraryPlace({ folderId: 5 }),
      libraryPlace({ folderId: 4, search: searched('invoice') }),
      libraryPlace({ folderId: null, search: searched('invoice') }),
      libraryPlace({ folderId: null, search: { q: 'invoice', filter: { ...EMPTY_FILTER, types: ['invoice'] } } }),
      libraryPlace({ folderId: null, favorites: true, search: searched('invoice') }),
      libraryPlace({ folderId: null, favorites: true }),
      libraryPlace({ folderId: null, recent: true }),
      libraryPlace({ folderId: null, trash: true }),
    ]
    expect(new Set(places).size).toBe(places.length)
  })

  it("tells a search for a folder's number from that folder", () => {
    expect(libraryPlace({ folderId: null, search: searched('4') })).not.toBe(libraryPlace({ folderId: 4 }))
  })
})
