import { describe, expect, it } from 'vitest'
import { EMPTY_FILTER } from '../library.ts'
import { buildWorkspaceUrl, parseWorkspaceUrl, sameFocus, withSearch } from './url.ts'
import type { WorkspaceUrl } from './url.ts'

const parse = (url: string) => {
  const [pathname, search = ''] = url.split('?')
  return parseWorkspaceUrl(pathname, search ? `?${search}` : '')
}

const home: WorkspaceUrl = {
  projectId: 3,
  library: { folderId: null },
  document: null,
  chatId: null,
  about: null,
  canonical: true,
}

describe('parseWorkspaceUrl', () => {
  it('reads a project with nothing open', () => {
    expect(parse('/projects/3')).toEqual(home)
    expect(parse('/projects/3/')).toEqual(home)
  })

  it("reads the folder, the library's search, the open document and the chat", () => {
    expect(parse('/projects/3/documents/41?folder=7&q=late+delivery&page=2&chat=12')).toEqual({
      ...home,
      library: { folderId: 7, search: { q: 'late delivery', filter: EMPTY_FILTER } },
      document: { id: 41, focus: { page: 2 } },
      chatId: 12,
    })
    expect(parse('/projects/3?chat=new')?.chatId).toBe('new')
    // Spaces alone search nothing.
    expect(parse('/projects/3?q=+')?.library).toEqual({ folderId: null })
    // The favourites' or the recent documents' names are searched with them.
    expect(parse('/projects/3?q=inv&favorites')?.library).toEqual({ folderId: null, favorites: true, search: { q: 'inv', filter: EMPTY_FILTER } })
  })

  it("reads what a new chat will be about, a folder or documents, and nothing beside another's", () => {
    expect(parse('/projects/3?chat=new&about=f7')?.about).toEqual({ folder_id: 7 })
    expect(parse('/projects/3?chat=new&about=d41-42')?.about).toEqual({ document_ids: [41, 42] })
    expect(parse('/projects/3?chat=new&about=d41')?.about).toEqual({ document_ids: [41] })
    for (const about of ['', 'f', 'fx', 'd', 'd41-', 'd-41', 'x7']) {
      expect(parse(`/projects/3?chat=new&about=${about}`)?.about, about).toBeNull()
    }
    // A chat that exists says what it is about itself.
    expect(parse('/projects/3?chat=12&about=f7')?.about).toBeNull()
  })

  it('opens a document at a passage before a block, and a block before a page', () => {
    expect(parse('/projects/3/documents/41?page=2&block=0&passage=812')?.document).toEqual({
      id: 41,
      focus: { passage: 812 },
    })
    expect(parse('/projects/3/documents/41?page=2&block=0')?.document?.focus).toEqual({ block: 0 })
    expect(parse('/projects/3/documents/41')?.document?.focus).toBeNull()
  })

  it('ignores parameters that are not ids', () => {
    expect(parse('/projects/3/documents/41?folder=x&chat=-1&passage=1.5&block=b')).toEqual({
      ...home,
      document: { id: 41, focus: null },
    })
  })

  it('reads the old chat page, the search tab and the filter by name as the workspace, to be rewritten', () => {
    expect(parse('/projects/3/ask')).toEqual({ ...home, canonical: false })
    expect(parse('/projects/3/ask/5')).toEqual({ ...home, chatId: 5, canonical: false })
    expect(parse('/projects/3/ask/new')).toEqual({ ...home, chatId: 'new', canonical: false })
    expect(parse('/projects/3/ask?chat=9')).toEqual({ ...home, chatId: 9, canonical: false })
    // The search tab's, from 2026-11-05 until the search was the library's again.
    expect(parse('/projects/3/search?folder=7&q=notice')).toEqual({
      ...home,
      library: { folderId: 7, search: { q: 'notice', filter: EMPTY_FILTER } },
      canonical: false,
    })
    expect(parse('/projects/3/search')).toEqual({ ...home, canonical: false })
    // The filter by name's words are the search's, unless the search tab had words of its own beside it.
    expect(parse('/projects/3?favorites&name=inv')).toEqual({
      ...home,
      library: { folderId: null, favorites: true, search: { q: 'inv', filter: EMPTY_FILTER } },
      canonical: false,
    })
    expect(parse('/projects/3/documents/41?name=inv&q=notice')?.library).toEqual({ folderId: null, search: { q: 'notice', filter: EMPTY_FILTER } })
    expect(parse('/projects/3?name=+')).toEqual({ ...home, canonical: false })
  })

  it('says null for a path outside a project workspace', () => {
    for (const path of ['/', '/projects', '/projects/x', '/projects/3/settings', '/projects/3/documents/x', '/account']) {
      expect(parse(path), path).toBeNull()
    }
  })
})

describe('buildWorkspaceUrl', () => {
  it('writes only what is set', () => {
    expect(buildWorkspaceUrl(home)).toBe('/projects/3')
    expect(
      buildWorkspaceUrl({
        ...home,
        library: { folderId: 7, search: { q: 'late delivery', filter: EMPTY_FILTER } },
        document: { id: 41, focus: { passage: 812 } },
        chatId: 'new',
      }),
    ).toBe('/projects/3/documents/41?folder=7&q=late+delivery&passage=812&chat=new')
    expect(buildWorkspaceUrl({ ...home, library: { folderId: null, search: { q: 'notice', filter: EMPTY_FILTER } } })).toBe('/projects/3?q=notice')
    expect(buildWorkspaceUrl({ ...home, library: { folderId: null, trash: true } })).toBe('/projects/3?trash=')
    expect(buildWorkspaceUrl({ ...home, library: { folderId: null, templates: true } })).toBe('/projects/3?templates=')
    expect(buildWorkspaceUrl({ ...home, library: { folderId: null, facts: 'purchase order' } })).toBe('/projects/3?facts=purchase+order')
    expect(buildWorkspaceUrl({ ...home, library: { folderId: null, changes: 'invoice' } })).toBe('/projects/3?changes=invoice')
    expect(buildWorkspaceUrl({ ...home, library: { folderId: null, checks: true } })).toBe('/projects/3?checks=')
    expect(buildWorkspaceUrl({ ...home, library: { folderId: null, check: 5 } })).toBe('/projects/3?check=5')
    expect(buildWorkspaceUrl({ ...home, library: { folderId: null, run: 8 } })).toBe('/projects/3?run=8')
    expect(buildWorkspaceUrl({ ...home, library: { folderId: null, questionSets: true } })).toBe('/projects/3?question-sets=')
    expect(buildWorkspaceUrl({ ...home, library: { folderId: null, report: 12 } })).toBe('/projects/3?report=12')
    expect(buildWorkspaceUrl({ ...home, library: { folderId: null, reviews: 'all' } })).toBe('/projects/3?reviews=')
    expect(buildWorkspaceUrl({ ...home, library: { folderId: null, reviews: 'mine' } })).toBe('/projects/3?reviews=mine')
    expect(buildWorkspaceUrl({ ...home, library: { folderId: null, writingTemplates: true } })).toBe('/projects/3?writing-templates=')
    expect(buildWorkspaceUrl({ ...home, library: { folderId: null, mail: true } })).toBe('/projects/3?mail=')
    expect(buildWorkspaceUrl({ ...home, library: { folderId: null, connectors: true } })).toBe('/projects/3?connectors=')
    expect(buildWorkspaceUrl({ ...home, library: { folderId: null, favorites: true } })).toBe('/projects/3?favorites=')
    expect(buildWorkspaceUrl({ ...home, library: { folderId: null, recent: true } })).toBe('/projects/3?recent=')
    expect(buildWorkspaceUrl({ ...home, chatId: 'new', about: { folder_id: 7 } })).toBe('/projects/3?chat=new&about=f7')
    expect(buildWorkspaceUrl({ ...home, chatId: 'new', about: { document_ids: [41, 42] } })).toBe('/projects/3?chat=new&about=d41-42')
    expect(buildWorkspaceUrl({ ...home, chatId: 'new', about: {} })).toBe('/projects/3?chat=new')
    expect(buildWorkspaceUrl({ ...home, chatId: 12, about: { folder_id: 7 } })).toBe('/projects/3?chat=12')
  })

  it('reads back what it wrote', () => {
    const urls: WorkspaceUrl[] = [
      home,
      { ...home, chatId: 12 },
      { ...home, library: { folderId: null, search: { q: 'a & b = c?', filter: EMPTY_FILTER } } },
      { ...home, library: { folderId: null, search: { q: '', filter: { ...EMPTY_FILTER, mentions: ['March'] } } }, chatId: 12 },
      { ...home, library: { folderId: 7, search: { q: 'notice', filter: EMPTY_FILTER } }, document: { id: 41, focus: { passage: 812 } } },
      { ...home, library: { folderId: null, favorites: true, search: { q: 'inv', filter: EMPTY_FILTER } } },
      { ...home, document: { id: 41, focus: { block: 0 } }, chatId: 12 },
      { ...home, library: { folderId: 7 }, document: { id: 41, focus: { page: 3 } } },
      { ...home, library: { folderId: null, trash: true }, chatId: 12 },
      { ...home, library: { folderId: null, templates: true }, document: { id: 41, focus: null } },
      { ...home, library: { folderId: null, facts: 'delivery note' }, document: { id: 41, focus: { block: 7 } } },
      { ...home, library: { folderId: null, changes: 'delivery note' }, chatId: 12 },
      { ...home, library: { folderId: null, checks: true }, chatId: 12 },
      { ...home, library: { folderId: null, check: 5 }, document: { id: 41, focus: { block: 2 } } },
      { ...home, library: { folderId: null, run: 8 }, document: { id: 41, focus: { block: 2 } } },
      { ...home, library: { folderId: null, questionSets: true }, chatId: 12 },
      { ...home, library: { folderId: null, report: 12 }, document: { id: 41, focus: { passage: 812 } } },
      { ...home, library: { folderId: null, reviews: 'all' }, chatId: 12 },
      { ...home, library: { folderId: null, reviews: 'mine' }, document: { id: 41, focus: null } },
      { ...home, library: { folderId: null, writingTemplates: true }, chatId: 12 },
      { ...home, library: { folderId: null, mail: true }, chatId: 12 },
      { ...home, library: { folderId: null, connectors: true } },
      { ...home, library: { folderId: null, recent: true }, document: { id: 41, focus: null } },
      { ...home, library: { folderId: null, favorites: true } },
      { ...home, library: { folderId: null, recent: true } },
      { ...home, chatId: 'new', about: { folder_id: 7 } },
      { ...home, document: { id: 41, focus: null }, chatId: 'new', about: { document_ids: [41, 42] } },
    ]
    for (const url of urls) expect(parse(buildWorkspaceUrl(url))).toEqual(url)
  })

  it('rewrites an old URL to the current shape', () => {
    expect(buildWorkspaceUrl(parse('/projects/3/ask/5')!)).toBe('/projects/3?chat=5')
    expect(buildWorkspaceUrl(parse('/projects/3/search?folder=7&q=notice')!)).toBe('/projects/3?folder=7&q=notice')
    expect(buildWorkspaceUrl(parse('/projects/3/documents/41?recent&name=inv')!)).toBe('/projects/3/documents/41?q=inv&recent=')
    // A notice's link to a thread of comments, from before they went, opens its document.
    expect(buildWorkspaceUrl(parse('/projects/3/documents/41?thread=5')!)).toBe('/projects/3/documents/41')
  })
})

describe("the library's search", () => {
  const filter = { ...EMPTY_FILTER, types: ['invoice'] }

  it('is set in the place it is run from, its words trimmed, the place kept to go back to', () => {
    expect(withSearch({ folderId: 7 }, { q: ' late fee ', filter })).toEqual({ folderId: 7, search: { q: 'late fee', filter } })
    expect(withSearch({ folderId: null, favorites: true }, { q: 'inv', filter: EMPTY_FILTER })).toEqual({
      folderId: null,
      favorites: true,
      search: { q: 'inv', filter: EMPTY_FILTER },
    })
    expect(withSearch({ folderId: 7, search: { q: 'notice', filter } }, { q: 'late fee', filter })).toEqual({ folderId: 7, search: { q: 'late fee', filter } })
  })

  it('stays without words while something narrows it, and ends once nothing does', () => {
    expect(withSearch({ folderId: 7, search: { q: 'notice', filter } }, { q: '  ', filter })).toEqual({ folderId: 7, search: { q: '', filter } })
    expect(withSearch({ folderId: 7, search: { q: 'notice', filter } }, { q: '', filter: EMPTY_FILTER })).toEqual({ folderId: 7 })
    expect(withSearch({ folderId: 7, search: { q: 'notice', filter } }, null)).toEqual({ folderId: 7 })
  })
})

describe('a search in the URL', () => {
  const filter = {
    types: ['invoice', 'credit note'],
    after: '2024-01-01',
    before: null,
    reviews: ['disputed' as const],
    folder_id: 9,
    mentions: ['late delivery', 'penalty'],
  }

  it("keeps what narrows it, each part its own parameter, its folder apart from the library's, and reads it back", () => {
    const url = buildWorkspaceUrl({ ...home, library: { folderId: 7, search: { q: 'notice', filter } } })
    expect(url).toBe(
      '/projects/3?folder=7&q=notice&in=9&type=invoice&type=credit+note&after=2024-01-01&review=disputed&within=late+delivery&within=penalty',
    )
    expect(parse(url)).toEqual({ ...home, library: { folderId: 7, search: { q: 'notice', filter } } })
  })

  it('leaves out what it cannot read, and opens no search where nothing is asked', () => {
    expect(parse('/projects/3?q=notice&after=2024-1-1&review=lost&within=+&in=y')?.library.search).toEqual({ q: 'notice', filter: EMPTY_FILTER })
    expect(parse('/projects/3/documents/41?within=penalty')?.library.search).toEqual({ q: '', filter: { ...EMPTY_FILTER, mentions: ['penalty'] } })
    expect(parse('/projects/3/documents/41?in=x&q=+')?.library.search).toBeUndefined()
  })
})

describe('sameFocus', () => {
  it('compares what a document is opened at, with nothing as nothing', () => {
    expect(sameFocus({ page: 2 }, { page: 2 })).toBe(true)
    expect(sameFocus({ page: 2 }, { block: 2 })).toBe(false)
    expect(sameFocus(null, undefined)).toBe(true)
    expect(sameFocus(null, { passage: 1 })).toBe(false)
  })
})
