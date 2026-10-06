import { useQuery } from '@tanstack/react-query'
import { Files, MessageSquare } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router'
import { api, isPdf } from '../api.ts'
import type { Document } from '../api.ts'
import { Loading, Notice, Segmented } from '../components/ui.tsx'
import { useDocuments, useFolderTree, useFolders, useLocalStorage } from '../hooks.ts'
import { ChatPane } from '../workspace/ChatPane.tsx'
import { DocumentTab } from '../workspace/DocumentTab.tsx'
import { Library } from '../workspace/Library.tsx'
import { SplitPane } from '../workspace/SplitPane.tsx'
import { TabStrip } from '../workspace/TabStrip.tsx'
import { SPLIT_KEY, WorkspaceContext, isTabsStore, tabsStorageKey, useWorkspace } from '../workspace/state.ts'
import type { DocView, OpenTab, TabsStore, Workspace as WorkspaceState } from '../workspace/state.ts'
import { buildWorkspaceUrl, sameFocus, useWorkspaceUrl } from '../workspace/url.ts'
import type { Focus, Go, WorkspaceUrl } from '../workspace/url.ts'

const EMPTY_TABS: TabsStore = { v: 1, open: [] }

/** A project's two panes: the library with its open files on the left, the agent on the right. */
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
  const project = useQuery({ queryKey: ['project', projectId], queryFn: () => api.project(projectId) })
  const documents = useDocuments(projectId)
  const folders = useFolders(projectId)
  const tree = useFolderTree(folders.data)
  const [tabs, setTabs] = useLocalStorage(tabsStorageKey(projectId), EMPTY_TABS, isTabsStore)
  const [mobilePane, setMobilePane] = useState<'files' | 'chat'>('files')
  const draggingFiles = useFilesDragging()

  const activeDocumentId = url.tab.kind === 'document' ? url.tab.documentId : null
  const activeFocus = url.tab.kind === 'document' ? url.tab.focus : null

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

  const goToLibrary = useCallback<WorkspaceState['goToLibrary']>(
    (folderId, options) => go({ tab: { kind: 'library', folderId, q: '' } }, options),
    [go],
  )

  // Tabs whose documents are gone (deleted here or elsewhere) are closed.
  const known = documents.data
  useEffect(() => {
    if (!known) return
    const ids = new Set(known.map((document) => document.id))
    if (tabs.open.every((tab) => ids.has(tab.id))) return
    setTabs((store) => ({ v: 1, open: store.open.filter((tab) => ids.has(tab.id)) }))
    if (activeDocumentId != null && !ids.has(activeDocumentId)) goToLibrary(null, { replace: true })
  }, [known, tabs.open, activeDocumentId, setTabs, goToLibrary])

  const openTabs = tabs.open
  const workspace = useMemo<WorkspaceState | null>(() => {
    if (!project.data) return null
    return {
      project: project.data,
      documents: documents.data ?? [],
      documentsLoaded: documents.isSuccess,
      folders: folders.data ?? [],
      tree,
      url,
      openTabs,
      activeDocumentId,
      openDocument(id, focus = null, options) {
        setTabs((store) =>
          store.open.some((tab) => tab.id === id) ? store : { v: 1, open: [...store.open, { id }] },
        )
        setMobilePane('files')
        go({ tab: { kind: 'document', documentId: id, focus } }, options)
      },
      closeTabs(ids) {
        const closing = new Set(ids)
        if (activeDocumentId != null && closing.has(activeDocumentId)) {
          // Move to the nearest tab that stays open: the one to the right, else to the left.
          const index = openTabs.findIndex((tab) => tab.id === activeDocumentId)
          const remaining = openTabs.filter((tab) => !closing.has(tab.id))
          const next = remaining.find((tab) => openTabs.indexOf(tab) > index) ?? remaining.at(-1)
          if (next) go({ tab: { kind: 'document', documentId: next.id, focus: next.focus ?? null } }, { replace: true })
          else goToLibrary(null, { replace: true })
        }
        setTabs((store) => ({ v: 1, open: store.open.filter((tab) => !closing.has(tab.id)) }))
      },
      setTabView(id, view) {
        setTabs((store) => ({ v: 1, open: store.open.map((tab) => (tab.id === id ? { ...tab, view } : tab)) }))
      },
      clearFocus() {
        if (activeDocumentId != null) {
          go({ tab: { kind: 'document', documentId: activeDocumentId, focus: null } }, { replace: true })
        }
      },
      goToLibrary,
      setSearch(q) {
        const folderId = url.tab.kind === 'library' ? url.tab.folderId : null
        go({ tab: { kind: 'library', folderId, q } }, { replace: true })
      },
      openChat(chatId, options) {
        if (chatId === 'new') setMobilePane('chat')
        go({ chatId }, options)
      },
      mobilePane,
      setMobilePane,
      draggingFiles,
    }
  }, [project.data, documents.data, documents.isSuccess, folders.data, tree, url, openTabs, activeDocumentId, mobilePane, draggingFiles, go, goToLibrary, setTabs])

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

  return (
    <WorkspaceContext value={workspace}>
      <div className="flex h-full min-h-0 flex-col">
        <div className="flex justify-center border-b border-line bg-surface px-3 py-1.5 lg:hidden">
          <Segmented
            value={mobilePane}
            onChange={setMobilePane}
            options={[
              { value: 'files', label: 'Files', icon: Files },
              { value: 'chat', label: 'Chat', icon: MessageSquare },
            ]}
          />
        </div>
        <div className="min-h-0 flex-1">
          <SplitPane storageKey={SPLIT_KEY} shown={mobilePane === 'files' ? 'left' : 'right'} left={<LeftPane />} right={<ChatPane />} />
        </div>
      </div>
    </WorkspaceContext>
  )
}

/** The tab strip with the Library or the document in front below it. */
function LeftPane() {
  const workspace = useWorkspace()
  const { activeDocumentId, url } = workspace
  const document = activeDocumentId == null ? null : workspace.documents.find((d) => d.id === activeDocumentId)
  const tab = workspace.openTabs.find((t) => t.id === activeDocumentId)
  const focus: Focus | null = url.tab.kind === 'document' ? url.tab.focus : null

  return (
    <div className="flex h-full min-h-0 flex-col bg-page">
      <TabStrip />
      <div className="min-h-0 flex-1">
        {activeDocumentId == null ? (
          <Library />
        ) : document ? (
          <DocumentTab
            key={document.id}
            document={document}
            focus={focus}
            view={tab?.view ?? defaultView(document)}
            onViewChange={(view) => workspace.setTabView(document.id, view)}
            onClearFocus={workspace.clearFocus}
          />
        ) : workspace.documentsLoaded ? (
          <div className="p-4">
            <Notice tone="bad">This document no longer exists.</Notice>
          </div>
        ) : (
          <Loading />
        )}
      </div>
    </div>
  )
}

const defaultView = (document: Document): DocView => (isPdf(document) ? 'file' : 'text')

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
