// The workspace's URL: which project, where the library is, which document is open in the middle
// and which chat is open on the right. Nothing else reads or writes the route shape.

import { useCallback, useMemo } from 'react'
import { useLocation, useNavigate } from 'react-router'

/** What a document is opened at: a cited passage, a block of its text, or a page. */
export type Focus = { passage: number } | { block: number } | { page: number }

/** Whose documents in review the reviews view lists: every member's, or those assigned to the member. */
export type Reviews = 'all' | 'mine'

/** The folder the library shows, and the search running in it; or, with `trash`, the project's trash, with `templates`, its extraction templates, with `facts`, the facts table of its documents of that kind, with `changes`, what their last readings changed, with `checks`, its cross-checks, with `check`, what that one finds, with `run`, a run of them kept with what it found, with `reviews`, its documents in review, with `drafts`, the drafts the model wrote from its documents, with `draft`, one of them, with `draftTemplates`, how its drafts of each kind read, with `mail`, its address and what it received, and with `connectors`, the folders of its members' Google Drive, Dropbox or OneDrive it keeps in step with. */
export interface LibraryView {
  folderId: number | null
  q: string
  trash?: true
  templates?: true
  facts?: string
  changes?: string
  checks?: true
  check?: number
  run?: number
  reviews?: Reviews
  drafts?: true
  draft?: number
  draftTemplates?: true
  mail?: true
  connectors?: true
}

export interface OpenDocument {
  id: number
  focus: Focus | null
}

export interface WorkspaceUrl {
  projectId: number
  library: LibraryView
  /** The document in the middle pane; null leaves the library and the agent side by side. */
  document: OpenDocument | null
  chatId: number | 'new' | null
  /** False for an old URL shape (/ask, /search) that should be rewritten to the current one. */
  canonical: boolean
}

const PROJECT = /^\/projects\/(\d+)(?:\/(.*))?$/

export function parseWorkspaceUrl(pathname: string, search: string): WorkspaceUrl | null {
  const match = PROJECT.exec(pathname.replace(/\/+$/, ''))
  if (!match) return null
  const projectId = Number(match[1])
  const rest = match[2] ?? ''
  const params = new URLSearchParams(search)
  const chat = params.get('chat')
  let chatId: WorkspaceUrl['chatId'] = chat === 'new' ? 'new' : id(chat)
  const library: LibraryView = { folderId: id(params.get('folder')), q: params.get('q') ?? '' }
  if (params.has('trash')) library.trash = true
  else if (params.has('templates')) library.templates = true
  else if (params.get('facts')) library.facts = params.get('facts') ?? undefined
  else if (params.get('changes')) library.changes = params.get('changes') ?? undefined
  else if (id(params.get('check')) != null) library.check = id(params.get('check')) ?? undefined
  else if (id(params.get('run')) != null) library.run = id(params.get('run')) ?? undefined
  else if (params.has('checks')) library.checks = true
  else if (params.has('reviews')) library.reviews = params.get('reviews') === 'mine' ? 'mine' : 'all'
  else if (id(params.get('draft')) != null) library.draft = id(params.get('draft')) ?? undefined
  else if (params.has('drafts')) library.drafts = true
  else if (params.has('draft-templates')) library.draftTemplates = true
  else if (params.has('mail')) library.mail = true
  else if (params.has('connectors')) library.connectors = true

  let document: OpenDocument | null = null
  let canonical = true
  const opened = /^documents\/(\d+)$/.exec(rest)
  const legacyChat = /^ask(?:\/(\d+|new))?$/.exec(rest)
  if (opened) {
    document = { id: Number(opened[1]), focus: parseFocus(params) }
  } else if (legacyChat) {
    canonical = false
    chatId = legacyChat[1] === 'new' ? 'new' : (id(legacyChat[1] ?? null) ?? chatId)
  } else if (rest === 'search') {
    canonical = false
  } else if (rest !== '') {
    return null
  }
  return { projectId, library, document, chatId, canonical }
}

function parseFocus(params: URLSearchParams): Focus | null {
  const passage = id(params.get('passage'))
  if (passage != null) return { passage }
  const block = params.get('block')
  if (block != null && /^\d+$/.test(block)) return { block: Number(block) }
  const page = id(params.get('page'))
  return page != null ? { page } : null
}

function id(value: string | null): number | null {
  return value != null && /^\d+$/.test(value) ? Number(value) : null
}

export function buildWorkspaceUrl(url: Omit<WorkspaceUrl, 'canonical'>): string {
  const params = new URLSearchParams()
  let path = `/projects/${url.projectId}`
  if (url.library.folderId != null) params.set('folder', String(url.library.folderId))
  if (url.library.q) params.set('q', url.library.q)
  if (url.library.trash) params.set('trash', '')
  if (url.library.templates) params.set('templates', '')
  if (url.library.facts) params.set('facts', url.library.facts)
  if (url.library.changes) params.set('changes', url.library.changes)
  if (url.library.checks) params.set('checks', '')
  if (url.library.check != null) params.set('check', String(url.library.check))
  if (url.library.run != null) params.set('run', String(url.library.run))
  if (url.library.reviews) params.set('reviews', url.library.reviews === 'mine' ? 'mine' : '')
  if (url.library.drafts) params.set('drafts', '')
  if (url.library.draft != null) params.set('draft', String(url.library.draft))
  if (url.library.draftTemplates) params.set('draft-templates', '')
  if (url.library.mail) params.set('mail', '')
  if (url.library.connectors) params.set('connectors', '')
  if (url.document) {
    path += `/documents/${url.document.id}`
    const focus = url.document.focus
    if (focus) {
      if ('passage' in focus) params.set('passage', String(focus.passage))
      else if ('block' in focus) params.set('block', String(focus.block))
      else params.set('page', String(focus.page))
    }
  }
  if (url.chatId != null) params.set('chat', String(url.chatId))
  const query = params.toString()
  return query ? `${path}?${query}` : path
}

export type Go = (patch: Partial<Omit<WorkspaceUrl, 'canonical'>>, options?: { replace?: boolean }) => void

/** The parsed URL, and a way to change part of it while keeping the rest. */
export function useWorkspaceUrl(): [WorkspaceUrl | null, Go] {
  const location = useLocation()
  const navigate = useNavigate()
  const url = useMemo(
    () => parseWorkspaceUrl(location.pathname, location.search),
    [location.pathname, location.search],
  )
  const go = useCallback<Go>(
    (patch, options) => {
      // Read the live URL rather than the rendered one: two changes in one tick must both land.
      const current = parseWorkspaceUrl(window.location.pathname, window.location.search)
      if (!current) return
      navigate(buildWorkspaceUrl({ ...current, ...patch }), { replace: options?.replace })
    },
    [navigate],
  )
  return [url, go]
}

export function sameFocus(a: Focus | null | undefined, b: Focus | null | undefined): boolean {
  return JSON.stringify(a ?? null) === JSON.stringify(b ?? null)
}
