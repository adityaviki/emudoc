import { describe, expect, it } from 'vitest'
import type { ChatScope } from './api.ts'
import { SUMMARY_QUESTION, comparisonQuestion, questionsFor, sameScope, scopeIn, scopeOf, scopeWords } from './scopes.ts'

const project: ChatScope = { kind: 'project', folder: null, documents: [] }
const folder: ChatScope = { kind: 'folder', folder: { id: 7, name: '2024' }, documents: [] }
const two: ChatScope = {
  kind: 'documents',
  folder: null,
  documents: [
    { id: 41, filename: 'contract.pdf' },
    { id: 42, filename: 'invoice.md' },
  ],
}
const paths = (id: number) => (id === 7 ? 'Invoices/2024' : '')

describe('scopeWords', () => {
  it('names the project, the folder by its path and the documents by their names', () => {
    expect(scopeWords(project, paths)).toBe('All documents')
    expect(scopeWords(folder, paths)).toBe('Invoices/2024')
    expect(scopeWords({ ...folder, folder: { id: 8, name: 'Unknown' } }, paths)).toBe('Unknown')
    expect(scopeWords({ ...two, documents: two.documents.slice(0, 1) }, paths)).toBe('contract.pdf')
    expect(scopeWords(two, paths)).toBe('contract.pdf and invoice.md')
    expect(scopeWords({ ...two, documents: [...two.documents, { id: 43, filename: 'log.csv' }] }, paths)).toBe('3 documents')
  })

  it('says when what the chat was about is gone', () => {
    expect(scopeWords({ ...folder, folder: null }, paths)).toBe('A folder no longer in the project')
    expect(scopeWords({ ...two, documents: [] }, paths)).toBe('Documents no longer in the project')
  })
})

describe('scopeIn and scopeOf', () => {
  it('turns a scope shown into one the server takes, and back', () => {
    expect(scopeIn(project)).toEqual({})
    expect(scopeIn(folder)).toEqual({ folder_id: 7 })
    expect(scopeIn(two)).toEqual({ document_ids: [41, 42] })
    // Gone, it is the whole project again when chosen anew.
    expect(scopeIn({ ...folder, folder: null })).toEqual({})
  })

  it("names a new chat's scope from what the page holds", () => {
    const folderName = (id: number) => (id === 7 ? '2024' : undefined)
    const nameOf = (id: number) => (id === 41 ? 'contract.pdf' : undefined)
    expect(scopeOf(null, folderName, nameOf)).toEqual(project)
    expect(scopeOf({}, folderName, nameOf)).toEqual(project)
    expect(scopeOf({ folder_id: 7 }, folderName, nameOf)).toEqual(folder)
    expect(scopeOf({ folder_id: 9 }, folderName, nameOf)).toEqual({ kind: 'folder', folder: null, documents: [] })
    expect(scopeOf({ document_ids: [41, 42] }, folderName, nameOf).documents).toEqual([
      { id: 41, filename: 'contract.pdf' },
      { id: 42, filename: 'Document 42' },
    ])
  })

  it('compares scopes by what they name, whatever the order of the documents', () => {
    expect(sameScope({}, {})).toBe(true)
    expect(sameScope({ document_ids: [] }, {})).toBe(true)
    expect(sameScope({ folder_id: 7 }, { folder_id: 7 })).toBe(true)
    expect(sameScope({ folder_id: 7 }, { folder_id: 8 })).toBe(false)
    expect(sameScope({ document_ids: [42, 41] }, { document_ids: [41, 42] })).toBe(true)
    expect(sameScope({ document_ids: [41] }, { folder_id: 41 })).toBe(false)
  })
})

describe('questionsFor', () => {
  it("offers a folder's summary and two documents' comparison, the project its own", () => {
    expect(questionsFor(project)).toBeNull()
    expect(questionsFor(folder)).toEqual([SUMMARY_QUESTION])
    expect(questionsFor(two)).toEqual([comparisonQuestion('contract.pdf', 'invoice.md')])
    expect(questionsFor(two)).toEqual(['Compare contract.pdf and invoice.md: where do they agree, and where do they differ?'])
    expect(questionsFor({ ...two, documents: two.documents.slice(1) })).toEqual(['Summarise invoice.md: what it is, and what it says.'])
    expect(questionsFor({ ...folder, folder: null })).toEqual([])
    expect(questionsFor({ ...two, documents: [] })).toEqual([])
  })
})
