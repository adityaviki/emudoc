import { describe, expect, it } from 'vitest'
import type { Document, MyFile, MyFileVersion, MyFolder } from './api.ts'
import { formatDate, formatDateTime } from './format.ts'
import {
  addingWords,
  copiedWords,
  copyQuestion,
  following,
  heldBy,
  heldWords,
  leftOutWords,
  myFileDeletionQuestion,
  myFileUploads,
  myFileWhere,
  myFilesNamed,
  myFilesTree,
  noMyFileNameWords,
  myFolderDeletionQuestion,
  myFileLine,
  myFileWords,
  myFilesCount,
  myVersionName,
  myVersionWords,
  retryable,
  servable,
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
  folder_id: null,
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

  it('asks before a copy into the project files, by the file’s name', () => {
    expect(copyQuestion(file())).toEqual({
      title: 'Copy “Dispute.docx” to project files?',
      body: 'Its newest version becomes a document every member of the project sees, read and indexed as any upload. Your file stays here.',
      action: 'Copy',
    })
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

describe('a search of the library in My files', () => {
  const folders: MyFolder[] = [
    { id: 1, name: 'Letters', parent_id: null },
    { id: 2, name: '2026', parent_id: 1 },
    { id: 3, name: 'Loop', parent_id: 4 },
    { id: 4, name: 'Back', parent_id: 3 },
  ]
  const files = [file({ id: 1, name: 'Dispute letter.docx' }), file({ id: 2, name: 'Invoice lines.xlsx' }), file({ id: 3, name: 'LETTER to Acme.docx' })]

  it('finds the files whose names hold the words in any case, in the list order, and none without words', () => {
    expect(myFilesNamed(files, ' letter ').map((one) => one.id)).toEqual([1, 3])
    expect(myFilesNamed(files, 'xlsx').map((one) => one.id)).toEqual([2])
    expect(myFilesNamed(files, 'nothing like it')).toEqual([])
    expect(myFilesNamed(files, '  ')).toEqual([])
  })

  it('says where a file is: its folder by its path, else the top of My files', () => {
    expect(myFileWhere({ folder_id: 2 }, folders)).toBe('Letters / 2026')
    expect(myFileWhere({ folder_id: 1 }, folders)).toBe('Letters')
    expect(myFileWhere({ folder_id: null }, folders)).toBe('My files')
    expect(myFileWhere({ folder_id: 99 }, folders)).toBe('My files')
    expect(myFileWhere({ folder_id: 3 }, folders)).toBe('Back / Loop')
  })

  it('says when no name holds the words', () => {
    expect(noMyFileNameWords(' acme ')).toBe('No file’s name in My files holds “acme”.')
  })
})

describe('the folders of My files', () => {
  const folder = (id: number, name: string, parent_id: number | null = null): MyFolder => ({ id, name, parent_id })
  const folders = [folder(1, 'Letters'), folder(2, 'archive'), folder(3, 'Q10', 1), folder(4, 'Q2', 1)]
  const files = [file({ id: 10, name: 'Newest.docx', folder_id: 3 }), file({ id: 11, name: 'Top.docx' }), file({ id: 12, name: 'Older.docx', folder_id: 3 })]

  it('builds the tree: folders by name as the library sorts them, files in the list order, each in its folder', () => {
    const tree = myFilesTree({ files, folders })
    expect(tree.folders.map((node) => node.folder.name)).toEqual(['archive', 'Letters'])
    expect(tree.files.map((one) => one.id)).toEqual([11])
    const letters = tree.folders[1]
    expect(letters.folders.map((node) => node.folder.name)).toEqual(['Q2', 'Q10'])
    expect(letters.folders[1].files.map((one) => one.id)).toEqual([10, 12])
  })

  it('keeps at the top what names a folder the list does not hold', () => {
    const tree = myFilesTree({ files: [file({ id: 5, folder_id: 99 })], folders: [folder(7, 'Lost', 98)] })
    expect(tree.files.map((one) => one.id)).toEqual([5])
    expect(tree.folders.map((node) => node.folder.id)).toEqual([7])
  })

  it('counts what a folder holds at every depth, and asks before deleting it with that', () => {
    const letters = myFilesTree({ files, folders }).folders[1]
    expect(heldBy(letters)).toEqual({ files: 2, folders: 2 })
    expect(myFolderDeletionQuestion(letters)).toEqual({
      title: 'Delete this folder?',
      body: 'Delete “Letters” and the 2 files and 2 folders in it for good? Nobody can bring them back.',
      action: 'Delete for good',
    })
    const archive = myFilesTree({ files, folders }).folders[0]
    expect(myFolderDeletionQuestion(archive)).toEqual({ title: 'Delete this folder?', body: 'Delete the empty folder “archive”?', action: 'Delete' })
  })

  it('sends a Word document or a workbook with its folder, names the rest, and leaves what a folder hides unsaid', () => {
    const item = (name: string, folder = '') => ({ file: new File(['x'], name), folder })
    const chosen = myFileUploads([item('Letter.DOCX', 'Mail'), item('Sums.xlsx', 'Mail/2026'), item('scan.pdf', 'Mail'), item('.~lock.docx', 'Mail'), item('Thumbs.db', 'Mail')])
    expect(chosen.items.map(({ file, folder }) => [file.name, folder])).toEqual([
      ['Letter.DOCX', 'Mail'],
      ['Sums.xlsx', 'Mail/2026'],
    ])
    expect(chosen.left).toEqual(['Mail/scan.pdf'])
    expect(leftOutWords(chosen.left)).toBe('My files keeps Word documents and Excel workbooks, so this was left out: Mail/scan.pdf.')
    expect(leftOutWords(['a', 'b', 'c', 'd', 'e', 'f', 'g'])).toBe('My files keeps Word documents and Excel workbooks, so these were left out: a, b, c, d, e and 2 more.')
  })

  it('says what an upload is adding', () => {
    expect(addingWords(1)).toBe('Adding the file…')
    expect(addingWords(12)).toBe('Adding 12 files…')
  })
})
