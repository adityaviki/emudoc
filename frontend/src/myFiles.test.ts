import { describe, expect, it } from 'vitest'
import type { Document, MyFile, MyFileVersion } from './api.ts'
import { formatDate, formatDateTime } from './format.ts'
import {
  copiedWords,
  filesSide,
  following,
  heldWords,
  myFileDeletionQuestion,
  myFileLine,
  myFileWords,
  myFilesCount,
  myVersionName,
  myVersionWords,
  retryable,
  servable,
  tabLeads,
  unshownWords,
  waiting,
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

  it('says beside its name in its row who wrote it last, its versions past the first and the day it changed', () => {
    const day = formatDate('2026-11-12T09:30:00Z')
    expect(myFileLine(file())).toBe(day)
    expect(myFileLine(file({ versions: 3, latest: version({ by_model: true }) }))).toBe(`By the agent · 3 versions · ${day}`)
  })

  it('asks before it is deleted for good, naming its versions', () => {
    expect(myFileDeletionQuestion(file())).toBe('Delete “Dispute.docx” for good? Nobody can bring it back.')
    expect(myFileDeletionQuestion(file({ versions: 4 }))).toBe('Delete “Dispute.docx” and its 4 versions for good? Nobody can bring it back.')
  })

  it('says where its copy went in the project’s files', () => {
    const document = { filename: 'Dispute.docx', version: 2 } as Document
    expect(copiedWords(document, false)).toBe('“Dispute.docx” is in project files now, being read. Your file stays here.')
    expect(copiedWords(document, true)).toBe('“Dispute.docx” in project files is now version 2. Its new file is being read.')
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

describe('the list', () => {
  it('is followed while a version waits for its scan or its pages', () => {
    expect(following({ files: [file({ latest: version({ scan: 'pending' }) })] })).toBe(true)
    expect(following({ files: [file()] })).toBe(false)
    expect(following({ files: [] })).toBe(false)
  })

  it('counts the files, and the most a member keeps once they come near it', () => {
    expect([myFilesCount(1, 200), myFilesCount(2, 200), myFilesCount(179, 200)]).toEqual(['1 file', '2 files', '179 files'])
    expect([myFilesCount(180, 200), myFilesCount(200, 200)]).toEqual(['180 of 200 files', '200 of 200 files'])
  })
})

describe('the library’s two sides', () => {
  it('shows My files for the list and each file, and the project’s files for everything else', () => {
    expect([filesSide({ folderId: null, myFiles: true }), filesSide({ folderId: null, myFile: 4 })]).toEqual(['mine', 'mine'])
    expect([filesSide({ folderId: 3 }), filesSide({ folderId: null, trash: true }), filesSide({ folderId: null, recent: true })]).toEqual([
      'project',
      'project',
      'project',
    ])
  })

  it('leads from a tab to the other side, or back to its own list from a page within it, and stays within the folders', () => {
    expect([tabLeads({ folderId: 3 }, 'mine'), tabLeads({ folderId: null, myFiles: true }, 'project')]).toEqual([true, true])
    expect([tabLeads({ folderId: null, myFile: 4 }, 'mine'), tabLeads({ folderId: null, myFiles: true }, 'mine')]).toEqual([true, false])
    const within = [{ trash: true as const }, { mail: true as const }, { connectors: true as const }].map((page) => tabLeads({ folderId: null, ...page }, 'project'))
    expect(within).toEqual([true, true, true])
    expect([tabLeads({ folderId: 3 }, 'project'), tabLeads({ folderId: null, favorites: true }, 'project')]).toEqual([false, false])
  })
})
