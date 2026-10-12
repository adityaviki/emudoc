import { useQuery } from '@tanstack/react-query'
import {
  ChevronRight,
  ChevronsDownUp,
  ChevronsUpDown,
  ExternalLink,
  Folder as FolderIcon,
  FolderPlus,
  MoreHorizontal,
  Pencil,
  RefreshCw,
  Search,
  Sparkles,
  Trash2,
  Upload,
  X,
} from 'lucide-react'
import { createElement, Fragment, useEffect, useRef, useState } from 'react'
import type { DragEvent, KeyboardEvent, MouseEvent, RefObject } from 'react'
import {
  ACCEPTED_EXTENSIONS,
  ACCEPTED_TYPES_LABEL,
  api,
  fileUrl,
  pageLabel,
  sectionPath,
} from '../api.ts'
import type { Document, Folder, SearchHit } from '../api.ts'
import { StatusIcon } from '../components/IndexingStatus.tsx'
import type { MenuItem } from '../components/Menu.tsx'
import { PlanLimitNotice } from '../components/PlanLimit.tsx'
import { Button, Chip, EmptyState, IconButton, Loading, Notice, Spinner } from '../components/ui.tsx'
import { useContextMenu } from '../components/useContextMenu.tsx'
import { documentsIn, useFolders, useLocalStorage } from '../hooks.ts'
import type { FolderTree } from '../hooks.ts'
import { fileKind } from './files.ts'
import { useLibraryMutations } from './mutations.ts'
import type { LibraryMutations } from './mutations.ts'
import { useWorkspace } from './state.ts'

type Item = { kind: 'folder'; folder: Folder } | { kind: 'file'; document: Document }

const itemKey = (item: Item) => (item.kind === 'folder' ? `f:${item.folder.id}` : `d:${item.document.id}`)
const itemName = (item: Item) => (item.kind === 'folder' ? item.folder.name : item.document.filename)
const parentOf = (item: Item) => (item.kind === 'folder' ? item.folder.parent_id : item.document.folder_id)

/** An item in the order it is shown, with how far below the shown folder it sits. */
interface Entry {
  item: Item
  depth: number
}

// Internal drags carry the selected items under this type; drags from the desktop carry Files.
const ITEMS_TYPE = 'application/x-emudoc-items'
interface DragPayload {
  folderIds: number[]
  documentIds: number[]
}
// What is being dragged, while it is: dataTransfer data cannot be read until the drop, only its types.
type DragRef = RefObject<DragPayload | null>

const hasItems = (event: DragEvent) => event.dataTransfer.types.includes(ITEMS_TYPE)
const hasFiles = (event: DragEvent) => event.dataTransfer.types.includes('Files')

// The folders left open in the tree, per project.
const treeKey = (projectId: number) => `emudoc.tree-open.${projectId}`
const isIdList = (value: unknown): value is number[] =>
  Array.isArray(value) && value.every((id) => typeof id === 'number')

/** The file manager: a folder's contents, with search over the whole project or the folder. */
export function Library() {
  const workspace = useWorkspace()
  const { tree, project } = workspace
  const { folderId, q: query } = workspace.url.library
  const folders = useFolders(project.id)
  const mutations = useLibraryMutations()
  const health = useQuery({ queryKey: ['status'], queryFn: api.status })
  const fileInput = useRef<HTMLInputElement>(null)
  const dragging = useRef<DragPayload | null>(null)
  const [creating, setCreating] = useState(false)
  const [overPane, setOverPane] = useState(false)

  // A folder that no longer exists falls back to the root.
  const folderMissing = folders.isSuccess && folderId != null && !tree.byId.has(folderId)
  useEffect(() => {
    if (folderMissing) workspace.goToFolder(null, { replace: true })
  }, [folderMissing, workspace])

  const current = folderId == null ? null : tree.byId.get(folderId)
  const currentName = current?.name ?? project.name

  function uploadFiles(files: File[], targetId: number | null) {
    // Dropped directories arrive as empty, typeless files.
    const accepted = files.filter((file) => file.size > 0 || file.type !== '')
    if (accepted.length < files.length) mutations.setNotice('Folders cannot be uploaded; drop files instead.')
    if (accepted.length) mutations.upload.mutate({ files: accepted, folderId: targetId })
  }

  // Documents indexed before a model was configured can catch up in one go.
  const waitingForModel = workspace.documents.filter((d) => d.stages.enrich?.status === 'skipped')

  return (
    <div
      className="relative flex h-full min-h-0 flex-col bg-page"
      onDragOver={(event) => {
        if (!hasFiles(event)) return
        event.preventDefault()
        event.dataTransfer.dropEffect = 'copy'
        setOverPane(true)
      }}
      onDragLeave={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setOverPane(false)
      }}
      onDrop={(event) => {
        setOverPane(false)
        if (!hasFiles(event)) return
        event.preventDefault()
        uploadFiles([...event.dataTransfer.files], folderId)
      }}
    >
      <div className="@container shrink-0 border-b border-line bg-surface">
        <div className="flex h-11 items-center px-3">
          <Breadcrumb folderId={folderId} mutations={mutations} dragging={dragging} onUpload={uploadFiles} />
        </div>
        <div className="flex items-center gap-2 px-3 pb-2.5">
          <SearchBox query={query} scope={folderId == null ? null : currentName} />
          <Button
            icon={<FolderPlus className="size-4" />}
            onClick={() => setCreating(true)}
            disabled={!!query}
            title="New folder"
            aria-label="New folder"
            className="px-2"
          />
          {/* A narrow pane keeps the icon only. */}
          <Button
            icon={mutations.upload.isPending ? <Spinner /> : <Upload className="size-4" />}
            onClick={() => fileInput.current?.click()}
            disabled={mutations.upload.isPending}
            title={ACCEPTED_TYPES_LABEL}
            aria-label={mutations.upload.isPending ? 'Uploading…' : 'Upload'}
            className="px-2 @xs:px-3"
          >
            <span className="hidden @xs:inline">{mutations.upload.isPending ? 'Uploading…' : 'Upload'}</span>
          </Button>
        </div>
      </div>

      <div className="shrink-0 space-y-2 empty:hidden px-3 pt-2">
        {mutations.notice && (
          <Notice tone="bad">
            <div className="flex items-start justify-between gap-3">
              <span>{mutations.notice}</span>
              <IconButton label="Dismiss" onClick={() => mutations.setNotice(null)} className="p-0.5!">
                <X className="size-3.5" />
              </IconButton>
            </div>
          </Notice>
        )}
        {mutations.planLimit && <PlanLimitNotice message={mutations.planLimit} onDismiss={mutations.clearPlanLimit} />}
        {mutations.rejected.length > 0 && (
          <Notice>
            <div className="flex items-start justify-between gap-3">
              <ul>
                {mutations.rejected.map((file) => (
                  <li key={file.filename}>
                    <span className="font-medium">{file.filename}</span>: {file.reason}
                  </li>
                ))}
              </ul>
              <IconButton label="Dismiss" onClick={mutations.clearRejected} className="p-0.5!">
                <X className="size-3.5" />
              </IconButton>
            </div>
          </Notice>
        )}
        {health.data?.llm.available && waitingForModel.length > 0 && !query && (
          <Notice tone="info">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span>
                {waitingForModel.length} document{waitingForModel.length === 1 ? ' has' : 's have'} no summaries or
                facts yet.
              </span>
              <Button
                icon={<Sparkles className="size-4" />}
                onClick={() => mutations.enrichAll.mutate()}
                disabled={mutations.enrichAll.isPending}
              >
                Run the model on them
              </Button>
            </div>
          </Notice>
        )}
      </div>

      {query ? (
        <SearchResults query={query} folderId={folderId} scope={folderId == null ? null : currentName} />
      ) : (
        <Listing
          key={folderId ?? 'root'}
          folderId={folderId}
          creating={creating}
          onCreatingChange={setCreating}
          mutations={mutations}
          dragging={dragging}
          onDragChange={(payload) => {
            dragging.current = payload
          }}
          onUploadFiles={uploadFiles}
          onPickFiles={() => fileInput.current?.click()}
        />
      )}

      <input
        ref={fileInput}
        type="file"
        multiple
        hidden
        accept={ACCEPTED_EXTENSIONS.join(',')}
        onChange={(event) => {
          const files = [...(event.target.files ?? [])]
          if (files.length) uploadFiles(files, folderId)
          event.target.value = ''
        }}
      />

      {workspace.draggingFiles && overPane && (
        <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center bg-page/80 p-4 backdrop-blur-xs">
          <div className="rounded-xl border-2 border-dashed border-accent/60 bg-surface px-8 py-6 text-center text-sm shadow-lg shadow-ink/5">
            <div className="mx-auto mb-3 flex size-10 items-center justify-center rounded-full bg-accent-soft text-accent">
              <Upload className="size-5" />
            </div>
            Drop to upload into <span className="font-semibold">{currentName}</span>
            <p className="mt-0.5 text-xs text-muted">or onto a folder</p>
          </div>
        </div>
      )}
    </div>
  )
}

interface BreadcrumbProps {
  folderId: number | null
  mutations: LibraryMutations
  dragging: DragRef
  onUpload: (files: File[], targetId: number | null) => void
}

/** The path to the current folder. Each part opens that folder and takes drops into it. */
function Breadcrumb({ folderId, mutations, dragging, onUpload }: BreadcrumbProps) {
  const workspace = useWorkspace()
  const crumbs: { id: number | null; name: string }[] = [
    { id: null, name: workspace.project.name },
    ...workspace.tree.pathTo(folderId).map((folder) => ({ id: folder.id, name: folder.name })),
  ]
  const [over, setOver] = useState<number | null | 'none'>('none')

  return (
    <nav aria-label="Folder path" className="flex min-w-0 items-center gap-0.5 text-[13px]">
      {crumbs.map((crumb, index) => {
        const last = index === crumbs.length - 1
        return (
          <Fragment key={crumb.id ?? 'root'}>
            {index > 0 && <ChevronRight className="size-3.5 shrink-0 text-muted/60" />}
            <button
              type="button"
              title={crumb.name}
              disabled={last}
              onClick={() => workspace.goToFolder(crumb.id)}
              onDragOver={(event) => {
                if (last) return
                if (hasItems(event) && !canDropItems(workspace.tree, crumb.id, dragging.current)) return
                if (!hasItems(event) && !hasFiles(event)) return
                event.preventDefault()
                event.stopPropagation()
                event.dataTransfer.dropEffect = hasItems(event) ? 'move' : 'copy'
                setOver(crumb.id)
              }}
              onDragLeave={() => setOver('none')}
              onDrop={(event) => {
                setOver('none')
                if (last) return
                event.preventDefault()
                event.stopPropagation()
                if (hasItems(event)) {
                  const payload = JSON.parse(event.dataTransfer.getData(ITEMS_TYPE)) as DragPayload
                  mutations.moveItems.mutate({ ...payload, targetId: crumb.id })
                } else if (hasFiles(event)) {
                  onUpload([...event.dataTransfer.files], crumb.id)
                }
              }}
              // When the path does not fit, the folders above give way before the current one. The factor
              // is large enough that the current one does not lose even a fraction of a pixel, which
              // would already cost it an ellipsis.
              className={`h-6.5 max-w-48 truncate rounded-md px-1.5 transition ${
                last ? 'font-semibold text-ink' : 'min-w-7 shrink-100000 text-muted hover:bg-sunken hover:text-ink'
              } ${over !== 'none' && over === crumb.id ? 'bg-accent-soft text-accent ring-1 ring-accent' : ''}`}
            >
              {crumb.name}
            </button>
          </Fragment>
        )
      })}
    </nav>
  )
}

/** Whether the items being dragged may land in this folder. */
function canDropItems(tree: FolderTree, targetId: number | null, payload: DragPayload | null): boolean {
  if (!payload) return false
  const { folderIds, documentIds } = payload
  // A folder cannot go inside itself or any folder below it.
  if (targetId != null && folderIds.some((id) => tree.subtree(id).has(targetId))) return false
  // Nothing to do when every item is already in the target.
  const alreadyThere =
    folderIds.every((id) => tree.byId.get(id)?.parent_id === targetId) &&
    documentIds.every((id) => documentsParent.get(id) === targetId)
  return !alreadyThere
}
// The folder each document is in, kept current by the Listing for the drop checks above.
const documentsParent = new Map<number, number | null>()

function SearchBox({ query, scope }: { query: string; scope: string | null }) {
  const workspace = useWorkspace()
  const [draft, setDraft] = useState(query)
  // The URL can change the query from outside (opening a tab, going back).
  const [lastQuery, setLastQuery] = useState(query)
  if (query !== lastQuery) {
    setLastQuery(query)
    setDraft(query)
  }

  useEffect(() => {
    const trimmed = draft.trim()
    if (trimmed === query) return
    const timer = window.setTimeout(() => workspace.setSearch(trimmed), 300)
    return () => window.clearTimeout(timer)
  }, [draft, query, workspace])

  return (
    <div className="relative min-w-0 flex-1">
      <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted" />
      <input
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Escape') setDraft('')
        }}
        placeholder={scope ? `Search in ${scope}` : 'Search the project'}
        aria-label="Search"
        className="field h-8 pr-8 pl-8 text-[13px]"
      />
      {draft && (
        <button
          type="button"
          aria-label="Clear search"
          onClick={() => setDraft('')}
          className="absolute top-1/2 right-1.5 -translate-y-1/2 rounded p-0.5 text-muted transition hover:bg-sunken hover:text-ink"
        >
          <X className="size-3.5" />
        </button>
      )}
    </div>
  )
}

interface ListingProps {
  folderId: number | null
  creating: boolean
  onCreatingChange: (creating: boolean) => void
  mutations: LibraryMutations
  dragging: DragRef
  onDragChange: (payload: DragPayload | null) => void
  onUploadFiles: (files: File[], targetId: number | null) => void
  onPickFiles: () => void
}

/** The folders and files in one folder, with selection, renaming, drag and drop and a menu. */
function Listing({
  folderId,
  creating,
  onCreatingChange,
  mutations,
  dragging,
  onDragChange,
  onUploadFiles,
  onPickFiles,
}: ListingProps) {
  const workspace = useWorkspace()
  const { tree, documents } = workspace
  const [openFolders, setOpenFolders] = useLocalStorage<number[]>(treeKey(workspace.project.id), [], isIdList)
  const openSet = new Set(openFolders)
  const entries = treeEntries(tree, documents, folderId, openSet)
  const items = entries.map((entry) => entry.item)
  const keys = items.map(itemKey)
  useEffect(() => {
    for (const document of documents) documentsParent.set(document.id, document.folder_id)
  }, [documents])

  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [anchor, setAnchor] = useState<string | null>(null)
  const [cursor, setCursor] = useState<string | null>(null)
  const [renaming, setRenaming] = useState<string | null>(null)
  const [dropTarget, setDropTarget] = useState<string | null>(null)
  const menu = useContextMenu()
  const list = useRef<HTMLDivElement>(null)
  // A closed folder held under a drag opens after a moment, so the drag can go deeper.
  const expandTimer = useRef<{ id: number; timer: number } | null>(null)

  // Selection cannot outlive the items it points at.
  const present = new Set(keys)
  const liveSelected = new Set([...selected].filter((key) => present.has(key)))
  const selectedItems = items.filter((item) => liveSelected.has(itemKey(item)))

  useEffect(() => {
    if (cursor) list.current?.querySelector(`[data-key="${CSS.escape(cursor)}"]`)?.scrollIntoView({ block: 'nearest' })
  }, [cursor])

  useEffect(
    () => () => {
      if (expandTimer.current) window.clearTimeout(expandTimer.current.timer)
    },
    [],
  )

  function select(key: string, event?: { ctrlKey: boolean; metaKey: boolean; shiftKey: boolean }) {
    setCursor(key)
    if (event?.shiftKey && anchor && present.has(anchor)) {
      const [from, to] = [keys.indexOf(anchor), keys.indexOf(key)].sort((a, b) => a - b)
      setSelected(new Set(keys.slice(from, to + 1)))
      return
    }
    setAnchor(key)
    if (event?.ctrlKey || event?.metaKey) {
      setSelected((current) => {
        const next = new Set(current)
        if (next.has(key)) next.delete(key)
        else next.add(key)
        return next
      })
    } else {
      setSelected(new Set([key]))
    }
  }

  function open(item: Item) {
    if (item.kind === 'folder') workspace.goToFolder(item.folder.id)
    else workspace.openDocument(item.document.id)
  }

  function setExpanded(id: number, expanded: boolean) {
    // Closing a folder over the cursor moves the cursor up to it.
    const at = cursor ? items[keys.indexOf(cursor)] : undefined
    const parent = at && parentOf(at)
    if (!expanded && parent != null && tree.subtree(id).has(parent)) setCursor(`f:${id}`)
    setOpenFolders((current) => (expanded ? [...new Set([...current, id])] : current.filter((open) => open !== id)))
  }

  /** A folder opens in place; a file opens in the middle pane. */
  function activate(item: Item) {
    if (item.kind === 'folder') setExpanded(item.folder.id, !openSet.has(item.folder.id))
    else open(item)
  }

  function remove(chosen: Item[]) {
    const targets = outermost(chosen, tree)
    if (!targets.length) return
    const folders = targets.filter((item) => item.kind === 'folder')
    const files = targets.filter((item) => item.kind === 'file')
    const inFolders = folders.flatMap((item) =>
      item.kind === 'folder' ? documents.filter((d) => d.folder_id != null && tree.subtree(item.folder.id).has(d.folder_id)) : [],
    )
    const message =
      targets.length === 1 && folders.length === 1
        ? `Delete "${itemName(targets[0])}" and everything in it (${inFolders.length} file${inFolders.length === 1 ? '' : 's'})?`
        : targets.length === 1
          ? `Delete "${itemName(targets[0])}"?`
          : `Delete ${targets.length} items?`
    if (!window.confirm(message)) return
    for (const item of folders) {
      if (item.kind === 'folder') {
        const ids = documents.filter((d) => d.folder_id != null && tree.subtree(item.folder.id).has(d.folder_id)).map((d) => d.id)
        mutations.deleteFolder.mutate({ id: item.folder.id, documentIds: ids })
      }
    }
    const fileIds = files.flatMap((item) => (item.kind === 'file' ? [item.document.id] : []))
    if (fileIds.length) mutations.deleteDocuments.mutate(fileIds)
    setSelected(new Set())
  }

  function commitRename(item: Item, value: string) {
    setRenaming(null)
    const name = value.trim()
    if (!name || name === itemName(item)) return
    if (name.includes('/')) {
      mutations.setNotice("A name cannot contain '/'.")
      return
    }
    if (item.kind === 'folder') mutations.renameFolder.mutate({ id: item.folder.id, name })
    else mutations.renameDocument.mutate({ id: item.document.id, filename: name })
  }

  function itemsMenu(targets: Item[]): MenuItem[] {
    if (targets.length > 1) {
      return [{ label: `Delete ${targets.length} items`, icon: Trash2, danger: true, onSelect: () => remove(targets) }]
    }
    const [item] = targets
    const rename: MenuItem = { label: 'Rename', icon: Pencil, shortcut: 'F2', onSelect: () => setRenaming(itemKey(item)) }
    const del: MenuItem = { label: 'Delete', icon: Trash2, shortcut: 'Del', danger: true, onSelect: () => remove([item]) }
    if (item.kind === 'folder') {
      return [
        { label: 'Open', onSelect: () => open(item) },
        rename,
        'separator',
        del,
      ]
    }
    return [
      { label: 'Open', onSelect: () => open(item) },
      {
        label: 'Open original file',
        icon: ExternalLink,
        onSelect: () => window.open(fileUrl(item.document.id), '_blank', 'noreferrer'),
      },
      rename,
      {
        label: 'Index again',
        icon: RefreshCw,
        disabled: item.document.status === 'processing',
        onSelect: () => mutations.reprocess.mutate(item.document.id),
      },
      'separator',
      del,
    ]
  }

  const backgroundMenu: MenuItem[] = [
    { label: 'New folder', icon: FolderPlus, onSelect: () => onCreatingChange(true) },
    { label: 'Upload files', icon: Upload, onSelect: onPickFiles },
    'separator',
    { label: 'Select all', shortcut: 'Ctrl+A', disabled: !items.length, onSelect: () => setSelected(new Set(keys)) },
    'separator',
    {
      label: 'Expand all',
      icon: ChevronsUpDown,
      // Every folder below this one that has subfolders or files of its own.
      onSelect: () => setOpenFolders((current) => [...new Set([...current, ...expandable(tree, documents, folderId)])]),
    },
    {
      label: 'Collapse all',
      icon: ChevronsDownUp,
      disabled: !entries.some((entry) => entry.depth > 0),
      onSelect: () => {
        const shown = folderId == null ? null : tree.subtree(folderId)
        setOpenFolders((current) => (shown ? current.filter((id) => id === folderId || !shown.has(id)) : []))
      },
    },
  ]

  function onContextMenu(event: MouseEvent, item?: Item) {
    if (!item) {
      menu.open(event, backgroundMenu)
      return
    }
    const key = itemKey(item)
    if (!liveSelected.has(key)) select(key)
    const targets = liveSelected.has(key) && liveSelected.size > 1 ? selectedItems : [item]
    menu.open(event, itemsMenu(targets))
  }

  function onKeyDown(event: KeyboardEvent) {
    if (renaming || creating) return
    const index = cursor ? keys.indexOf(cursor) : -1
    const item = items[index] as Item | undefined
    const moveTo = (next: number) => {
      if (!keys.length) return
      event.preventDefault()
      select(keys[Math.max(0, Math.min(keys.length - 1, next))], { ctrlKey: false, metaKey: false, shiftKey: event.shiftKey })
    }
    switch (event.key) {
      case 'ArrowDown':
        return moveTo(index < 0 ? 0 : index + 1)
      case 'ArrowUp':
        if (event.altKey) {
          event.preventDefault()
          return goUp()
        }
        return moveTo(index < 0 ? 0 : index - 1)
      case 'ArrowRight':
        // Open a closed folder, or step into an open one.
        if (item?.kind === 'folder') {
          event.preventDefault()
          if (!openSet.has(item.folder.id)) setExpanded(item.folder.id, true)
          else if ((entries[index + 1]?.depth ?? 0) > entries[index].depth) moveTo(index + 1)
        }
        return
      case 'ArrowLeft':
        // Close an open folder, or step out to the folder holding the item.
        if (item) {
          event.preventDefault()
          const parent = parentOf(item)
          if (item.kind === 'folder' && openSet.has(item.folder.id)) setExpanded(item.folder.id, false)
          else if (parent != null && parent !== folderId) moveTo(keys.indexOf(`f:${parent}`))
        }
        return
      case 'Home':
        return moveTo(0)
      case 'End':
        return moveTo(keys.length - 1)
      case 'Enter':
        if (item) {
          event.preventDefault()
          activate(item)
        }
        return
      case 'Backspace':
        event.preventDefault()
        return goUp()
      case 'F2':
        if (liveSelected.size === 1) {
          event.preventDefault()
          setRenaming([...liveSelected][0])
        }
        return
      case 'Delete':
        event.preventDefault()
        return remove(selectedItems)
      case 'Escape':
        setSelected(new Set())
        return
      case 'a':
        if (event.ctrlKey || event.metaKey) {
          event.preventDefault()
          setSelected(new Set(keys))
        }
        return
    }
  }

  function goUp() {
    const current = folderId == null ? null : tree.byId.get(folderId)
    if (current) workspace.goToFolder(current.parent_id)
  }

  function onDragStart(event: DragEvent, item: Item) {
    const key = itemKey(item)
    // A folder carries its contents, so anything selected inside a dragged folder stays where it is.
    const dragged = outermost(liveSelected.has(key) ? selectedItems : [item], tree)
    if (!liveSelected.has(key)) select(key)
    const payload: DragPayload = {
      folderIds: dragged.flatMap((i) => (i.kind === 'folder' ? [i.folder.id] : [])),
      documentIds: dragged.flatMap((i) => (i.kind === 'file' ? [i.document.id] : [])),
    }
    onDragChange(payload)
    event.dataTransfer.setData(ITEMS_TYPE, JSON.stringify(payload))
    event.dataTransfer.effectAllowed = 'move'
  }

  function cancelExpand() {
    if (expandTimer.current) window.clearTimeout(expandTimer.current.timer)
    expandTimer.current = null
  }

  function onFolderDragOver(event: DragEvent, folder: Folder) {
    if (hasItems(event)) {
      if (!canDropItems(tree, folder.id, dragging.current)) return
      event.dataTransfer.dropEffect = 'move'
    } else if (hasFiles(event)) {
      event.dataTransfer.dropEffect = 'copy'
    } else {
      return
    }
    event.preventDefault()
    event.stopPropagation()
    setDropTarget(`f:${folder.id}`)
    if (!openSet.has(folder.id) && expandTimer.current?.id !== folder.id) {
      cancelExpand()
      const timer = window.setTimeout(() => {
        expandTimer.current = null
        setOpenFolders((current) => [...new Set([...current, folder.id])])
      }, 600)
      expandTimer.current = { id: folder.id, timer }
    }
  }

  function onFolderDrop(event: DragEvent, folder: Folder) {
    setDropTarget(null)
    cancelExpand()
    if (hasItems(event)) {
      event.preventDefault()
      event.stopPropagation()
      const payload = JSON.parse(event.dataTransfer.getData(ITEMS_TYPE)) as DragPayload
      if (canDropItems(tree, folder.id, payload)) mutations.moveItems.mutate({ ...payload, targetId: folder.id })
    } else if (hasFiles(event)) {
      event.preventDefault()
      event.stopPropagation()
      onUploadFiles([...event.dataTransfer.files], folder.id)
    }
  }

  const empty = items.length === 0 && !creating
  return (
    <div
      ref={list}
      role="tree"
      aria-multiselectable
      tabIndex={0}
      onKeyDown={onKeyDown}
      onContextMenu={(event) => onContextMenu(event)}
      onClick={(event) => {
        if (event.target === event.currentTarget) setSelected(new Set())
      }}
      className="group/list min-h-0 flex-1 overflow-y-auto p-2 outline-none"
    >
      {empty && (
        <EmptyState icon={<FolderIcon />} title={folderId == null ? 'This library is empty' : 'This folder is empty'}>
          Upload files or drop them here. Each one is parsed, outlined and indexed in the background.
          <br />
          {ACCEPTED_TYPES_LABEL}.
        </EmptyState>
      )}
      {creating && (
        <GhostFolderRow
          onCommit={(name) => {
            onCreatingChange(false)
            const trimmed = name.trim()
            if (!trimmed) return
            if (trimmed.includes('/')) {
              mutations.setNotice("A name cannot contain '/'.")
              return
            }
            mutations.createFolder.mutate({ name: trimmed, parentId: folderId })
          }}
          onCancel={() => onCreatingChange(false)}
        />
      )}
      {entries.map(({ item, depth }) => {
        const key = itemKey(item)
        return (
          <Row
            key={key}
            item={item}
            depth={depth}
            expanded={item.kind === 'folder' && openSet.has(item.folder.id)}
            selected={liveSelected.has(key)}
            isCursor={cursor === key}
            renaming={renaming === key}
            dropping={dropTarget === key}
            onSelect={(event) => select(key, event)}
            onOpen={() => activate(item)}
            onToggle={() => item.kind === 'folder' && setExpanded(item.folder.id, !openSet.has(item.folder.id))}
            onContextMenu={(event) => onContextMenu(event, item)}
            onMenuButton={(element) => {
              select(key)
              menu.openAt(element, itemsMenu([item]))
            }}
            onRename={(value) => commitRename(item, value)}
            onCancelRename={() => setRenaming(null)}
            onDragStart={(event) => onDragStart(event, item)}
            onDragEnd={() => {
              onDragChange(null)
              setDropTarget(null)
              cancelExpand()
            }}
            onDragOver={item.kind === 'folder' ? (event) => onFolderDragOver(event, item.folder) : undefined}
            onDragLeave={(event) => {
              if (event.currentTarget.contains(event.relatedTarget as Node | null)) return
              if (dropTarget === key) setDropTarget(null)
              if (item.kind === 'folder' && expandTimer.current?.id === item.folder.id) cancelExpand()
            }}
            onDrop={item.kind === 'folder' ? (event) => onFolderDrop(event, item.folder) : undefined}
            folderCount={
              item.kind === 'folder'
                ? (tree.children.get(item.folder.id)?.length ?? 0) + documentsIn(documents, item.folder.id).length
                : undefined
            }
          />
        )
      })}
      {menu.element}
    </div>
  )
}

/** The items under a folder as the tree shows them: each open folder followed by its contents. */
function treeEntries(tree: FolderTree, documents: Document[], rootId: number | null, open: Set<number>): Entry[] {
  const entries: Entry[] = []
  const visit = (folderId: number | null, depth: number) => {
    for (const folder of tree.children.get(folderId) ?? []) {
      entries.push({ item: { kind: 'folder', folder }, depth })
      if (open.has(folder.id)) visit(folder.id, depth + 1)
    }
    for (const document of documentsIn(documents, folderId)) entries.push({ item: { kind: 'file', document }, depth })
  }
  visit(rootId, 0)
  return entries
}

/** The folders below this one that have anything in them to show when opened. */
function expandable(tree: FolderTree, documents: Document[], rootId: number | null): number[] {
  const filled = new Set(documents.flatMap((document) => (document.folder_id == null ? [] : [document.folder_id])))
  return [...tree.byId.values()]
    .filter((folder) => folder.id !== rootId && (tree.children.has(folder.id) || filled.has(folder.id)))
    .filter((folder) => rootId == null || tree.pathTo(folder.parent_id).some((above) => above.id === rootId))
    .map((folder) => folder.id)
}

/** The items not inside a folder that is itself among them; acting on a folder covers what it holds. */
function outermost(items: Item[], tree: FolderTree): Item[] {
  const folderIds = new Set(items.flatMap((item) => (item.kind === 'folder' ? [item.folder.id] : [])))
  return items.filter((item) => !tree.pathTo(parentOf(item)).some((folder) => folderIds.has(folder.id)))
}

interface RowProps {
  item: Item
  /** How far below the shown folder the item sits, which is how far it is indented. */
  depth: number
  /** Whether a folder shows its contents. */
  expanded: boolean
  selected: boolean
  isCursor: boolean
  renaming: boolean
  dropping: boolean
  folderCount?: number
  onSelect: (event: MouseEvent) => void
  onOpen: () => void
  onToggle: () => void
  onContextMenu: (event: MouseEvent) => void
  onMenuButton: (element: Element) => void
  onRename: (value: string) => void
  onCancelRename: () => void
  onDragStart: (event: DragEvent) => void
  onDragEnd: () => void
  onDragOver?: (event: DragEvent) => void
  onDragLeave: (event: DragEvent) => void
  onDrop?: (event: DragEvent) => void
}

function ItemIcon({ item, className }: { item: Item; className: string }) {
  if (item.kind === 'folder') return <FolderIcon className={`${className} fill-accent/15 text-accent`} />
  const kind = fileKind(item.document)
  return createElement(kind.icon, { className: `${className} ${kind.color}`, 'aria-label': kind.label })
}

function Row(props: RowProps) {
  const { item, depth, expanded, selected, isCursor, renaming, dropping, folderCount } = props
  const name = itemName(item)

  const handlers = {
    role: 'treeitem' as const,
    'aria-selected': selected,
    'data-key': itemKey(item),
    draggable: !renaming,
    onClick: (event: MouseEvent) => {
      event.stopPropagation()
      if (!renaming) props.onSelect(event)
    },
    onDoubleClick: () => !renaming && props.onOpen(),
    onContextMenu: props.onContextMenu,
    onDragStart: props.onDragStart,
    onDragEnd: props.onDragEnd,
    onDragOver: props.onDragOver,
    onDragLeave: props.onDragLeave,
    onDrop: props.onDrop,
  }
  // The keyboard cursor is only marked while the keyboard is in the listing.
  const state = `${selected ? 'bg-accent-soft' : 'hover:bg-sunken'} ${isCursor ? 'group-focus/list:ring-1 group-focus/list:ring-accent/50' : ''} ${
    dropping ? 'bg-accent-soft ring-2 ring-accent' : ''
  }`
  const rename = (
    <RenameInput value={name} isFile={item.kind === 'file'} onCommit={props.onRename} onCancel={props.onCancelRename} />
  )
  const menuButton = (
    <IconButton
      label="More actions"
      className={`size-6! shrink-0 ${selected ? '' : 'opacity-0 group-hover:opacity-100 focus-visible:opacity-100'}`}
      onClick={(event) => {
        event.stopPropagation()
        props.onMenuButton(event.currentTarget)
      }}
    >
      <MoreHorizontal className="size-4" />
    </IconButton>
  )

  const folder = item.kind === 'folder'
  return (
    <div
      {...handlers}
      aria-level={depth + 1}
      aria-expanded={folder ? expanded : undefined}
      className={`group flex h-7 items-center rounded-md pr-0.5 pl-1.5 text-[13px] transition-colors select-none ${state}`}
    >
      {/* A guide line for each level above, under the chevron of the folder it belongs to. */}
      {Array.from({ length: depth }, (_, level) => (
        <span
          key={level}
          aria-hidden
          className="relative w-4 shrink-0 self-stretch before:absolute before:inset-y-0 before:left-1/2 before:border-l before:border-line"
        />
      ))}
      {folder && (folderCount ?? 0) > 0 ? (
        <button
          type="button"
          tabIndex={-1}
          aria-label={expanded ? 'Collapse' : 'Expand'}
          onClick={(event) => {
            event.stopPropagation()
            props.onToggle()
          }}
          onDoubleClick={(event) => event.stopPropagation()}
          className="flex size-4 shrink-0 items-center justify-center rounded-sm text-muted transition hover:text-ink"
        >
          <ChevronRight className={`size-3.5 transition-transform ${expanded ? 'rotate-90' : ''}`} />
        </button>
      ) : (
        <span className="w-4 shrink-0" />
      )}
      <ItemIcon item={item} className="ml-1 size-4 shrink-0" />
      <div className="ml-2 min-w-0 flex-1">
        {renaming ? (
          rename
        ) : (
          <p className="truncate" title={item.kind === 'file' ? (item.document.title ?? name) : name}>
            {name}
          </p>
        )}
      </div>
      {item.kind === 'file' && (
        <span className="ml-2 flex">
          <StatusIcon document={item.document} />
        </span>
      )}
      <span className="ml-1 flex">{menuButton}</span>
    </div>
  )
}

interface RenameInputProps {
  value: string
  isFile: boolean
  onCommit: (value: string) => void
  onCancel: () => void
}

/** An inline name editor. Enter or leaving the field commits; Escape cancels and nothing else fires. */
function RenameInput({ value, isFile, onCommit, onCancel }: RenameInputProps) {
  const cancelled = useRef(false)
  return (
    <input
      autoFocus
      defaultValue={value}
      aria-label="New name"
      maxLength={isFile ? 500 : 200}
      onFocus={(event) => {
        // Select the name but not the extension, which is kept anyway.
        const dot = isFile ? value.lastIndexOf('.') : -1
        event.target.setSelectionRange(0, dot > 0 ? dot : value.length)
      }}
      onClick={(event) => event.stopPropagation()}
      onDoubleClick={(event) => event.stopPropagation()}
      onKeyDown={(event) => {
        event.stopPropagation()
        if (event.key === 'Enter') onCommit(event.currentTarget.value)
        if (event.key === 'Escape') {
          cancelled.current = true
          onCancel()
        }
      }}
      onBlur={(event) => {
        if (!cancelled.current) onCommit(event.target.value)
      }}
      className="field rounded px-1.5 py-0.5 text-[13px]"
    />
  )
}

interface GhostFolderRowProps {
  onCommit: (name: string) => void
  onCancel: () => void
}

/** The folder being created, in the place it will appear once named. */
function GhostFolderRow({ onCommit, onCancel }: GhostFolderRowProps) {
  return (
    <div className="flex h-7 items-center rounded-md bg-accent-soft pr-0.5 pl-1.5 text-[13px]">
      <span className="w-4 shrink-0" />
      <FolderIcon className="ml-1 size-4 shrink-0 fill-accent/15 text-accent" />
      <div className="ml-2 min-w-0 flex-1">
        <RenameInput value="New folder" isFile={false} onCommit={onCommit} onCancel={onCancel} />
      </div>
    </div>
  )
}

interface SearchResultsProps {
  query: string
  folderId: number | null
  scope: string | null
}

function SearchResults({ query, folderId, scope }: SearchResultsProps) {
  const workspace = useWorkspace()
  const results = useQuery({
    queryKey: ['search', workspace.project.id, query, folderId],
    queryFn: () => api.search(workspace.project.id, query, folderId),
  })

  return (
    <div className="min-h-0 flex-1 space-y-2 overflow-y-auto p-3">
      {results.isPending && <Loading />}
      {results.isError && <Notice tone="bad">Search failed: {results.error.message}</Notice>}
      {results.data && !results.data.semantic && (
        <Notice>The embedding service is offline, so these are keyword matches only.</Notice>
      )}
      {results.data?.hits.length === 0 && (
        <EmptyState title={`Nothing found for "${query}"${scope ? ` in ${scope}` : ''}`}>
          Results combine exact word matches with matches on meaning.
        </EmptyState>
      )}
      {results.data?.hits.map((hit) => (
        <Hit key={hit.passage_id} hit={hit} onOpen={() => workspace.openDocument(hit.document_id, { passage: hit.passage_id })} />
      ))}
    </div>
  )
}

function Hit({ hit, onOpen }: { hit: SearchHit; onOpen: () => void }) {
  const location = [sectionPath(hit.context, hit.document_title), pageLabel(hit.page_start, hit.page_end)]
    .filter(Boolean)
    .join(' · ')
  // Which ranking found the passage: exact words, meaning, or both.
  const found = [hit.keyword_rank && 'words', hit.vector_rank && 'meaning'].filter(Boolean).join(' + ')

  return (
    <button
      type="button"
      onClick={onOpen}
      className="block w-full rounded-lg border border-line bg-surface px-4 py-3 text-left shadow-xs transition hover:border-ink/15 hover:shadow-sm"
    >
      <div className="flex items-baseline justify-between gap-3">
        <p className="min-w-0 truncate text-[13px] font-semibold">
          {hit.document_title}
          {hit.doc_type && (
            <span className="ml-2 align-middle">
              <Chip>{hit.doc_type}</Chip>
            </span>
          )}
        </p>
        <span className="shrink-0 text-[11px] text-muted" title="How this passage was found">
          {found}
        </span>
      </div>
      {location && <p className="mt-0.5 text-xs text-muted">{location}</p>}
      <p className="mt-2 text-[13px] leading-relaxed text-ink/90">
        {hit.snippet.map((segment, index) =>
          segment.match ? <mark key={index}>{segment.text}</mark> : <span key={index}>{segment.text}</span>,
        )}
      </p>
    </button>
  )
}
