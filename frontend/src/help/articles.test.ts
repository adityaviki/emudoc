import { describe, expect, it } from 'vitest'
import type { HelpArticle } from '../api.ts'
import { matching, nothingFound } from './articles.ts'

const article = (slug: string, title: string, text: string): HelpArticle => ({ slug, title, summary: '', text: `# ${title}\n\n${text}` })
const HELP = [
  article('documents', 'Uploading and organising documents', 'A file may be up to 100 MB. Deleting puts it in the trash.'),
  article('checking', 'Facts, checks and reviews', 'A cross-check compares a fact of one kind of document with another.'),
  article('data', 'Your data: exports, the trash and deletion', 'Exporting a project gives one ZIP file.'),
]

describe('matching', () => {
  it('finds the articles that hold every word, ignoring case', () => {
    expect(matching(HELP, 'ZIP file').map((found) => found.slug)).toEqual(['data'])
    expect(matching(HELP, 'cross-check fact').map((found) => found.slug)).toEqual(['checking'])
    expect(matching(HELP, 'invoice')).toEqual([])
  })

  it('puts the articles whose title holds a word first', () => {
    expect(matching(HELP, 'trash').map((found) => found.slug)).toEqual(['data', 'documents'])
  })

  it('gives every article, in order, for no words', () => {
    expect(matching(HELP, '  ')).toEqual(HELP)
  })
})

describe('nothingFound', () => {
  it('names the words, trimmed', () => {
    expect(nothingFound('  invoice ')).toBe('The help says nothing of “invoice”.')
  })
})
