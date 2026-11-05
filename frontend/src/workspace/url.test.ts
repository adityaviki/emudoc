import { describe, expect, it } from 'vitest'
import { EMPTY_FILTER } from '../library.ts'
import { buildWorkspaceUrl, parseWorkspaceUrl, sameFocus, searchedUrl, searching } from './url.ts'
import type { WorkspaceUrl } from './url.ts'

const parse = (url: string) => {
  const [pathname, search = ''] = url.split('?')
  return parseWorkspaceUrl(pathname, search ? `?${search}` : '')
}

const home: WorkspaceUrl = {
  projectId: 3,
  library: { folderId: null },
  document: null,
  search: null,
  chatId: null,
  about: null,
  canonical: true,
}

describe('parseWorkspaceUrl', () => {
  it('reads a project with nothing open', () => {
    expect(parse('/projects/3')).toEqual(home)
    expect(parse('/projects/3/')).toEqual(home)
  })

  it('reads the folder, its filter by name, the search, the open document and the chat', () => {
    expect(parse('/projects/3/documents/41?folder=7&name=inv&q=late+delivery&page=2&chat=12')).toEqual({
      ...home,
      library: { folderId: 7, name: 'inv' },
      document: { id: 41, focus: { page: 2 } },
      search: { q: 'late delivery', filter: EMPTY_FILTER },
      chatId: 12,
    })
    expect(parse('/projects/3?chat=new')?.chatId).toBe('new')
    // Spaces alone filter nothing.
    expect(parse('/projects/3?name=+')?.library).toEqual({ folderId: null })
  })

  it('reads the search tab shown, its words or none, and it beside a document open in front of it', () => {
    const shown = parse('/projects/3/search?q=notice')!
    expect(shown).toEqual({ ...home, search: { q: 'notice', filter: EMPTY_FILTER } })
    expect(searching(shown)).toBe(true)
    expect(parse('/projects/3/search')?.search).toEqual({ q: '', filter: EMPTY_FILTER })
    const behind = parse('/projects/3/documents/41?q=notice')!
    expect(behind.search).toEqual({ q: 'notice', filter: EMPTY_FILTER })
    expect(searching(behind)).toBe(false)
    expect(searching(home)).toBe(false)
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

  it('reads the old chat page, and a search in the library as it was, as the workspace, to be rewritten', () => {
    expect(parse('/projects/3/ask')).toEqual({ ...home, canonical: false })
    expect(parse('/projects/3/ask/5')).toEqual({ ...home, chatId: 5, canonical: false })
    expect(parse('/projects/3/ask/new')).toEqual({ ...home, chatId: 'new', canonical: false })
    expect(parse('/projects/3/ask?chat=9')).toEqual({ ...home, chatId: 9, canonical: false })
    expect(parse('/projects/3?folder=7&q=notice')).toEqual({
      ...home,
      library: { folderId: 7 },
      search: { q: 'notice', filter: EMPTY_FILTER },
      canonical: false,
    })
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
        library: { folderId: 7, name: 'inv' },
        document: { id: 41, focus: { passage: 812 } },
        search: { q: 'late delivery', filter: EMPTY_FILTER },
        chatId: 'new',
      }),
    ).toBe('/projects/3/documents/41?folder=7&name=inv&q=late+delivery&passage=812&chat=new')
    expect(buildWorkspaceUrl({ ...home, search: { q: 'notice', filter: EMPTY_FILTER } })).toBe('/projects/3/search?q=notice')
    expect(buildWorkspaceUrl({ ...home, search: { q: '', filter: EMPTY_FILTER } })).toBe('/projects/3/search')
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
    expect(buildWorkspaceUrl({ ...home, library: { folderId: null, activity: true } })).toBe('/projects/3?activity=')
    expect(buildWorkspaceUrl({ ...home, document: { id: 41, focus: { thread: 5 } } })).toBe('/projects/3/documents/41?thread=5')
    expect(buildWorkspaceUrl({ ...home, library: { folderId: null, drafts: true } })).toBe('/projects/3?drafts=')
    expect(buildWorkspaceUrl({ ...home, library: { folderId: null, draft: 9 } })).toBe('/projects/3?draft=9')
    expect(buildWorkspaceUrl({ ...home, library: { folderId: null, draftTemplates: true } })).toBe('/projects/3?draft-templates=')
    expect(buildWorkspaceUrl({ ...home, library: { folderId: null, mail: true } })).toBe('/projects/3?mail=')
    expect(buildWorkspaceUrl({ ...home, library: { folderId: null, connectors: true } })).toBe('/projects/3?connectors=')
    expect(buildWorkspaceUrl({ ...home, library: { folderId: null, tag: 4 } })).toBe('/projects/3?tag=4')
    expect(buildWorkspaceUrl({ ...home, library: { folderId: null, favorites: true } })).toBe('/projects/3?favorites=')
    expect(buildWorkspaceUrl({ ...home, library: { folderId: null, recent: true } })).toBe('/projects/3?recent=')
    expect(buildWorkspaceUrl({ ...home, library: { folderId: null, tags: true } })).toBe('/projects/3?tags=')
    expect(buildWorkspaceUrl({ ...home, chatId: 'new', about: { folder_id: 7 } })).toBe('/projects/3?chat=new&about=f7')
    expect(buildWorkspaceUrl({ ...home, chatId: 'new', about: { document_ids: [41, 42] } })).toBe('/projects/3?chat=new&about=d41-42')
    expect(buildWorkspaceUrl({ ...home, chatId: 'new', about: {} })).toBe('/projects/3?chat=new')
    expect(buildWorkspaceUrl({ ...home, chatId: 12, about: { folder_id: 7 } })).toBe('/projects/3?chat=12')
  })

  it('reads back what it wrote', () => {
    const urls: WorkspaceUrl[] = [
      home,
      { ...home, chatId: 12 },
      { ...home, search: { q: 'a & b = c?', filter: EMPTY_FILTER } },
      { ...home, search: { q: '', filter: EMPTY_FILTER }, chatId: 12 },
      { ...home, search: { q: 'notice', filter: EMPTY_FILTER }, document: { id: 41, focus: { passage: 812 } } },
      { ...home, library: { folderId: 7, name: 'a & b' } },
      { ...home, library: { folderId: null, tag: 4, name: 'inv' } },
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
      { ...home, library: { folderId: null, activity: true }, document: { id: 41, focus: { thread: 5 } } },
      { ...home, library: { folderId: null, drafts: true }, chatId: 12 },
      { ...home, library: { folderId: null, draft: 9 }, document: { id: 41, focus: { passage: 812 } } },
      { ...home, library: { folderId: null, draftTemplates: true } },
      { ...home, library: { folderId: null, mail: true }, chatId: 12 },
      { ...home, library: { folderId: null, connectors: true } },
      { ...home, library: { folderId: null, tag: 4 }, document: { id: 41, focus: null } },
      { ...home, library: { folderId: null, favorites: true } },
      { ...home, library: { folderId: null, recent: true } },
      { ...home, library: { folderId: null, tags: true } },
      { ...home, chatId: 'new', about: { folder_id: 7 } },
      { ...home, document: { id: 41, focus: null }, chatId: 'new', about: { document_ids: [41, 42] } },
    ]
    for (const url of urls) expect(parse(buildWorkspaceUrl(url))).toEqual(url)
  })

  it('rewrites an old URL to the current shape', () => {
    expect(buildWorkspaceUrl(parse('/projects/3/ask/5')!)).toBe('/projects/3?chat=5')
    expect(buildWorkspaceUrl(parse('/projects/3?folder=7&q=notice')!)).toBe('/projects/3/search?folder=7&q=notice')
  })

  it("shows the header's words in the search tab, in front of a document, what narrows the search kept", () => {
    const filter = { ...EMPTY_FILTER, types: ['invoice'] }
    const reading = { ...home, document: { id: 41, focus: null }, search: { q: 'notice', filter } }
    expect(searchedUrl(reading, 'late fee')).toBe('/projects/3/search?q=late+fee&type=invoice')
    expect(searchedUrl({ ...home, library: { folderId: 7 } }, 'late fee')).toBe('/projects/3/search?folder=7&q=late+fee')
  })
})

describe('a search in the URL', () => {
  const filter = {
    tags: [4, 5],
    every_tag: true,
    types: ['invoice', 'credit note'],
    after: '2024-01-01',
    before: null,
    reviews: ['disputed' as const],
    folder_id: 9,
    mentions: ['late delivery', 'penalty'],
  }

  it("keeps what narrows it, each part its own parameter, its folder apart from the library's, and reads it back", () => {
    const url = buildWorkspaceUrl({ ...home, library: { folderId: 7 }, search: { q: 'notice', filter } })
    expect(url).toBe(
      '/projects/3/search?folder=7&q=notice&in=9&tagged=4&tagged=5&every-tag=&type=invoice&type=credit+note&after=2024-01-01&review=disputed&within=late+delivery&within=penalty',
    )
    expect(parse(url)).toEqual({ ...home, library: { folderId: 7 }, search: { q: 'notice', filter } })
  })

  it('leaves out what it cannot read, and opens no search where nothing is asked', () => {
    expect(parse('/projects/3/search?q=notice&tagged=x&after=2024-1-1&review=lost&within=+&every-tag=&in=y')?.search).toEqual({ q: 'notice', filter: EMPTY_FILTER })
    expect(parse('/projects/3/search?tagged=4&every-tag=')?.search?.filter).toMatchObject({ tags: [4], every_tag: false })
    expect(parse('/projects/3/documents/41?within=penalty')?.search).toEqual({ q: '', filter: { ...EMPTY_FILTER, mentions: ['penalty'] } })
    expect(parse('/projects/3/documents/41?tagged=x&q=+')?.search).toBeNull()
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
