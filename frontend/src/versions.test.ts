import { describe, expect, it } from 'vitest'
import type { Document, DocumentVersion } from './api.ts'
import { formatDateTime } from './format.ts'
import { givenWords, versionHeld, versionName, versionWords } from './versions.ts'

const version = (fields: Partial<DocumentVersion> = {}): DocumentVersion => ({
  number: 1,
  current: false,
  filename: 'msa.docx',
  mime_type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  size_bytes: 2048,
  page_count: null,
  uploaded_at: '2026-10-21T09:30:00Z',
  uploaded_by: 'Ada',
  served: true,
  has_text: true,
  ...fields,
})

describe('a version', () => {
  it('is named by its number, the current one said so', () => {
    expect(versionName(version())).toBe('Version 1')
    expect(versionName(version({ number: 3, current: true }))).toBe('Version 3, the current one')
  })

  it('says what its file was and who brought it in when', () => {
    const at = formatDateTime('2026-10-21T09:30:00Z')
    expect(versionWords(version())).toBe(`msa.docx · 2 kB · uploaded by Ada on ${at}`)
    expect(versionWords(version({ filename: 'msa.pdf', page_count: 1, uploaded_by: null }))).toBe(`msa.pdf · 2 kB · 1 page · uploaded on ${at}`)
    expect(versionWords(version({ page_count: 12 }))).toContain(' · 12 pages · ')
  })

  it('says why its file or its text is not offered', () => {
    expect(versionHeld(version())).toBeNull()
    expect(versionHeld(version({ served: false, has_text: false }))).toMatch(/^It was replaced before the malware scan/)
    expect(versionHeld(version({ has_text: false }))).toBe('It was replaced before its text was read.')
  })

  it('is announced by the document it is now', () => {
    expect(givenWords({ filename: 'msa.pdf', version: 2 } as Document)).toMatch(/^“msa.pdf” is now version 2\. /)
  })
})
