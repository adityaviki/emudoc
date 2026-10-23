import { describe, expect, it } from 'vitest'
import { failureWords, libraryPlace } from './failures.ts'

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
    expect(libraryPlace({ folderId: 4, q: 'invoice' })).toBe(libraryPlace({ folderId: 4, q: 'invoice' }))
    expect(libraryPlace({ folderId: null, q: '' })).toBe(libraryPlace({ folderId: null, q: '' }))
  })

  it('changes with the folder, the search, the trash and the templates, each of which starts it afresh', () => {
    const places = [
      libraryPlace({ folderId: null, q: '' }),
      libraryPlace({ folderId: 4, q: '' }),
      libraryPlace({ folderId: 5, q: '' }),
      libraryPlace({ folderId: 4, q: 'invoice' }),
      libraryPlace({ folderId: null, q: 'invoice' }),
      libraryPlace({ folderId: null, q: '', trash: true }),
      libraryPlace({ folderId: null, q: '', templates: true }),
    ]
    expect(new Set(places).size).toBe(places.length)
  })

  it('tells a search for a folder\'s number from that folder', () => {
    expect(libraryPlace({ folderId: null, q: '4' })).not.toBe(libraryPlace({ folderId: 4, q: '' }))
  })
})
