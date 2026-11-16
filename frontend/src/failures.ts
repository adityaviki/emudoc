// What a part of the page says once it rendered into an error (components/ErrorBoundary.tsx), and
// when it starts afresh. The whole page has one boundary; inside it the shell's page and each of
// the workspace's panes have their own, so that the header (the help menu, the way to support,
// the workspace's name) and the other panes stay while one part shows this.

import type { LibraryView } from './workspace/url.ts'

/** What broke: the page below the header, or one of the workspace's panes. */
export type Part = 'page' | 'library' | 'document' | 'chat'

export interface FailureWords {
  title: string
  advice: string
  /** Whether to offer to render the part again before reloading the whole page. */
  retry: boolean
}

const TITLES: Record<Part, string> = {
  page: 'This page ran into an error.',
  library: 'The library ran into an error.',
  document: 'This document ran into an error.',
  chat: 'This chat ran into an error.',
}

/** What `part` says in its place. */
export function failureWords(part: Part): FailureWords {
  if (part === 'page') return { title: TITLES.page, advice: 'Reload it to go on.', retry: false }
  return { title: TITLES[part], advice: 'The rest of the page still works: try again, or reload the page.', retry: true }
}

/**
 * Where the library is, as a boundary's reset key: a folder, a search, the trash, the templates or a facts table opened since
 * the library broke is tried afresh. A string, since a boundary compares its key with `!==` and a
 * new object at each render would start a broken part again at every one.
 */
export function libraryPlace(view: LibraryView): string {
  if (view.facts) return `facts:${view.facts}`
  if (view.changes) return `changes:${view.changes}`
  const place = view.favorites ? 'favorites' : view.recent ? 'recent' : (view.folderId ?? 'root')
  return view.trash ? 'trash' : view.templates ? 'templates' : `${place}?${view.search ? JSON.stringify(view.search) : ''}`
}
