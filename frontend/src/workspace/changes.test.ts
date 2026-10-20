import { describe, expect, it } from 'vitest'
import type { Document, Folder } from '../api.ts'
import { describeChange, moved, renamed } from './changes.ts'
import type { Change } from './changes.ts'

const folder = (id: number, name: string, parentId: number | null = null): Folder => ({
  id,
  project_id: 3,
  parent_id: parentId,
  name,
  created_at: '2026-10-21T09:00:00Z',
  document_count: 0,
})

const doc = (id: number, filename: string, folderId: number | null = null): Document => ({
  id,
  project_id: 3,
  folder_id: folderId,
  filename,
  mime_type: 'text/plain',
  size_bytes: 10,
  status: 'ready',
  stages: {},
  error: null,
  page_count: null,
  title: null,
  doc_type: null,
  doc_date: null,
  summary: null,
  usage: {},
  created_at: '2026-10-21T09:00:00Z',
})

const folders = [folder(1, 'Invoices'), folder(2, '2024', 1), folder(3, 'Contracts')]
const documents = [doc(10, 'msa.pdf'), doc(11, 'invoice-7.pdf', 2), doc(12, 'invoice-8.pdf', 2)]

describe('a move, as it can be undone', () => {
  it('moves each item back into the folder it came from', () => {
    const change = moved({ folderIds: [2], documentIds: [10, 11], targetId: 3 }, folders, documents, 'Acme')
    expect(change).toEqual({
      kind: 'move',
      back: [
        { folderIds: [2], documentIds: [], targetId: 1 },
        { folderIds: [], documentIds: [10], targetId: null },
        { folderIds: [], documentIds: [11], targetId: 2 },
      ],
      names: ['2024', 'msa.pdf', 'invoice-7.pdf'],
      to: 'Contracts',
    })
  })

  it('puts the items that came from one folder back in one move', () => {
    const change = moved({ folderIds: [], documentIds: [11, 12], targetId: null }, folders, documents, 'Acme')
    expect(change).toMatchObject({ back: [{ folderIds: [], documentIds: [11, 12], targetId: 2 }], to: 'Acme' })
  })

  it('offers nothing when an item or the target is not held', () => {
    expect(moved({ folderIds: [], documentIds: [10, 99], targetId: 3 }, folders, documents, 'Acme')).toBeNull()
    expect(moved({ folderIds: [99], documentIds: [], targetId: 3 }, folders, documents, 'Acme')).toBeNull()
    expect(moved({ folderIds: [], documentIds: [10], targetId: 99 }, folders, documents, 'Acme')).toBeNull()
  })
})

describe('a rename, as it can be undone', () => {
  it('goes back to the name it had', () => {
    expect(renamed('document', 10, 'msa.pdf', 'contract.pdf')).toEqual({
      kind: 'rename',
      of: 'document',
      id: 10,
      from: 'msa.pdf',
      to: 'contract.pdf',
    })
  })

  it('offers nothing when the server kept the name, or the old one is not known', () => {
    expect(renamed('folder', 1, 'Invoices', 'Invoices')).toBeNull()
    expect(renamed('folder', 1, undefined, 'Bills')).toBeNull()
  })
})

describe('what the library says of its last change', () => {
  const trash = (names: string[]): Change => ({
    kind: 'trash',
    items: { folderIds: [], documentIds: [10], inFolders: [], names },
  })

  it('says where a deletion went', () => {
    expect(describeChange(trash(['msa.pdf']))).toBe('“msa.pdf” is in the trash.')
    expect(describeChange(trash(['msa.pdf', 'Invoices']))).toBe('2 items are in the trash.')
  })

  it('says where a move put the items', () => {
    const change = moved({ folderIds: [], documentIds: [10], targetId: 3 }, folders, documents, 'Acme')!
    expect(describeChange(change)).toBe('“msa.pdf” is now in “Contracts”.')
    const several = moved({ folderIds: [2], documentIds: [10], targetId: null }, folders, documents, 'Acme')!
    expect(describeChange(several)).toBe('2 items are now in “Acme”.')
  })

  it('says what a rename called the item', () => {
    expect(describeChange(renamed('document', 10, 'msa.pdf', 'contract.pdf')!)).toBe(
      '“msa.pdf” is now called “contract.pdf”.',
    )
  })
})
