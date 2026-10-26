import { describe, expect, it } from 'vitest'
import type { ProjectExport } from './api.ts'
import { describeExport, making } from './exports.ts'

const ready: ProjectExport = {
  id: 3,
  status: 'ready',
  documents: 12,
  size_bytes: 4_200_000,
  error: null,
  created_at: '2026-10-21T10:00:00Z',
  expires_at: '2026-10-28T10:01:00Z',
  chosen: null,
}
const day = (value: string) => value.slice(0, 10)

describe('describeExport', () => {
  it('says what a ready export holds and until when', () => {
    expect(describeExport(ready, day)).toBe('12 documents, 4.2 MB, until 2026-10-28')
    expect(describeExport({ ...ready, documents: 1, size_bytes: 900 }, day)).toBe('1 document, 900 bytes, until 2026-10-28')
  })

  it('says an export is of the documents chosen alone', () => {
    const chosen = { ...ready, chosen: 3, documents: 2 }
    expect(describeExport(chosen, day)).toBe('Chosen documents: 2 documents, 4.2 MB, until 2026-10-28')
    expect(describeExport({ ...chosen, status: 'queued', expires_at: null }, day)).toBe('Chosen documents, being made…')
    const failed = { ...chosen, status: 'failed', error: 'The export could not be made.', expires_at: null } as const
    expect(describeExport(failed, day)).toBe('Chosen documents: The export could not be made.')
  })

  it('says one is being made, or why it failed', () => {
    const queued = { ...ready, status: 'queued', documents: 0, size_bytes: 0, expires_at: null } as const
    expect(describeExport(queued, day)).toBe('Being made…')
    expect(describeExport({ ...queued, status: 'failed', error: 'The export could not be made.' }, day)).toBe('The export could not be made.')
  })
})

describe('making', () => {
  it('is true while any is being made', () => {
    expect(making([ready])).toBe(false)
    expect(making([ready, { ...ready, id: 4, status: 'queued', expires_at: null }])).toBe(true)
    expect(making([])).toBe(false)
  })
})
