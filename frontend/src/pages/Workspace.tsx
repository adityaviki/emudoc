import { useQueries, useQuery, useQueryClient } from '@tanstack/react-query'
import type { UseQueryResult } from '@tanstack/react-query'
import { FileText, Library as LibraryIcon, MessageSquare } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router'
import { api, auth } from '../api.ts'
import type { Document, MyFileDetail } from '../api.ts'
import { setSignedIn, useMe } from '../auth.ts'
import { ErrorBoundary } from '../components/ErrorBoundary.tsx'
import { Tour } from '../components/Tour.tsx'
import { Loading, Notice, Segmented } from '../components/ui.tsx'
import { libraryPlace } from '../failures.ts'
import { useFolderTree, useFolders, useLocalStorage } from '../hooks.ts'
import { myFileQuery } from '../myFiles.ts'
import { offerTour, workspaceTour } from '../tour.ts'
import { ChatPane } from '../workspace/ChatPane.tsx'
import { DocumentTab } from '../workspace/DocumentTab.tsx'
import { FilesTabs } from '../workspace/FilesTabs.tsx'
import { Library } from '../workspace/Library.tsx'
import { documentQuery, isGone, placeListingsKey } from '../workspace/listing.ts'
import { MyFileView } from '../workspace/MyFiles.tsx'
import { WorkspaceShortcuts } from '../workspace/Shortcuts.tsx'
import { SplitPanes } from '../workspace/SplitPane.tsx'
import type { Pane as SplitPane } from '../workspace/SplitPane.tsx'
import { DOCUMENT_PANEL, TAB_ID, TabStrip } from '../workspace/TabStrip.tsx'
import { SPLIT_KEY, WorkspaceContext, documentTab, isTabsStore, moved, myFileTab, pinned, shownAfter, tabKey, tabsStorageKey, useWorkspace } from '../workspace/state.ts'
import type { OpenTab, Pane, TabKey, TabsStore, Workspace as WorkspaceState } from '../workspace/state.ts'
import { buildWorkspaceUrl, sameFocus, useWorkspaceUrl, withSearch } from '../workspace/url.ts'
import type { Go, WorkspaceUrl } from '../workspace/url.ts'

const EMPTY_TABS: TabsStore = { v: 1, open: [] }

/**
 * A project's panes: the library on the left, with the project's search, the agent on the right, and while a document or a
 * file of My files is open, their tabs between them.
 */
export function Workspace() {
  const [url, go] = useWorkspaceUrl()
  const navigate = useNavigate()

  // Old URL shapes (/ask, the search tab's /search, a filter by name) are rewritten to the current one.
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
  const activeMyFileId = url.document ? null : url.myFile
  const activeTab = activeDocumentId != null ? documentTab(activeDocumentId) : activeMyFileId != null ? myFileTab(activeMyFileId) : null

  // The URL is the active tab: make sure it is in the open list, and remember where a document's
  // points so that coming back to it restores the highlight.
  useEffect(() => {
    if (activeTab == null) return
    setTabs((store) => {
      const entry = store.open.find((tab) => tabKey(tab) === activeTab)
      if (entry && (entry.myFile || sameFocus(entry.focus, activeFocus))) return store
      const updated: OpenTab =
        activeDocumentId != null ? { ...entry, id: activeDocumentId, focus: activeFocus ?? undefined } : { ...entry, id: activeMyFileId!, myFile: true }
      return {
        v: 1,
        open: entry ? store.open.map((tab) => (tabKey(tab) === activeTab ? updated : tab)) : [...store.open, updated],
      }
    })
  }, [activeTab, activeDocumentId, activeMyFileId, activeFocus, setTabs])

  // A document opened in the middle pane is one of the member's recent ones (backend/app/recents.py);
  // the list shows it next time it is asked for, and a visit that does not reach the server is let go.
  useEffect(() => {
    if (activeDocumentId == null) return
    api.visitDocument(activeDocumentId).then(
      () => queryClient.invalidateQueries({ queryKey: placeListingsKey(projectId, { recent: true }) }),
      () => {},
    )
  }, [activeDocumentId, projectId, queryClient])

  // Each tab's document, or file of My files, is asked for by itself; the library holds only the pages it shows.
  const documentTabs = useMemo(() => tabs.open.filter((tab) => !tab.myFile), [tabs.open])
  const myFileTabs = useMemo(() => tabs.open.filter((tab) => tab.myFile), [tabs.open])
  const tabDocuments = useQueries({
    queries: documentTabs.map((tab) => documentQuery(queryClient, projectId, tab.id)),
    combine: tabsCame<Document>,
  })
  const tabMyFiles = useQueries({
    queries: myFileTabs.map((tab) => myFileQuery(tab.id)),
    combine: tabsCame<MyFileDetail>,
  })
  const openDocuments = useMemo(() => new Map(tabDocuments.came.map((document) => [document.id, document])), [tabDocuments.came])
  const openMyFiles = useMemo(() => new Map(tabMyFiles.came.map((file) => [file.id, file])), [tabMyFiles.came])

  // Tabs whose documents or files are gone (deleted here or elsewhere) are closed.
  const goneKeys = [
    ...documentTabs.filter((_, index) => tabDocuments.gone[index]),
    ...myFileTabs.filter((_, index) => tabMyFiles.gone[index]),
  ]
    .map(tabKey)
    .join()
  useEffect(() => {
    if (!goneKeys) return
    const closing = new Set(goneKeys.split(','))
    setTabs((store) => ({ v: 1, open: store.open.filter((tab) => !closing.has(tabKey(tab))) }))
    if (activeTab != null && closing.has(activeTab)) go({ document: null, myFile: null }, { replace: true })
  }, [goneKeys, activeTab, setTabs, go])

  const goToFolder = useCallback<WorkspaceState['goToFolder']>(
    (folderId, options) => go({ library: { folderId } }, options),
    [go],
  )

  const openTabs = tabs.open
  const workspace = useMemo<WorkspaceState | null>(() => {
    if (!project.data) return null
    return {
      project: project.data,
      openDocuments,
      openMyFiles,
      folders: folders.data ?? [],
      tree,
      url,
      openTabs,
      activeTab,
      openDocument(id, focus = null, options) {
        setTabs((store) =>
          store.open.some((tab) => !tab.myFile && tab.id === id) ? store : { v: 1, open: [...store.open, { id }] },
        )
        setMobilePane('document')
        go({ document: { id, focus }, myFile: null }, options)
      },
      openMyFile(id, options) {
        setTabs((store) =>
          store.open.some((tab) => tab.myFile && tab.id === id) ? store : { v: 1, open: [...store.open, { id, myFile: true }] },
        )
        setMobilePane('document')
        go({ document: null, myFile: id }, options)
      },
      closeTabs(keys) {
        const closing = new Set(keys)
        if (activeTab != null && closing.has(activeTab)) go(shownIn(shownAfter(openTabs, activeTab, closing)), { replace: true })
        setTabs((store) => ({ v: 1, open: store.open.filter((tab) => !closing.has(tabKey(tab))) }))
      },
      moveTab(key, index) {
        setTabs((store) => {
          const open = moved(store.open, key, index)
          return open === store.open ? store : { v: 1, open }
        })
      },
      pinTab(key, pin) {
        setTabs((store) => {
          const open = pinned(store.open, key, pin)
          return open === store.open ? store : { v: 1, open }
        })
      },
      clearFocus() {
        if (activeDocumentId != null) go({ document: { id: activeDocumentId, focus: null } }, { replace: true })
      },
      goToFolder,
      showSearch(search, options) {
        setMobilePane('library')
        go({ library: withSearch(url.library, search) }, options)
      },
      showTrash() {
        setMobilePane('library')
        go({ library: { folderId: null, trash: true } })
      },
      showMailbox() {
        setMobilePane('library')
        go({ library: { folderId: null, mail: true } })
      },
      showConnectors() {
        setMobilePane('library')
        go({ library: { folderId: null, connectors: true } })
      },
      showMyFiles() {
        setMobilePane('library')
        go({ library: { folderId: null, myFiles: true } })
      },
      showPlace(place) {
        setMobilePane('library')
        go({ library: { folderId: null, ...place } })
      },
      openChat(chatId, options) {
        // Opened from the library too, as a chat about what was chosen there.
        if (chatId != null) setMobilePane('chat')
        go({ chatId, about: options?.about ?? null }, { replace: options?.replace })
      },
      mobilePane,
      setMobilePane,
      draggingFiles,
    }
  }, [project.data, openDocuments, openMyFiles, folders.data, tree, url, openTabs, activeTab, activeDocumentId, mobilePane, draggingFiles, go, goToFolder, setTabs])

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

  // The middle pane holds the tabs of the documents and the files of My files; it goes away with the last of them, and a
  // narrow screen then shows the library.
  const hasMiddle = activeTab != null
  const shown: Pane = mobilePane === 'document' && !hasMiddle ? 'library' : mobilePane
  // Beside a document the library is a sidebar; alone with the agent it is the main view.
  // Each pane breaks alone, and starts afresh once it is moved elsewhere (src/failures.ts).
  const panes: SplitPane[] = [
    {
      key: 'library',
      label: 'Library',
      node: (
        // The project's files and the member's own, a tab each: a side that breaks leaves the tabs to the other.
        <FilesTabs>
          <ErrorBoundary part="library" resetKey={libraryPlace(url.library)}>
            <Library />
          </ErrorBoundary>
        </FilesTabs>
      ),
      share: hasMiddle ? 25 : 55,
      min: 240,
    },
    ...(hasMiddle
      ? [
          {
            key: 'document',
            label: 'Document',
            node: (
              <ErrorBoundary part="document" resetKey={activeTab}>
                <MiddlePane />
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
      share: hasMiddle ? 30 : 45,
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
              ...(hasMiddle ? [{ value: 'document' as const, label: 'Document', icon: FileText }] : []),
              { value: 'chat', label: 'Chat', icon: MessageSquare },
            ]}
          />
        </div>
        <div className="min-h-0 flex-1">
          <SplitPanes storageKey={SPLIT_KEY} shown={shown} panes={panes} />
        </div>
      </div>
      {tour.steps && <Tour steps={tour.steps} onEnd={tour.end} />}
      <WorkspaceShortcuts />
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

/** Where the URL goes to show an open tab, or to show none. */
function shownIn(tab: OpenTab | undefined): Parameters<Go>[0] {
  if (!tab) return { document: null, myFile: null }
  return tab.myFile ? { document: null, myFile: tab.id } : { document: { id: tab.id, focus: tab.focus ?? null }, myFile: null }
}

interface Came<T> {
  came: T[]
  gone: boolean[]
}

/**
 * The tabs' documents (or files) that have come, and for each tab whether its document is gone.
 * Plain arrays, which the queries' combined result keeps as they were while nothing in them changes.
 */
function tabsCame<T>(results: UseQueryResult<T>[]): Came<T> {
  return {
    came: results.flatMap((result) => (result.data ? [result.data] : [])),
    gone: results.map((result) => isGone(result.error)),
  }
}

/** The middle pane: the tab strip with the active document, or file of My files, below it. */
function MiddlePane() {
  const { url } = useWorkspace()
  return (
    <div className="flex h-full min-h-0 flex-col bg-page">
      <TabStrip />
      {url.document ? <DocumentPanel documentId={url.document.id} /> : url.myFile != null ? <MyFilePanel fileId={url.myFile} /> : null}
    </div>
  )
}

/** The active file of My files below the tabs. */
function MyFilePanel({ fileId }: { fileId: number }) {
  const key: TabKey = myFileTab(fileId)
  return (
    <div id={DOCUMENT_PANEL} role="tabpanel" aria-labelledby={`${TAB_ID}${key}`} className="min-h-0 flex-1">
      {/* A file that breaks leaves the tabs, from which another opens. */}
      <ErrorBoundary part="document" resetKey={key}>
        <MyFileView key={fileId} fileId={fileId} />
      </ErrorBoundary>
    </div>
  )
}

/** The active document below the tabs. */
function DocumentPanel({ documentId }: { documentId: number }) {
  const workspace = useWorkspace()
  const queryClient = useQueryClient()
  const document = useQuery(documentQuery(queryClient, workspace.project.id, documentId))

  return (
    <>
      <div id={DOCUMENT_PANEL} role="tabpanel" aria-labelledby={`${TAB_ID}${documentTab(documentId)}`} className="min-h-0 flex-1">
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
    </>
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
