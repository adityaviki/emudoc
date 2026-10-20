import { describe, expect, it } from 'vitest'
import type { Trashed, TrashedProject } from './api.ts'
import { describeTrashed, describeTrashedProject, movedToTrash, trashedName } from './trash.ts'

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
    const project: TrashedProject = {
      id: 4,
      name: 'Contracts',
      sample: false,
      document_count: 12,
      deleted_at: '2026-10-21T09:00:00Z',
      deleted_by: null,
      purged_at: '2026-11-20T09:00:00Z',
    }
    expect(describeTrashedProject(project, '21 Oct')).toBe('12 documents · Deleted on 21 Oct')
  })
})

describe('a deletion', () => {
  it('names one thing, and counts several', () => {
    expect(movedToTrash(['msa.pdf'])).toBe('“msa.pdf” is in the trash.')
    expect(movedToTrash(['msa.pdf', 'Invoices'])).toBe('2 items are in the trash.')
  })
})
