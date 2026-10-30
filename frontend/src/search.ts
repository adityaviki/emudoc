// The search of every project a member sees (backend/app/api/search.py), a page of its own at /search beside the
// projects: its address, what narrows it in words, and where a hit opens.

import type { Project, SearchAllFilter, SearchAllHit } from './api.ts'
import { EMPTY_FILTER, filterParts, listed, smartFolderProblem } from './library.ts'
import { buildWorkspaceUrl } from './workspace/url.ts'

export const SEARCH_PATH = '/search'

export const EMPTY_SEARCH_ALL: SearchAllFilter = { projects: [], types: [], after: null, before: null }

/** The types of every project's documents, which the search's filter offers. */
export const ALL_TYPES_KEY = ['document-types', 'all'] as const

/** The search of every project: its words, and what narrows it. */
export interface SearchAll {
  q: string
  filter: SearchAllFilter
}

const DAY = /^\d{4}-\d{2}-\d{2}$/

/** The search the page's address asks for: `q`, and what narrows it, `project` and `type` once a value, `after` and `before` days. */
export function parseSearchAll(params: URLSearchParams): SearchAll {
  const day = (value: string | null) => (value != null && DAY.test(value) ? value : null)
  const ids = params.getAll('project').filter((value) => /^[1-9]\d*$/.test(value))
  return {
    q: params.get('q')?.trim() ?? '',
    filter: {
      projects: [...new Set(ids.map(Number))],
      types: [...new Set(params.getAll('type').filter((value) => value.trim() !== ''))],
      after: day(params.get('after')),
      before: day(params.get('before')),
    },
  }
}

/** The page's address for a search. */
export function searchAllUrl({ q, filter }: SearchAll): string {
  const params = new URLSearchParams()
  if (q.trim()) params.set('q', q.trim())
  for (const id of filter.projects) params.append('project', String(id))
  for (const type of filter.types) params.append('type', type)
  if (filter.after) params.set('after', filter.after)
  if (filter.before) params.set('before', filter.before)
  const query = params.toString()
  return query ? `${SEARCH_PATH}?${query}` : SEARCH_PATH
}

/** What narrows the search that a member can take off alone. */
export type SearchAllPart = 'projects' | 'types' | 'dates'

/** Each part the filter narrows by in words, its projects by their names as they are now, in the order a sentence lists them. */
export function searchAllParts(filter: SearchAllFilter, projects: Pick<Project, 'id' | 'name'>[]): { part: SearchAllPart; words: string }[] {
  const parts: { part: SearchAllPart; words: string }[] = []
  if (filter.projects.length) {
    const names = filter.projects.map((id) => projects.find((project) => project.id === id)?.name).map((name) => (name ? `“${name}”` : 'a project you do not see'))
    parts.push({ part: 'projects', words: `in ${listed(names, 'or')}` })
  }
  // The types and the dates in the words the library's search says them in.
  for (const { part, words } of filterParts({ ...EMPTY_FILTER, types: filter.types, after: filter.after, before: filter.before }, [], null)) {
    parts.push({ part: part === 'types' ? 'types' : 'dates', words })
  }
  return parts
}

/** The filter without one of its parts. */
export function withoutSearchAllPart(filter: SearchAllFilter, part: SearchAllPart): SearchAllFilter {
  const emptied: Record<SearchAllPart, Partial<SearchAllFilter>> = { projects: { projects: [] }, types: { types: [] }, dates: { after: null, before: null } }
  return { ...filter, ...emptied[part] }
}

/** Whether the filter narrows anything, or takes every project the member sees. */
export const narrowsAll = (filter: SearchAllFilter) => searchAllParts(filter, []).length > 0

/** What a search searches, in words. */
export function searchAllWords(filter: SearchAllFilter, projects: Pick<Project, 'id' | 'name'>[]): string {
  const parts = searchAllParts(filter, projects).map(({ words }) => words)
  return parts.length ? `Documents ${parts.join(', ')}` : 'Every project you see'
}

/** Why the filter as chosen cannot be applied, in the words the server would refuse it with; null when it can. */
export function searchAllProblem(filter: SearchAllFilter): string | null {
  return smartFolderProblem('-', { ...EMPTY_FILTER, after: filter.after, before: filter.before })
}

/** Where a hit opens: its document in its project, at the passage. */
export function hitUrl(hit: Pick<SearchAllHit, 'project_id' | 'document_id' | 'passage_id'>): string {
  return buildWorkspaceUrl({
    projectId: hit.project_id,
    library: { folderId: null, q: '' },
    document: { id: hit.document_id, focus: { passage: hit.passage_id } },
    chatId: null,
    about: null,
  })
}
