// What the panes share: the project's data, the open tabs, and the ways to move around.

import { createContext, useContext } from 'react'
import type { Document, Folder, ListingPlace, Project, ScopeIn } from '../api.ts'
import type { FolderTree } from '../hooks.ts'
import type { Focus, Reviews, WorkspaceSearch, WorkspaceUrl } from './url.ts'

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
  clearFocus(): void
  /** Shows a folder in the library; the open document stays where it is. */
  goToFolder(folderId: number | null, options?: { replace?: boolean }): void
  /** Narrows what the library shows to the files whose names hold these letters, every folder's from a folder; '' for all. */
  filterByName(name: string): void
  /** Runs a search in the search tab, what narrows it given whole, and brings the tab to the front. */
  showSearch(search: WorkspaceSearch, options?: { replace?: boolean }): void
  /** Closes the search tab, the next document's tab taking its place when it was the one shown. */
  closeSearch(): void
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
  /** Shows the project's question sets, with the reports of their answers, in the library, likewise. */
  showQuestionSets(): void
  /** Shows one report of a question set's answers in the library, likewise. */
  showReport(reportId: number): void
  /** Shows the project's documents in review in the library, every member's or those assigned to the member, likewise. */
  showReviews(whose?: Reviews): void
  /** Shows what was done in the project lately in the library, likewise. */
  showActivity(): void
  /** Shows the drafts the model wrote from the project's documents in the library, likewise. */
  showDrafts(): void
  /** Shows one of the project's drafts, with its versions, in the library, likewise. */
  showDraft(draftId: number): void
  /** Shows how the project's drafts of each kind read in the library, likewise. */
  showDraftTemplates(): void
  /** Shows the project's address, whose mail becomes its documents, in the library, likewise. */
  showMailbox(): void
  /** Shows the project's connected folders in the library, likewise. */
  showConnectors(): void
  /** Shows a place of the library that is not a folder (a tag's documents, the member's favourites or recent ones), likewise. */
  showPlace(place: Exclude<ListingPlace, { folder: number | null }>): void
  /** Shows the project's tags to make and change in the library, likewise. */
  showTags(): void
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
