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
