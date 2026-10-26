import { describe, expect, it } from 'vitest'
import type { Document, Tag } from './api.ts'
import { MAX_DOCUMENT_TAGS, exportWords, nextState, overfull, overfullWords, reprocessedWords, retagged, tagStates, tagsChange } from './selection.ts'

const doc = (id: number, tag_ids: number[], filename = `${id}.pdf`) => ({ id, filename, tag_ids }) as Document
const tag = (id: number) => ({ id, name: `tag ${id}`, color: 'grey', document_count: 0 }) as Tag

describe('the tags of the chosen documents', () => {
  it('stand on every one, some or none', () => {
    const states = tagStates([doc(1, [1, 2]), doc(2, [1])], [tag(1), tag(2), tag(3)])
    expect([...states]).toEqual([
      [1, 'all'],
      [2, 'some'],
      [3, 'none'],
    ])
    expect(tagStates([doc(1, [2])], [tag(2)]).get(2)).toBe('all')
  })

  it('go on and off every one, and one only some carried comes back to that', () => {
    expect(nextState('none', 'none')).toBe('all')
    expect(nextState('all', 'none')).toBe('none')
    expect(nextState('all', 'all')).toBe('none')
    expect(nextState('some', 'some')).toBe('all')
    expect(nextState('all', 'some')).toBe('none')
    expect(nextState('none', 'some')).toBe('some')
  })

  it('send what changed alone, a tag made meanwhile among it', () => {
    const was = new Map([
      [1, 'all'],
      [2, 'some'],
      [3, 'some'],
      [4, 'none'],
    ] as const)
    const now = new Map([
      [1, 'none'],
      [2, 'all'],
      [3, 'some'],
      [4, 'none'],
      [9, 'all'],
    ] as const)
    expect(tagsChange(was, now)).toEqual({ add: [2, 9], remove: [1] })
    expect(tagsChange(was, was)).toEqual({ add: [], remove: [] })
  })

  it('are kept in the order they were put on, as the server keeps them', () => {
    expect(retagged([3, 1, 2], { add: [4, 2], remove: [1] })).toEqual([3, 2, 4])
  })

  it('may not pass what a document carries', () => {
    const full = Array.from({ length: MAX_DOCUMENT_TAGS }, (_, index) => index + 1)
    const chosen = [doc(1, full, 'full.pdf'), doc(2, []), doc(3, full, 'also.pdf')]
    expect(overfull(chosen, { add: [99], remove: [] })).toEqual(['full.pdf', 'also.pdf'])
    expect(overfull(chosen, { add: [99], remove: [1] })).toEqual([])
    expect(overfullWords(['full.pdf'])).toBe(
      '“full.pdf” would carry more than 20 tags, which is as many as a document carries. Take some off, or leave it out of the choice.',
    )
    expect(overfullWords(['full.pdf', 'also.pdf'])).toMatch(/^“full\.pdf” and 1 other would carry .* leave them out of the choice\.$/)
  })
})

describe('what the library says', () => {
  it('of the chosen indexed again, and those being indexed already', () => {
    const queued = [doc(1, []), doc(2, [])]
    expect(reprocessedWords({ documents: queued, skipped: 0 }, false)).toBe('Indexing 2 documents again.')
    expect(reprocessedWords({ documents: queued.slice(1), skipped: 1 }, false)).toBe(
      'Indexing 1 document again. 1 document being indexed already was left as it is.',
    )
    expect(reprocessedWords({ documents: queued, skipped: 2 }, true)).toBe(
      'Indexing the rest of 2 documents. 2 documents being indexed already were left as they are.',
    )
    expect(reprocessedWords({ documents: [], skipped: 3 }, false)).toBe('They are being indexed already, so nothing more was queued.')
    expect(reprocessedWords({ documents: [], skipped: 0 }, true)).toBe('None of them is partly indexed any more.')
  })

  it('of an export of them', () => {
    expect(exportWords(3)).toBe(
      'An export of 3 documents is being made. You are told when it is ready, and the project’s exports on the projects page list it for 7 days.',
    )
  })
})
