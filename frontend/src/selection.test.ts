import { describe, expect, it } from 'vitest'
import type { Document } from './api.ts'
import { exportWords, reprocessedWords } from './selection.ts'

const doc = (id: number) => ({ id, filename: `${id}.pdf` }) as Document

describe('what the library says', () => {
  it('of the chosen indexed again, and those being indexed already', () => {
    const queued = [doc(1), doc(2)]
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
