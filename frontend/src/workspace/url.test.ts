import { describe, expect, it } from 'vitest'
import { buildWorkspaceUrl, parseWorkspaceUrl, sameFocus } from './url.ts'
import type { WorkspaceUrl } from './url.ts'

const parse = (url: string) => {
  const [pathname, search = ''] = url.split('?')
  return parseWorkspaceUrl(pathname, search ? `?${search}` : '')
}

const home: WorkspaceUrl = {
  projectId: 3,
  library: { folderId: null, q: '' },
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

  it('reads the folder, the search, the open document and the chat', () => {
    expect(parse('/projects/3/documents/41?folder=7&q=late+delivery&page=2&chat=12')).toEqual({
      ...home,
      library: { folderId: 7, q: 'late delivery' },
      document: { id: 41, focus: { page: 2 } },
      chatId: 12,
    })
    expect(parse('/projects/3?chat=new')?.chatId).toBe('new')
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

  it('reads the old chat and search pages as the workspace, to be rewritten', () => {
    expect(parse('/projects/3/ask')).toEqual({ ...home, canonical: false })
    expect(parse('/projects/3/ask/5')).toEqual({ ...home, chatId: 5, canonical: false })
    expect(parse('/projects/3/ask/new')).toEqual({ ...home, chatId: 'new', canonical: false })
    expect(parse('/projects/3/ask?chat=9')).toEqual({ ...home, chatId: 9, canonical: false })
    expect(parse('/projects/3/search?q=notice')).toEqual({
      ...home,
      library: { folderId: null, q: 'notice' },
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
        library: { folderId: 7, q: 'late delivery' },
        document: { id: 41, focus: { passage: 812 } },
        chatId: 'new',
      }),
    ).toBe('/projects/3/documents/41?folder=7&q=late+delivery&passage=812&chat=new')
    expect(buildWorkspaceUrl({ ...home, library: { folderId: null, q: '', trash: true } })).toBe('/projects/3?trash=')
    expect(buildWorkspaceUrl({ ...home, library: { folderId: null, q: '', templates: true } })).toBe('/projects/3?templates=')
    expect(buildWorkspaceUrl({ ...home, library: { folderId: null, q: '', facts: 'purchase order' } })).toBe('/projects/3?facts=purchase+order')
    expect(buildWorkspaceUrl({ ...home, library: { folderId: null, q: '', changes: 'invoice' } })).toBe('/projects/3?changes=invoice')
    expect(buildWorkspaceUrl({ ...home, library: { folderId: null, q: '', checks: true } })).toBe('/projects/3?checks=')
    expect(buildWorkspaceUrl({ ...home, library: { folderId: null, q: '', check: 5 } })).toBe('/projects/3?check=5')
    expect(buildWorkspaceUrl({ ...home, library: { folderId: null, q: '', run: 8 } })).toBe('/projects/3?run=8')
    expect(buildWorkspaceUrl({ ...home, library: { folderId: null, q: '', reviews: 'all' } })).toBe('/projects/3?reviews=')
    expect(buildWorkspaceUrl({ ...home, library: { folderId: null, q: '', reviews: 'mine' } })).toBe('/projects/3?reviews=mine')
    expect(buildWorkspaceUrl({ ...home, library: { folderId: null, q: '', drafts: true } })).toBe('/projects/3?drafts=')
    expect(buildWorkspaceUrl({ ...home, library: { folderId: null, q: '', draft: 9 } })).toBe('/projects/3?draft=9')
    expect(buildWorkspaceUrl({ ...home, library: { folderId: null, q: '', draftTemplates: true } })).toBe('/projects/3?draft-templates=')
    expect(buildWorkspaceUrl({ ...home, library: { folderId: null, q: '', mail: true } })).toBe('/projects/3?mail=')
    expect(buildWorkspaceUrl({ ...home, library: { folderId: null, q: '', connectors: true } })).toBe('/projects/3?connectors=')
    expect(buildWorkspaceUrl({ ...home, library: { folderId: null, q: '', tag: 4 } })).toBe('/projects/3?tag=4')
    expect(buildWorkspaceUrl({ ...home, library: { folderId: null, q: '', smart: 6 } })).toBe('/projects/3?smart=6')
    expect(buildWorkspaceUrl({ ...home, library: { folderId: null, q: '', favorites: true } })).toBe('/projects/3?favorites=')
    expect(buildWorkspaceUrl({ ...home, library: { folderId: null, q: '', recent: true } })).toBe('/projects/3?recent=')
    expect(buildWorkspaceUrl({ ...home, library: { folderId: null, q: '', tags: true } })).toBe('/projects/3?tags=')
    expect(buildWorkspaceUrl({ ...home, library: { folderId: null, q: '', filing: true } })).toBe('/projects/3?filing=')
    expect(buildWorkspaceUrl({ ...home, chatId: 'new', about: { folder_id: 7 } })).toBe('/projects/3?chat=new&about=f7')
    expect(buildWorkspaceUrl({ ...home, chatId: 'new', about: { document_ids: [41, 42] } })).toBe('/projects/3?chat=new&about=d41-42')
    expect(buildWorkspaceUrl({ ...home, chatId: 'new', about: {} })).toBe('/projects/3?chat=new')
    expect(buildWorkspaceUrl({ ...home, chatId: 12, about: { folder_id: 7 } })).toBe('/projects/3?chat=12')
  })

  it('reads back what it wrote', () => {
    const urls: WorkspaceUrl[] = [
      home,
      { ...home, chatId: 12 },
      { ...home, library: { folderId: null, q: 'a & b = c?' } },
      { ...home, document: { id: 41, focus: { block: 0 } }, chatId: 12 },
      { ...home, library: { folderId: 7, q: '' }, document: { id: 41, focus: { page: 3 } } },
      { ...home, library: { folderId: null, q: '', trash: true }, chatId: 12 },
      { ...home, library: { folderId: null, q: '', templates: true }, document: { id: 41, focus: null } },
      { ...home, library: { folderId: null, q: '', facts: 'delivery note' }, document: { id: 41, focus: { block: 7 } } },
      { ...home, library: { folderId: null, q: '', changes: 'delivery note' }, chatId: 12 },
      { ...home, library: { folderId: null, q: '', checks: true }, chatId: 12 },
      { ...home, library: { folderId: null, q: '', check: 5 }, document: { id: 41, focus: { block: 2 } } },
      { ...home, library: { folderId: null, q: '', run: 8 }, document: { id: 41, focus: { block: 2 } } },
      { ...home, library: { folderId: null, q: '', reviews: 'all' }, chatId: 12 },
      { ...home, library: { folderId: null, q: '', reviews: 'mine' }, document: { id: 41, focus: null } },
      { ...home, library: { folderId: null, q: '', drafts: true }, chatId: 12 },
      { ...home, library: { folderId: null, q: '', draft: 9 }, document: { id: 41, focus: { passage: 812 } } },
      { ...home, library: { folderId: null, q: '', draftTemplates: true } },
      { ...home, library: { folderId: null, q: '', mail: true }, chatId: 12 },
      { ...home, library: { folderId: null, q: '', connectors: true } },
      { ...home, library: { folderId: null, q: '', tag: 4 }, document: { id: 41, focus: null } },
      { ...home, library: { folderId: null, q: '', smart: 6 }, chatId: 12 },
      { ...home, library: { folderId: null, q: '', favorites: true } },
      { ...home, library: { folderId: null, q: '', recent: true } },
      { ...home, library: { folderId: null, q: '', tags: true } },
      { ...home, library: { folderId: null, q: '', filing: true } },
      { ...home, chatId: 'new', about: { folder_id: 7 } },
      { ...home, document: { id: 41, focus: null }, chatId: 'new', about: { document_ids: [41, 42] } },
    ]
    for (const url of urls) expect(parse(buildWorkspaceUrl(url))).toEqual(url)
  })

  it('rewrites an old URL to the current shape', () => {
    const old = parse('/projects/3/ask/5')!
    expect(buildWorkspaceUrl(old)).toBe('/projects/3?chat=5')
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
