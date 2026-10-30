import { describe, expect, it } from 'vitest'
import { searchAllParams } from './api.ts'
import {
  EMPTY_SEARCH_ALL,
  hitUrl,
  narrowsAll,
  parseSearchAll,
  searchAllParts,
  searchAllProblem,
  searchAllUrl,
  searchAllWords,
  withoutSearchAllPart,
} from './search.ts'

const PROJECTS = [
  { id: 3, name: 'Contracts' },
  { id: 7, name: 'Supplier archive' },
]

describe('the address of the search of every project', () => {
  it('keeps the words and what narrows them, and reads them back', () => {
    const search = { q: ' late delivery ', filter: { projects: [3, 7], types: ['invoice', 'credit note'], after: '2026-01-01', before: null } }
    const url = searchAllUrl(search)
    expect(url).toBe('/search?q=late+delivery&project=3&project=7&type=invoice&type=credit+note&after=2026-01-01')
    expect(parseSearchAll(new URL(url, 'http://emudoc.test').searchParams)).toEqual({ ...search, q: 'late delivery' })
    expect(searchAllUrl({ q: '  ', filter: EMPTY_SEARCH_ALL })).toBe('/search')
  })

  it('leaves out what it cannot read', () => {
    const params = new URLSearchParams('q=x&project=0&project=abc&project=4&project=4&type=&type=memo&after=March&before=2026-02-30')
    expect(parseSearchAll(params)).toEqual({ q: 'x', filter: { projects: [4], types: ['memo'], after: null, before: '2026-02-30' } })
    expect(parseSearchAll(new URLSearchParams())).toEqual({ q: '', filter: EMPTY_SEARCH_ALL })
  })

  it('asks the route for each part, a list once a value', () => {
    const params = searchAllParams('total', { projects: [3, 7], types: ['invoice'], after: null, before: '2026-03-31' })
    expect(params.toString()).toBe('q=total&limit=20&projects=3&projects=7&types=invoice&before=2026-03-31')
  })
})

describe('what narrows the search of every project', () => {
  const filter = { projects: [7, 3], types: ['invoice', 'receipt'], after: '2026-01-01', before: '2026-03-31' }

  it('says each part, the projects by their names now', () => {
    expect(searchAllParts(filter, PROJECTS)).toEqual([
      { part: 'projects', words: 'in “Supplier archive” or “Contracts”' },
      { part: 'types', words: 'of type invoice or receipt' },
      { part: 'dates', words: expect.stringMatching(/^dated .+ to .+$/) },
    ])
    expect(searchAllParts({ ...EMPTY_SEARCH_ALL, projects: [9] }, PROJECTS)).toEqual([{ part: 'projects', words: 'in a project you do not see' }])
    expect(searchAllWords({ ...EMPTY_SEARCH_ALL, projects: [3], types: ['invoice'] }, PROJECTS)).toBe('Documents in “Contracts”, of type invoice')
    expect(searchAllWords(EMPTY_SEARCH_ALL, PROJECTS)).toBe('Every project you see')
  })

  it('takes a part off alone', () => {
    expect(withoutSearchAllPart(filter, 'projects')).toEqual({ ...filter, projects: [] })
    expect(withoutSearchAllPart(filter, 'types')).toEqual({ ...filter, types: [] })
    expect(withoutSearchAllPart(filter, 'dates')).toEqual({ ...filter, after: null, before: null })
    expect(narrowsAll(filter)).toBe(true)
    expect(narrowsAll(EMPTY_SEARCH_ALL)).toBe(false)
    expect(narrowsAll({ ...EMPTY_SEARCH_ALL, before: '2026-01-01' })).toBe(true)
  })

  it('refuses dates the wrong way round, as the server would', () => {
    expect(searchAllProblem({ ...EMPTY_SEARCH_ALL, after: '2026-02-01', before: '2026-01-01' })).toBe('Its earliest date is after its latest. Choose dates the other way round.')
    expect(searchAllProblem(filter)).toBeNull()
  })
})

it('opens a hit in its project, at its passage', () => {
  expect(hitUrl({ project_id: 7, document_id: 12, passage_id: 345 })).toBe('/projects/7/documents/12?passage=345')
})
