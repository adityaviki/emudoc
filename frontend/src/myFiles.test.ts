import { describe, expect, it } from 'vitest'
import type { Document, MyFile, MyFileVersion, MyFileWriting } from './api.ts'
import { formatDateTime } from './format.ts'
import {
  copiedWords,
  disputeLetter,
  following,
  heldWords,
  myFileDeletionQuestion,
  myFileWords,
  myVersionName,
  myVersionWords,
  retryable,
  servable,
  underWay,
  unshownWords,
  waiting,
  writingWords,
} from './myFiles.ts'

const version = (fields: Partial<MyFileVersion> = {}): MyFileVersion => ({
  number: 1,
  size_bytes: 2048,
  by_model: false,
  note: 'Uploaded',
  scan: 'passed',
  view_state: 'ready',
  view_page_sizes: [[595, 842]],
  view_error: null,
  created_at: '2026-11-12T09:30:00Z',
  ...fields,
})

const file = (fields: Partial<MyFile> = {}): MyFile => ({
  id: 1,
  project_id: 1,
  name: 'Dispute.docx',
  kind: 'docx',
  origin_document_id: null,
  origin_name: null,
  latest: version(),
  versions: 1,
  created_at: '2026-11-12T09:30:00Z',
  updated_at: '2026-11-12T09:30:00Z',
  ...fields,
})

describe('a file of My files', () => {
  it('says what it is in the list', () => {
    const at = formatDateTime('2026-11-12T09:30:00Z')
    expect(myFileWords(file())).toBe(`Word document · 2 kB · 1 version · changed ${at}`)
    expect(myFileWords(file({ kind: 'xlsx', versions: 3 }))).toMatch(/^Excel workbook · 2 kB · 3 versions · /)
  })

  it('asks before it is deleted for good, naming its versions', () => {
    expect(myFileDeletionQuestion(file())).toBe('Delete “Dispute.docx” for good? Nobody can bring it back.')
    expect(myFileDeletionQuestion(file({ versions: 4 }))).toBe('Delete “Dispute.docx” and its 4 versions for good? Nobody can bring it back.')
  })

  it('says where its copy went in the library', () => {
    const document = { filename: 'Dispute.docx', version: 2 } as Document
    expect(copiedWords(document, false)).toBe('“Dispute.docx” is in the library now, being read. Your file stays here.')
    expect(copiedWords(document, true)).toBe('“Dispute.docx” in the library is now version 2. Its new file is being read.')
  })
})

describe('a version', () => {
  it('is named by its number, the newest said so, and says who made it', () => {
    expect(myVersionName(version({ number: 2 }), 2)).toBe('Version 2, the newest')
    expect(myVersionName(version(), 2)).toBe('Version 1')
    const at = formatDateTime('2026-11-12T09:30:00Z')
    expect(myVersionWords(version())).toBe(`Uploaded · 2 kB · ${at}`)
    expect(myVersionWords(version({ by_model: true, note: 'Raised the total to 1,255 EUR' }))).toBe(
      `Written by the agent · Raised the total to 1,255 EUR · 2 kB · ${at}`,
    )
  })

  it('is served once its scan passed it, or there was none to run', () => {
    expect([servable(version()), servable(version({ scan: 'skipped' }))]).toEqual([true, true])
    expect([servable(version({ scan: 'pending' })), servable(version({ scan: 'flagged' }))]).toEqual([false, false])
    expect(heldWords(version())).toBeNull()
    expect(heldWords(version({ scan: 'pending' }))).toMatch(/waits for its malware scan/)
    expect(heldWords(version({ scan: 'flagged' }))).toMatch(/flagged it/)
  })

  it('says why its pages are not shown, and is followed while they are being made', () => {
    expect(unshownWords(version(), true)).toBeNull()
    expect(unshownWords(version({ scan: 'pending', view_state: 'queued', view_page_sizes: [] }), true)).toMatch(/^Waiting for the malware scan/)
    expect(unshownWords(version({ view_state: 'queued', view_page_sizes: [] }), true)).toBe('Making its pages…')
    expect(unshownWords(version({ view_state: 'failed', view_error: 'Not a workbook.' }), true)).toBe('Not a workbook.')
    expect(unshownWords(version({ view_state: null, view_page_sizes: [] }), false)).toMatch(/^This deployment does not show/)
    const flagged = version({ scan: 'flagged', view_state: 'failed', view_error: 'The malware scan flagged this file as Eicar.' })
    expect(unshownWords(flagged, true)).toBe('The malware scan flagged this file as Eicar.')
    expect([waiting(version({ scan: 'pending' })), waiting(version({ view_state: 'queued' })), waiting(version())]).toEqual([true, true, false])
    expect([retryable(version({ view_state: 'failed' })), retryable(flagged), retryable(version())]).toEqual([true, false, false])
  })
})

describe('a document the agent is asked to write into My files', () => {
  const writing = (fields: Partial<MyFileWriting> = {}): MyFileWriting => ({
    id: 3,
    kind: 'dispute_letter',
    title: 'Dispute letter about INV-1',
    status: 'queued',
    error: null,
    created_at: '2026-11-13T09:30:00Z',
    ...fields,
  })

  it('is asked of the agent by a disputed document, about it alone', () => {
    expect(disputeLetter({ id: 41 })).toEqual({ kind: 'dispute_letter', document_ids: [41] })
  })

  it('says how far its writing is, or why it could not be written', () => {
    expect(writingWords(writing())).toBe('Waiting to be written…')
    expect(writingWords(writing({ status: 'writing' }))).toBe('Being written…')
    expect(writingWords(writing({ status: 'failed', error: 'The model wrote nothing. Try again.' }))).toBe('The model wrote nothing. Try again.')
    expect(writingWords(writing({ status: 'failed' }))).toBe('It could not be written. Try again.')
  })

  it('has the list followed while it is written, and while a version waits, never for a failed one alone', () => {
    expect([underWay(writing()), underWay(writing({ status: 'writing' })), underWay(writing({ status: 'failed' }))]).toEqual([true, true, false])
    expect(following({ files: [file()], writings: [writing({ status: 'writing' })] })).toBe(true)
    expect(following({ files: [file()], writings: [writing({ status: 'failed' })] })).toBe(false)
    expect(following({ files: [file({ latest: version({ scan: 'pending' }) })], writings: [] })).toBe(true)
    expect(following({ files: [], writings: [] })).toBe(false)
  })
})
