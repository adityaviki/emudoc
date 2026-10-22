import { describe, expect, it } from 'vitest'
import type { Trashed, TrashedProject } from './api.ts'
import {
  describeTrashed,
  describeTrashedProject,
  emptyingQuestion,
  movedToTrash,
  projectPurgeQuestion,
  purgeQuestion,
  trashedName,
} from './trash.ts'

const project = (changes: Partial<TrashedProject>): TrashedProject => ({
  id: 4,
  name: 'Contracts',
  sample: false,
  document_count: 12,
  deleted_at: '2026-10-21T09:00:00Z',
  deleted_by: null,
  purged_at: '2026-11-20T09:00:00Z',
  ...changes,
})

const item = (changes: Partial<Trashed>): Trashed => ({
  kind: 'document',
  id: 1,
  name: 'invoice.pdf',
  place: null,
  documents: 0,
  mime_type: 'application/pdf',
  deleted_at: '2026-10-21T09:00:00Z',
  deleted_by: 'Ada',
  purged_at: '2026-11-20T09:00:00Z',
  ...changes,
})

describe('what the trash says of what it holds', () => {
  it('says where a file was and who deleted it', () => {
    expect(describeTrashed(item({ place: 'Invoices/2024' }), '21 Oct')).toBe('In Invoices/2024 · Deleted by Ada on 21 Oct')
    expect(describeTrashed(item({ deleted_by: null }), '21 Oct')).toBe('Deleted on 21 Oct')
  })

  it('counts the files that went with a folder', () => {
    expect(describeTrashed(item({ kind: 'folder', name: 'Old', documents: 3 }), '21 Oct')).toBe('3 files · Deleted by Ada on 21 Oct')
    expect(describeTrashed(item({ kind: 'folder', name: 'Old', documents: 1 }), '21 Oct')).toBe('1 file · Deleted by Ada on 21 Oct')
    expect(describeTrashed(item({ kind: 'folder', name: 'Old' }), '21 Oct')).toBe('No files · Deleted by Ada on 21 Oct')
  })

  it('names nobody for a chat, which is the asker’s own, and calls one without a title a new chat', () => {
    const chat = item({ kind: 'chat', name: '', mime_type: null })
    expect(describeTrashed(chat, '21 Oct')).toBe('Deleted on 21 Oct')
    expect(trashedName(chat)).toBe('New chat')
    expect(trashedName(item({}))).toBe('invoice.pdf')
  })

  it('says what a project in the trash holds', () => {
    expect(describeTrashedProject(project({}), '21 Oct')).toBe('12 documents · Deleted on 21 Oct')
  })
})

describe('a deletion', () => {
  it('names one thing, and counts several', () => {
    expect(movedToTrash(['msa.pdf'])).toBe('“msa.pdf” is in the trash.')
    expect(movedToTrash(['msa.pdf', 'Invoices'])).toBe('2 items are in the trash.')
  })
})

describe('a deletion for good', () => {
  it('names the file and where it was', () => {
    expect(purgeQuestion(item({ place: 'Invoices/2024' }))).toEqual({
      title: 'Delete “invoice.pdf” for good?',
      body: 'The file “invoice.pdf” from Invoices/2024 is deleted for good, with its pages and what was read from it. This cannot be undone.',
      action: 'Delete for good',
    })
  })

  it('says that a folder takes everything under it, what was deleted from it before as well', () => {
    expect(purgeQuestion(item({ kind: 'folder', name: 'Old', documents: 3 })).body).toBe(
      'The folder “Old” is deleted for good with everything in it: the 3 files deleted with it, and anything deleted from it earlier. This cannot be undone.',
    )
    expect(purgeQuestion(item({ kind: 'folder', name: 'Old', place: 'Invoices', documents: 1 })).body).toContain(
      'The folder “Old” from Invoices is deleted for good with everything in it: the file deleted with it,',
    )
    expect(purgeQuestion(item({ kind: 'folder', name: 'Old' })).body).toBe(
      'The folder “Old” is deleted for good with everything in it, anything deleted from it earlier included. This cannot be undone.',
    )
  })

  it('names a chat as the trash does', () => {
    const chat = purgeQuestion(item({ kind: 'chat', name: '', mime_type: null }))
    expect(chat.title).toBe('Delete “New chat” for good?')
    expect(chat.body).toBe('The chat “New chat” is deleted for good, with its answers. This cannot be undone.')
  })

  it('counts what emptying the trash deletes by kind while the list holds all of it', () => {
    const items = [
      item({ id: 1 }),
      item({ id: 2 }),
      item({ kind: 'folder', id: 3, name: 'Old', documents: 4 }),
      item({ kind: 'folder', id: 4, name: 'Older', documents: 3 }),
      item({ kind: 'chat', id: 5, name: 'Late deliveries' }),
    ]
    expect(emptyingQuestion({ items, total: 5, days: 30 })).toEqual({
      title: 'Empty the trash?',
      body: 'Everything in the trash is deleted for good: 2 files, 2 folders with 7 files in them and 1 chat. This cannot be undone.',
      action: 'Empty trash',
    })
    expect(emptyingQuestion({ items: [item({ kind: 'folder', name: 'Old' })], total: 1, days: 30 }).body).toBe(
      'Everything in the trash is deleted for good: 1 folder. This cannot be undone.',
    )
    expect(emptyingQuestion({ items: [item({}), item({ kind: 'folder', name: 'Old', documents: 1 })], total: 2, days: 30 }).body).toBe(
      'Everything in the trash is deleted for good: 1 file and 1 folder with 1 file in it. This cannot be undone.',
    )
  })

  it('counts the whole trash, not the list, once the list holds the newest alone', () => {
    expect(emptyingQuestion({ items: [item({})], total: 312, days: 30 }).body).toBe(
      'All 312 items in the trash are deleted for good, those deleted before the ones listed included. This cannot be undone.',
    )
  })

  it('says what a project takes with it', () => {
    expect(projectPurgeQuestion(project({}))).toEqual({
      title: 'Delete “Contracts” for good?',
      body: "The project is deleted for good with everything in it: its 12 documents, its folders, every member's chats in it and its own trash. This cannot be undone.",
      action: 'Delete for good',
    })
    expect(projectPurgeQuestion(project({ document_count: 1 })).body).toContain('everything in it: its document, its folders,')
    expect(projectPurgeQuestion(project({ document_count: 0 })).body).toContain('everything in it: its folders,')
  })
})
