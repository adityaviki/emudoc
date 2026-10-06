// What the two panes share: the project's data, the open tabs, and the ways to move around.

import { createContext, useContext } from 'react'
import type { Document, Folder, Project } from '../api.ts'
import type { FolderTree } from '../hooks.ts'
import type { Focus, WorkspaceUrl } from './url.ts'

/** How a document tab shows its file: the file itself, the extracted text, or rendered pages. */
export type DocView = 'file' | 'text' | 'pages'

export interface OpenTab {
  id: number
  focus?: Focus
  view?: DocView
}

export interface TabsStore {
  v: 1
  open: OpenTab[]
}

export const tabsStorageKey = (projectId: number) => `paperless.tabs.${projectId}`
export const SPLIT_KEY = 'paperless.split'

export function isTabsStore(value: unknown): value is TabsStore {
  if (typeof value !== 'object' || value === null) return false
  const store = value as Partial<TabsStore>
  return (
    store.v === 1 &&
    Array.isArray(store.open) &&
    store.open.every((tab) => typeof tab === 'object' && tab !== null && typeof tab.id === 'number')
  )
}

export interface Workspace {
  project: Project
  documents: Document[]
  documentsLoaded: boolean
  folders: Folder[]
  tree: FolderTree
  url: WorkspaceUrl
  openTabs: OpenTab[]
  /** The document in front on the left, or null when the Library is. */
  activeDocumentId: number | null
  openDocument(id: number, focus?: Focus | null, options?: { replace?: boolean }): void
  closeTabs(ids: number[]): void
  setTabView(id: number, view: DocView): void
  clearFocus(): void
  goToLibrary(folderId: number | null, options?: { replace?: boolean }): void
  setSearch(q: string): void
  openChat(chatId: number | 'new' | null, options?: { replace?: boolean }): void
  /** Which pane shows on a narrow screen, where only one fits. */
  mobilePane: 'files' | 'chat'
  setMobilePane(pane: 'files' | 'chat'): void
  /** True while files from the desktop are being dragged over the window. */
  draggingFiles: boolean
}

export const WorkspaceContext = createContext<Workspace | null>(null)

export function useWorkspace(): Workspace {
  const workspace = useContext(WorkspaceContext)
  if (!workspace) throw new Error('useWorkspace must be used inside a Workspace')
  return workspace
}
