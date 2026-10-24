import { useQueries, useQuery, useQueryClient } from '@tanstack/react-query'
import type { UseQueryResult } from '@tanstack/react-query'
import { FileText, Library as LibraryIcon, MessageSquare } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router'
import { api, auth } from '../api.ts'
import type { Document } from '../api.ts'
import { setSignedIn, useMe } from '../auth.ts'
import { ErrorBoundary } from '../components/ErrorBoundary.tsx'
import { Tour } from '../components/Tour.tsx'
import { Loading, Notice, Segmented } from '../components/ui.tsx'
import { libraryPlace } from '../failures.ts'
import { useFolderTree, useFolders, useLocalStorage } from '../hooks.ts'
import { offerTour, workspaceTour } from '../tour.ts'
import { ChatPane } from '../workspace/ChatPane.tsx'
import { DocumentTab } from '../workspace/DocumentTab.tsx'
import { Library } from '../workspace/Library.tsx'
import { documentQuery, isGone } from '../workspace/listing.ts'
import { SplitPanes } from '../workspace/SplitPane.tsx'
import type { Pane as SplitPane } from '../workspace/SplitPane.tsx'
import { DOCUMENT_PANEL, TAB_ID, TabStrip } from '../workspace/TabStrip.tsx'
import { SPLIT_KEY, WorkspaceContext, isTabsStore, tabsStorageKey, useWorkspace } from '../workspace/state.ts'
import type { OpenTab, Pane, TabsStore, Workspace as WorkspaceState } from '../workspace/state.ts'
import { buildWorkspaceUrl, sameFocus, useWorkspaceUrl } from '../workspace/url.ts'
import type { Go, WorkspaceUrl } from '../workspace/url.ts'

const EMPTY_TABS: TabsStore = { v: 1, open: [] }

/**
 * A project's panes: the library on the left, the agent on the right, and while a document is
 * open, the document between them.
 */
export function Workspace() {
  const [url, go] = useWorkspaceUrl()
  const navigate = useNavigate()

  // Old URL shapes (/ask, /search) are rewritten to the current one.
  useEffect(() => {
    if (url && !url.canonical) navigate(buildWorkspaceUrl(url), { replace: true })
  }, [url, navigate])

  if (!url) return <p className="py-16 text-center text-muted">Page not found.</p>
  // Each project keeps its own tabs; a fresh mount on switching keeps them apart.
  return <ProjectWorkspace key={url.projectId} url={url} go={go} />
}

function ProjectWorkspace({ url, go }: { url: WorkspaceUrl; go: Go }) {
  const projectId = url.projectId
  const queryClient = useQueryClient()
  const project = useQuery({ queryKey: ['project', projectId], queryFn: () => api.project(projectId) })
  const folders = useFolders(projectId)
  const tree = useFolderTree(folders.data)
  const [tabs, setTabs] = useLocalStorage(tabsStorageKey(projectId), EMPTY_TABS, isTabsStore)
  const [mobilePane, setMobilePane] = useState<Pane>('library')
  const draggingFiles = useFilesDragging()
  const tour = useWorkspaceTour(project.data?.sample ?? false)

  const activeDocumentId = url.document?.id ?? null
  const activeFocus = url.document?.focus ?? null

  // The URL is the active tab: make sure it is in the open list, and remember where it points
  // so that coming back to it restores the highlight.
  useEffect(() => {
    if (activeDocumentId == null) return
    setTabs((store) => {
      const entry = store.open.find((tab) => tab.id === activeDocumentId)
      if (entry && sameFocus(entry.focus, activeFocus)) return store
      const updated: OpenTab = { ...entry, id: activeDocumentId, focus: activeFocus ?? undefined }
      return {
        v: 1,
        open: entry ? store.open.map((tab) => (tab.id === activeDocumentId ? updated : tab)) : [...store.open, updated],
      }
    })
  }, [activeDocumentId, activeFocus, setTabs])

  // Each tab's document is asked for by itself; the library holds only the pages it shows.
  const tabDocuments = useQueries({
    queries: tabs.open.map((tab) => documentQuery(queryClient, projectId, tab.id)),
    combine: tabsCame,
  })
  const openDocuments = useMemo(
    () => new Map(tabDocuments.documents.map((document) => [document.id, document])),
    [tabDocuments.documents],
  )

  // Tabs whose documents are gone (deleted here or elsewhere) are closed.
  const goneIds = tabs.open.filter((_, index) => tabDocuments.gone[index]).map((tab) => tab.id).join()
  useEffect(() => {
    if (!goneIds) return
    const closing = new Set(goneIds.split(',').map(Number))
    setTabs((store) => ({ v: 1, open: store.open.filter((tab) => !closing.has(tab.id)) }))
    if (activeDocumentId != null && closing.has(activeDocumentId)) go({ document: null }, { replace: true })
  }, [goneIds, activeDocumentId, setTabs, go])

  const goToFolder = useCallback<WorkspaceState['goToFolder']>(
    (folderId, options) => go({ library: { folderId, q: '' } }, options),
    [go],
  )

  const openTabs = tabs.open
  const workspace = useMemo<WorkspaceState | null>(() => {
    if (!project.data) return null
    return {
      project: project.data,
      openDocuments,
      folders: folders.data ?? [],
      tree,
      url,
      openTabs,
      activeDocumentId,
      openDocument(id, focus = null, options) {
        setTabs((store) =>
          store.open.some((tab) => tab.id === id) ? store : { v: 1, open: [...store.open, { id }] },
        )
        setMobilePane('document')
        go({ document: { id, focus } }, options)
      },
      closeTabs(ids) {
        const closing = new Set(ids)
        if (activeDocumentId != null && closing.has(activeDocumentId)) {
          // Move to the nearest tab that stays open: the one to the right, else to the left.
          const index = openTabs.findIndex((tab) => tab.id === activeDocumentId)
          const remaining = openTabs.filter((tab) => !closing.has(tab.id))
          const next = remaining.find((tab) => openTabs.indexOf(tab) > index) ?? remaining.at(-1)
          go({ document: next ? { id: next.id, focus: next.focus ?? null } : null }, { replace: true })
        }
        setTabs((store) => ({ v: 1, open: store.open.filter((tab) => !closing.has(tab.id)) }))
      },
      clearFocus() {
        if (activeDocumentId != null) go({ document: { id: activeDocumentId, focus: null } }, { replace: true })
      },
      goToFolder,
      setSearch(q, folderId = url.library.folderId) {
        go({ library: { folderId, q } }, { replace: true })
      },
      showTrash() {
        setMobilePane('library')
        go({ library: { folderId: null, q: '', trash: true } })
      },
      showTemplates() {
        setMobilePane('library')
        go({ library: { folderId: null, q: '', templates: true } })
      },
      showFacts(kind) {
        setMobilePane('library')
        go({ library: { folderId: null, q: '', facts: kind } })
      },
      showChanges(kind) {
        setMobilePane('library')
        go({ library: { folderId: null, q: '', changes: kind } })
      },
      showChecks() {
        setMobilePane('library')
        go({ library: { folderId: null, q: '', checks: true } })
      },
      showFindings(checkId) {
        setMobilePane('library')
        go({ library: { folderId: null, q: '', check: checkId } })
      },
      showRun(runId) {
        setMobilePane('library')
        go({ library: { folderId: null, q: '', run: runId } })
      },
      showReviews(whose = 'all') {
        setMobilePane('library')
        go({ library: { folderId: null, q: '', reviews: whose } })
      },
      showDrafts() {
        setMobilePane('library')
        go({ library: { folderId: null, q: '', drafts: true } })
      },
      showDraft(draftId) {
        setMobilePane('library')
        go({ library: { folderId: null, q: '', draft: draftId } })
      },
      showDraftTemplates() {
        setMobilePane('library')
        go({ library: { folderId: null, q: '', draftTemplates: true } })
      },
      showMailbox() {
        setMobilePane('library')
        go({ library: { folderId: null, q: '', mail: true } })
      },
      showConnectors() {
        setMobilePane('library')
        go({ library: { folderId: null, q: '', connectors: true } })
      },
      openChat(chatId, options) {
        if (chatId === 'new') setMobilePane('chat')
        go({ chatId }, options)
      },
      mobilePane,
      setMobilePane,
      draggingFiles,
    }
  }, [project.data, openDocuments, folders.data, tree, url, openTabs, activeDocumentId, mobilePane, draggingFiles, go, goToFolder, setTabs])

  if (project.isPending) return <Loading />
  if (project.isError || !workspace) {
    return (
      <div className="p-6">
        <Notice tone="bad">
          {project.error?.message ?? 'Project not found'}.{' '}
          <Link to="/" className="underline">
            Back to projects
          </Link>
        </Notice>
      </div>
    )
  }

  const hasDocument = activeDocumentId != null
  // The document pane goes away with its last tab; a narrow screen then shows the library.
  const shown: Pane = mobilePane === 'document' && !hasDocument ? 'library' : mobilePane
  // Beside a document the library is a sidebar; alone with the agent it is the main view.
  // Each pane breaks alone, and starts afresh once it is moved elsewhere (src/failures.ts).
  const panes: SplitPane[] = [
    {
      key: 'library',
      label: 'Library',
      node: (
        <ErrorBoundary part="library" resetKey={libraryPlace(url.library)}>
          <Library />
        </ErrorBoundary>
      ),
      share: hasDocument ? 25 : 55,
      min: 240,
    },
    ...(hasDocument
      ? [
          {
            key: 'document',
            label: 'Document',
            node: (
              <ErrorBoundary part="document" resetKey={activeDocumentId}>
                <DocumentPane documentId={activeDocumentId} />
              </ErrorBoundary>
            ),
            share: 45,
            min: 360,
          },
        ]
      : []),
    {
      key: 'chat',
      label: 'Chat',
      // What the tour points at, there while the chats load too.
      node: (
        <div data-tour="chat" className="h-full">
          <ErrorBoundary part="chat" resetKey={url.chatId}>
            <ChatPane />
          </ErrorBoundary>
        </div>
      ),
      share: hasDocument ? 30 : 45,
      min: 320,
    },
  ]

  return (
    <WorkspaceContext value={workspace}>
      <div className="flex h-full min-h-0 flex-col">
        {/* The header's switcher shows the project's name; a screen reader finds it as the page's heading. */}
        <h1 className="sr-only">{workspace.project.name}</h1>
        {/* Where the chat is on a narrow screen, for the tour, which the hidden pane cannot show. */}
        <div data-tour="chat" className="flex justify-center border-b border-line bg-surface px-3 py-1.5 lg:hidden">
          <Segmented
            value={shown}
            onChange={setMobilePane}
            options={[
              { value: 'library', label: 'Library', icon: LibraryIcon },
              ...(hasDocument ? [{ value: 'document' as const, label: 'Document', icon: FileText }] : []),
              { value: 'chat', label: 'Chat', icon: MessageSquare },
            ]}
          />
        </div>
        <div className="min-h-0 flex-1">
          <SplitPanes storageKey={SPLIT_KEY} shown={shown} panes={panes} />
        </div>
      </div>
      {tour.steps && <Tour steps={tour.steps} onEnd={tour.end} />}
    </WorkspaceContext>
  )
}

/**
 * The tour of the workspace: shown by itself the first time the member opens a project, and
 * again whenever the user menu's "Take the tour" asks. Its end, however reached, says it was
 * seen, for every organisation; should saying so fail, it shows again in the next project.
 */
function useWorkspaceTour(sample: boolean) {
  const me = useMe()
  const queryClient = useQueryClient()
  const [touring, setTouring] = useState(!me.tour_seen)
  useEffect(() => {
    return offerTour(() => setTouring(true))
  }, [])
  const seen = me.tour_seen
  const end = useCallback(() => {
    setTouring(false)
    if (!seen) {
      void auth.chooseOnboarding({ tour_seen: true }).then(
        (answer) => setSignedIn(queryClient, answer),
        () => {},
      )
    }
  }, [seen, queryClient])
  const steps = useMemo(() => (touring ? workspaceTour(sample) : null), [touring, sample])
  return { steps, end }
}

/**
 * The tabs' documents that have come, and for each tab whether its document is gone. Plain
 * arrays, which the queries' combined result keeps as they were while nothing in them changes.
 */
function tabsCame(results: UseQueryResult<Document>[]) {
  return {
    documents: results.flatMap((result) => (result.data ? [result.data] : [])),
    gone: results.map((result) => isGone(result.error)),
  }
}

/** The middle pane: the tab strip with the active document below it. */
function DocumentPane({ documentId }: { documentId: number }) {
  const workspace = useWorkspace()
  const queryClient = useQueryClient()
  const document = useQuery(documentQuery(queryClient, workspace.project.id, documentId))

  return (
    <div className="flex h-full min-h-0 flex-col bg-page">
      <TabStrip />
      <div id={DOCUMENT_PANEL} role="tabpanel" aria-labelledby={`${TAB_ID}${documentId}`} className="min-h-0 flex-1">
        {/* A document that breaks leaves the tabs, from which another opens. */}
        <ErrorBoundary part="document" resetKey={documentId}>
          {document.data ? (
            <DocumentTab
              key={document.data.id}
              document={document.data}
              focus={workspace.url.document?.focus ?? null}
              onClearFocus={workspace.clearFocus}
            />
          ) : document.isError ? (
            <div className="p-4">
              <Notice tone="bad">{isGone(document.error) ? 'This document no longer exists.' : document.error.message}</Notice>
            </div>
          ) : (
            <Loading />
          )}
        </ErrorBoundary>
      </div>
    </div>
  )
}

/**
 * Whether files from outside the browser are being dragged over the window. Also stops the
 * browser from opening a file that is dropped where nothing accepts it.
 */
function useFilesDragging(): boolean {
  const [dragging, setDragging] = useState(false)
  const timer = useRef<number | undefined>(undefined)

  useEffect(() => {
    function over(event: DragEvent) {
      if (!event.dataTransfer?.types.includes('Files')) return
      event.preventDefault()
      setDragging(true)
      // No event says "left the window", so the flag lapses when dragover stops arriving.
      window.clearTimeout(timer.current)
      timer.current = window.setTimeout(() => setDragging(false), 150)
    }
    function end(event: DragEvent) {
      if (event.type === 'drop') event.preventDefault()
      window.clearTimeout(timer.current)
      setDragging(false)
    }
    window.addEventListener('dragover', over)
    window.addEventListener('drop', end)
    window.addEventListener('dragend', end)
    return () => {
      window.removeEventListener('dragover', over)
      window.removeEventListener('drop', end)
      window.removeEventListener('dragend', end)
      window.clearTimeout(timer.current)
    }
  }, [])
  return dragging
}
