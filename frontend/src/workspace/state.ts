// What the panes share: the project's data, the open tabs, and the ways to move around.

import { createContext, useContext } from 'react'
import type { Document, Folder, Project } from '../api.ts'
import type { FolderTree } from '../hooks.ts'
import type { Focus, WorkspaceUrl } from './url.ts'

export interface OpenTab {
  id: number
  focus?: Focus
}

export interface TabsStore {
  v: 1
  open: OpenTab[]
}

export const tabsStorageKey = (projectId: number) => `emudoc.tabs.${projectId}`
export const SPLIT_KEY = 'emudoc.split'

export function isTabsStore(value: unknown): value is TabsStore {
  if (typeof value !== 'object' || value === null) return false
  const store = value as Partial<TabsStore>
  return (
    store.v === 1 &&
    Array.isArray(store.open) &&
    store.open.every((tab) => typeof tab === 'object' && tab !== null && typeof tab.id === 'number')
  )
}

/** The panes, left to right. The document pane is only there while a document is open. */
export type Pane = 'library' | 'document' | 'chat'

export interface Workspace {
  project: Project
  /** The documents of the open tabs that have come, by id. */
  openDocuments: Map<number, Document>
  folders: Folder[]
  tree: FolderTree
  url: WorkspaceUrl
  openTabs: OpenTab[]
  /** The document open in the middle pane, or null when there is none. */
  activeDocumentId: number | null
  openDocument(id: number, focus?: Focus | null, options?: { replace?: boolean }): void
  closeTabs(ids: number[]): void
  clearFocus(): void
  /** Shows a folder in the library; the open document stays where it is. */
  goToFolder(folderId: number | null, options?: { replace?: boolean }): void
  /** Searches the folder shown, or the folder given (null for the whole project). */
  setSearch(q: string, folderId?: number | null): void
  openChat(chatId: number | 'new' | null, options?: { replace?: boolean }): void
  /** Which pane shows on a narrow screen, where only one fits. */
  mobilePane: Pane
  setMobilePane(pane: Pane): void
  /** True while files from the desktop are being dragged over the window. */
  draggingFiles: boolean
}

export const WorkspaceContext = createContext<Workspace | null>(null)

export function useWorkspace(): Workspace {
  const workspace = useContext(WorkspaceContext)
  if (!workspace) throw new Error('useWorkspace must be used inside a Workspace')
  return workspace
}
