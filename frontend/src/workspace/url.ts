// The workspace's URL: which project, where the library is and what it searches, which document is open in the middle
// and which chat is open on the right. Nothing else reads or writes the route shape.

import { useCallback, useMemo } from 'react'
import { useLocation, useNavigate } from 'react-router'
import type { DocumentFilter, ReviewState, ScopeIn } from '../api.ts'
import { narrows } from '../library.ts'
import { REVIEW_STATES } from '../reviews.ts'

/** What a document is opened at: a cited passage, a block of its text, a page, or a thread of its comments. */
export type Focus = { passage: number } | { block: number } | { page: number } | { thread: number }

/** Whose documents in review the reviews view lists: every member's, or those assigned to the member. */
export type Reviews = 'all' | 'mine'

/** The library's search: its words, which the files' names are matched by and the passages searched for, and what narrows the passages. */
export interface WorkspaceSearch {
  q: string
  filter: DocumentFilter
}

/** The folder the library shows, with `search` the project's search in its place (the files whose names hold its words, wherever they are, then the passages it finds); or, with `trash`, the project's trash, with `templates`, its extraction templates, with `facts`, the facts table of its documents of that kind, with `changes`, what their last readings changed, with `checks`, its cross-checks, with `check`, what that one finds, with `run`, a run of them kept with what it found, with `questionSets`, its question sets with the reports of their answers, with `report`, one of those reports, with `reviews`, its documents in review, with `activity`, what was done in it lately, with `drafts`, the drafts the model wrote from its documents, with `draft`, one of them, with `draftTemplates`, how its drafts of each kind read, with `mail`, its address and what it received, with `connectors`, the folders of its members' Google Drive, Dropbox or OneDrive it keeps in step with; with `favorites`, the member's favourites, and with `recent`, the documents they opened lately. The favourites and the recent
documents take `search` too, which then matches their names alone. */
export interface LibraryView {
  folderId: number | null
  search?: WorkspaceSearch
  trash?: true
  templates?: true
  facts?: string
  changes?: string
  checks?: true
  check?: number
  run?: number
  questionSets?: true
  report?: number
  reviews?: Reviews
  activity?: true
  drafts?: true
  draft?: number
  draftTemplates?: true
  mail?: true
  connectors?: true
  favorites?: true
  recent?: true
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
  /** What a new chat will be about, until its first question makes it; null for the whole project. */
  about: ScopeIn | null
  /** False for an old URL shape (/ask, the search tab's /search, a filter by name's `name`) that should be rewritten to the current one. */
  canonical: boolean
}

const PROJECT = /^\/projects\/(\d+)(?:\/(.*))?$/

export function parseWorkspaceUrl(pathname: string, query: string): WorkspaceUrl | null {
  const match = PROJECT.exec(pathname.replace(/\/+$/, ''))
  if (!match) return null
  const projectId = Number(match[1])
  const rest = match[2] ?? ''
  const params = new URLSearchParams(query)
  const chat = params.get('chat')
  let chatId: WorkspaceUrl['chatId'] = chat === 'new' ? 'new' : id(chat)
  const library: LibraryView = { folderId: id(params.get('folder')) }
  // An address from before the search was the library's gives its words as the filter by name's `name`.
  const search = parseSearch(params, params.get('name')?.trim() ?? '')
  if (search) library.search = search
  if (params.has('trash')) library.trash = true
  else if (params.has('templates')) library.templates = true
  else if (params.get('facts')) library.facts = params.get('facts') ?? undefined
  else if (params.get('changes')) library.changes = params.get('changes') ?? undefined
  else if (id(params.get('check')) != null) library.check = id(params.get('check')) ?? undefined
  else if (id(params.get('run')) != null) library.run = id(params.get('run')) ?? undefined
  else if (params.has('checks')) library.checks = true
  else if (id(params.get('report')) != null) library.report = id(params.get('report')) ?? undefined
  else if (params.has('question-sets')) library.questionSets = true
  else if (params.has('reviews')) library.reviews = params.get('reviews') === 'mine' ? 'mine' : 'all'
  else if (params.has('activity')) library.activity = true
  else if (id(params.get('draft')) != null) library.draft = id(params.get('draft')) ?? undefined
  else if (params.has('drafts')) library.drafts = true
  else if (params.has('draft-templates')) library.draftTemplates = true
  else if (params.has('mail')) library.mail = true
  else if (params.has('connectors')) library.connectors = true
  else if (params.has('favorites')) library.favorites = true
  else if (params.has('recent')) library.recent = true

  let document: OpenDocument | null = null
  // The search tab's path and the filter by name's parameter, from before the search was the library's, are rewritten.
  let canonical = !params.has('name')
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
  const about = chatId === 'new' ? parseAbout(params.get('about')) : null
  return { projectId, library, document, chatId, about, canonical }
}

const DAY = /^\d{4}-\d{2}-\d{2}$/

/**
 * The search: its words (`q`, else `named`, an older address's filter by name), and what narrows it, each part a parameter
 * of its own (its folder `in`) and a list's each value once; null when it has neither.
 */
function parseSearch(params: URLSearchParams, named: string): WorkspaceSearch | null {
  const day = (value: string | null) => (value != null && DAY.test(value) ? value : null)
  const filter: DocumentFilter = {
    types: params.getAll('type').filter(Boolean),
    after: day(params.get('after')),
    before: day(params.get('before')),
    reviews: params.getAll('review').filter((value): value is ReviewState => (REVIEW_STATES as string[]).includes(value)),
    folder_id: id(params.get('in')),
    mentions: params.getAll('within').filter((value) => value.trim() !== ''),
  }
  const q = params.get('q')?.trim() || named
  return q || narrows(filter) ? { q, filter } : null
}

function writeSearch(params: URLSearchParams, { q, filter: search }: WorkspaceSearch) {
  if (q) params.set('q', q)
  if (search.folder_id != null) params.set('in', String(search.folder_id))
  for (const type of search.types) params.append('type', type)
  if (search.after) params.set('after', search.after)
  if (search.before) params.set('before', search.before)
  for (const state of search.reviews) params.append('review', state)
  for (const words of search.mentions) params.append('within', words)
}

/** A new chat's scope as the URL writes it: `f12` a folder, `d3-5` documents. */
function parseAbout(value: string | null): ScopeIn | null {
  const folder = /^f(\d+)$/.exec(value ?? '')
  if (folder) return { folder_id: Number(folder[1]) }
  if (value && /^d\d+(-\d+)*$/.test(value)) return { document_ids: value.slice(1).split('-').map(Number) }
  return null
}

function writeAbout(about: ScopeIn): string | null {
  if ('folder_id' in about) return `f${about.folder_id}`
  if ('document_ids' in about && about.document_ids.length) return `d${about.document_ids.join('-')}`
  return null
}

function parseFocus(params: URLSearchParams): Focus | null {
  const passage = id(params.get('passage'))
  if (passage != null) return { passage }
  const block = params.get('block')
  if (block != null && /^\d+$/.test(block)) return { block: Number(block) }
  const page = id(params.get('page'))
  if (page != null) return { page }
  const thread = id(params.get('thread'))
  return thread != null ? { thread } : null
}

function id(value: string | null): number | null {
  return value != null && /^\d+$/.test(value) ? Number(value) : null
}

export function buildWorkspaceUrl(url: Omit<WorkspaceUrl, 'canonical'>): string {
  const params = new URLSearchParams()
  let path = `/projects/${url.projectId}`
  if (url.library.folderId != null) params.set('folder', String(url.library.folderId))
  if (url.library.search) writeSearch(params, url.library.search)
  if (url.library.trash) params.set('trash', '')
  if (url.library.templates) params.set('templates', '')
  if (url.library.facts) params.set('facts', url.library.facts)
  if (url.library.changes) params.set('changes', url.library.changes)
  if (url.library.checks) params.set('checks', '')
  if (url.library.check != null) params.set('check', String(url.library.check))
  if (url.library.run != null) params.set('run', String(url.library.run))
  if (url.library.questionSets) params.set('question-sets', '')
  if (url.library.report != null) params.set('report', String(url.library.report))
  if (url.library.reviews) params.set('reviews', url.library.reviews === 'mine' ? 'mine' : '')
  if (url.library.activity) params.set('activity', '')
  if (url.library.drafts) params.set('drafts', '')
  if (url.library.draft != null) params.set('draft', String(url.library.draft))
  if (url.library.draftTemplates) params.set('draft-templates', '')
  if (url.library.mail) params.set('mail', '')
  if (url.library.connectors) params.set('connectors', '')
  if (url.library.favorites) params.set('favorites', '')
  if (url.library.recent) params.set('recent', '')
  if (url.document) {
    path += `/documents/${url.document.id}`
    const focus = url.document.focus
    if (focus) {
      if ('passage' in focus) params.set('passage', String(focus.passage))
      else if ('block' in focus) params.set('block', String(focus.block))
      else if ('thread' in focus) params.set('thread', String(focus.thread))
      else params.set('page', String(focus.page))
    }
  }
  if (url.chatId != null) params.set('chat', String(url.chatId))
  const about = url.chatId === 'new' && url.about ? writeAbout(url.about) : null
  if (about) params.set('about', about)
  const query = params.toString()
  return query ? `${path}?${query}` : path
}

/**
 * The library's view with `search` in it, its words trimmed, the place it is in kept (a folder, to go back to once it ends;
 * the favourites or the recent documents, whose names it matches); without one once it has no words and nothing narrows it.
 */
export function withSearch(view: LibraryView, search: WorkspaceSearch | null): LibraryView {
  const rest = { ...view }
  delete rest.search
  const q = search?.q.trim() ?? ''
  return search && (q || narrows(search.filter)) ? { ...rest, search: { q, filter: search.filter } } : rest
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
