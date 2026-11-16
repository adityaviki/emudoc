// What the panes share: the project's data, the open tabs, and the ways to move around.

import { createContext, useContext } from 'react'
import type { Document, Folder, ListingPlace, Project, ScopeIn } from '../api.ts'
import type { FolderTree } from '../hooks.ts'
import type { Focus, WorkspaceSearch, WorkspaceUrl } from './url.ts'

export interface OpenTab {
  id: number
  focus?: Focus
  /** Pinned: kept before the others, and left open by "Close others" and "Close all". */
  pinned?: true
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

/**
 * The open tabs with one of them moved to `index` among them, clamped to the ends of its group,
 * the pinned tabs before the rest; the same list when it stays where it was, so that a store given
 * it back is not written again.
 */
export function moved(open: OpenTab[], id: number, index: number): OpenTab[] {
  const from = open.findIndex((tab) => tab.id === id)
  if (from < 0) return open
  const pins = open.filter((tab) => tab.pinned).length
  const [first, last] = open[from].pinned ? [0, pins - 1] : [pins, open.length - 1]
  const to = Math.max(first, Math.min(index, last))
  if (from === to) return open
  const rest = open.filter((tab) => tab.id !== id)
  return [...rest.slice(0, to), open[from], ...rest.slice(to)]
}

/**
 * The open tabs with one of them pinned, after the tabs pinned before it, or unpinned, first of the
 * rest; the same list when it is so already.
 */
export function pinned(open: OpenTab[], id: number, pin: boolean): OpenTab[] {
  const tab = open.find((each) => each.id === id)
  if (!tab || Boolean(tab.pinned) === pin) return open
  const rest = open.filter((each) => each.id !== id)
  const pins = rest.filter((each) => each.pinned).length
  // Left undefined, the key is not written to the store.
  return [...rest.slice(0, pins), { ...tab, pinned: pin || undefined }, ...rest.slice(pins)]
}

/** The tabs "Close others" closes from a tab's menu, or with no tab named, "Close all": none pinned. */
export function closedByMenu(open: OpenTab[], except: number | null = null): number[] {
  return open.filter((tab) => !tab.pinned && tab.id !== except).map((tab) => tab.id)
}

/**
 * Where a tab dropped into a gap between the tabs lands among them: `gap` counts the tabs before
 * the gap, as they stand while the tab is still in its place, so the gaps on either side of it
 * leave it there.
 */
export function droppedAt(from: number, gap: number): number {
  return gap > from ? gap - 1 : gap
}

/** The panes, left to right. The middle one is only there while a document or the search is open in it. */
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
  /** Moves an open document's tab to `index` among the documents' tabs, by a drag, its menu or its keys. */
  moveTab(id: number, index: number): void
  /** Pins an open document's tab, or unpins it. */
  pinTab(id: number, pin: boolean): void
  clearFocus(): void
  /** Shows a folder in the library; the open document stays where it is. */
  goToFolder(folderId: number | null, options?: { replace?: boolean }): void
  /**
   * Shows a search in the library, what narrows it given whole: the files whose names hold its words, then the passages it
   * finds; the place it was run from stays, to go back to once it has no words and nothing narrows it.
   */
  showSearch(search: WorkspaceSearch, options?: { replace?: boolean }): void
  /** Shows the project's trash in the library; going to a folder leaves it. */
  showTrash(): void
  /** Shows the project's extraction templates in the library, likewise. */
  showTemplates(): void
  /** Shows the facts table of the project's documents of a kind in the library, likewise. */
  showFacts(kind: string): void
  /** Shows what the last readings of the project's documents of a kind changed, likewise. */
  showChanges(kind: string): void
  /** Shows the project's cross-checks in the library, likewise. */
  showChecks(): void
  /** Shows what one of the project's cross-checks finds in the library, likewise. */
  showFindings(checkId: number): void
  /** Shows a run of the project's cross-checks, kept with what it found, in the library, likewise. */
  showRun(runId: number): void
  /** Shows the project's address, whose mail becomes its documents, in the library, likewise. */
  showMailbox(): void
  /** Shows the member's own files in the project, their My files, in the library, likewise. */
  showMyFiles(): void
  /** Shows one of the member's own files, with its pages and versions, in the library, likewise. */
  showMyFile(fileId: number): void
  /** Shows how the agent writes each kind of document into My files, the project's writing templates, in the library, likewise. */
  showWritingTemplates(): void
  /** Shows the project's connected folders in the library, likewise. */
  showConnectors(): void
  /** Shows a place of the library that is not a folder (the member's favourites or recent ones), likewise. */
  showPlace(place: Exclude<ListingPlace, { folder: number | null }>): void
  /** Opens a chat on the right, or a new one, about what `about` names until its first question makes it. */
  openChat(chatId: number | 'new' | null, options?: { replace?: boolean; about?: ScopeIn | null }): void
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
