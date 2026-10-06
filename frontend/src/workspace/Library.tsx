import { useQuery } from '@tanstack/react-query'
import {
  ChevronRight,
  ExternalLink,
  Folder as FolderIcon,
  FolderPlus,
  LayoutGrid,
  List,
  MoreHorizontal,
  Pencil,
  RefreshCw,
  Search,
  Sparkles,
  Trash2,
  Upload,
  X,
} from 'lucide-react'
import { createElement, useEffect, useRef, useState } from 'react'
import type { DragEvent, KeyboardEvent, MouseEvent, RefObject } from 'react'
import { ACCEPTED_EXTENSIONS, ACCEPTED_TYPES_LABEL, api, fileUrl, pageLabel, sectionPath } from '../api.ts'
import type { Document, Folder, SearchHit } from '../api.ts'
import { IndexingStatus } from '../components/IndexingStatus.tsx'
import type { MenuItem } from '../components/Menu.tsx'
import { Button, Chip, EmptyState, IconButton, Loading, Notice, Segmented, Spinner } from '../components/ui.tsx'
import { useContextMenu } from '../components/useContextMenu.tsx'
import { documentsIn, useFolders, useLocalStorage } from '../hooks.ts'
import type { FolderTree } from '../hooks.ts'
import { fileIcon } from './files.ts'
import { useLibraryMutations } from './mutations.ts'
import type { LibraryMutations } from './mutations.ts'
import { useWorkspace } from './state.ts'

type Item = { kind: 'folder'; folder: Folder } | { kind: 'file'; document: Document }

const itemKey = (item: Item) => (item.kind === 'folder' ? `f:${item.folder.id}` : `d:${item.document.id}`)
const itemName = (item: Item) => (item.kind === 'folder' ? item.folder.name : item.document.filename)

// Internal drags carry the selected items under this type; drags from the desktop carry Files.
const ITEMS_TYPE = 'application/x-paperless-items'
interface DragPayload {
  folderIds: number[]
  documentIds: number[]
}
// What is being dragged, while it is: dataTransfer data cannot be read until the drop, only its types.
type DragRef = RefObject<DragPayload | null>

const hasItems = (event: DragEvent) => event.dataTransfer.types.includes(ITEMS_TYPE)
const hasFiles = (event: DragEvent) => event.dataTransfer.types.includes('Files')

// How a folder's contents are laid out: tiles in a grid, or one row per item with its details.
type View = 'grid' | 'list'
const VIEW_KEY = 'paperless.library-view'
const isView = (value: unknown): value is View => value === 'grid' || value === 'list'

/** The file manager: a folder's contents, with search over the whole project or the folder. */
export function Library() {
  const workspace = useWorkspace()
  const { tree, project } = workspace
  const folderId = workspace.url.tab.kind === 'library' ? workspace.url.tab.folderId : null
  const query = workspace.url.tab.kind === 'library' ? workspace.url.tab.q : ''
  const folders = useFolders(project.id)
  const mutations = useLibraryMutations()
  const health = useQuery({ queryKey: ['health'], queryFn: api.health })
  const fileInput = useRef<HTMLInputElement>(null)
  const dragging = useRef<DragPayload | null>(null)
  const [creating, setCreating] = useState(false)
  const [overPane, setOverPane] = useState(false)
  const [view, setView] = useLocalStorage<View>(VIEW_KEY, 'grid', isView)

  // A folder that no longer exists falls back to the root.
  const folderMissing = folders.isSuccess && folderId != null && !tree.byId.has(folderId)
  useEffect(() => {
    if (folderMissing) workspace.goToLibrary(null, { replace: true })
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
      className="relative flex h-full min-h-0 flex-col"
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
      <div className="shrink-0 space-y-2 border-b border-line bg-surface px-3 py-2">
        <Breadcrumb folderId={folderId} mutations={mutations} dragging={dragging} onUpload={uploadFiles} />
        <div className="flex flex-wrap items-center gap-2">
          <Button icon={<FolderPlus className="size-4" />} onClick={() => setCreating(true)} disabled={!!query}>
            New folder
          </Button>
          <Button
            icon={mutations.upload.isPending ? <Spinner /> : <Upload className="size-4" />}
            onClick={() => fileInput.current?.click()}
            disabled={mutations.upload.isPending}
            title={ACCEPTED_TYPES_LABEL}
          >
            {mutations.upload.isPending ? 'Uploading…' : 'Upload'}
          </Button>
          <SearchBox query={query} scope={folderId == null ? null : currentName} />
          <Segmented
            value={view}
            onChange={setView}
            options={[
              { value: 'grid', label: 'Grid', icon: LayoutGrid },
              { value: 'list', label: 'List', icon: List },
            ]}
          />
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
          view={view}
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
        <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center bg-page/70 p-4">
          <div className="rounded-lg border-2 border-dashed border-accent bg-surface px-6 py-4 text-center text-sm shadow-sm">
            <Upload className="mx-auto mb-1 size-5 text-accent" />
            Drop to upload into <span className="font-medium">{currentName}</span>
            <p className="text-xs text-muted">or onto a folder</p>
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
    <nav aria-label="Folder path" className="flex min-w-0 flex-wrap items-center gap-0.5 text-sm">
      {crumbs.map((crumb, index) => {
        const last = index === crumbs.length - 1
        return (
          <span key={crumb.id ?? 'root'} className="flex min-w-0 items-center gap-0.5">
            {index > 0 && <ChevronRight className="size-3.5 shrink-0 text-muted" />}
            <button
              type="button"
              disabled={last}
              onClick={() => workspace.goToLibrary(crumb.id)}
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
              className={`max-w-48 truncate rounded px-1.5 py-0.5 ${
                last ? 'font-semibold text-ink' : 'text-muted hover:bg-sunken hover:text-ink'
              } ${over !== 'none' && over === crumb.id ? 'bg-accent-soft text-accent ring-1 ring-accent' : ''}`}
            >
              {crumb.name}
            </button>
          </span>
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
    <div className="relative min-w-40 flex-1">
      <Search className="absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted" />
      <input
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Escape') setDraft('')
        }}
        placeholder={scope ? `Search in ${scope}` : 'Search the project'}
        aria-label="Search"
        className="w-full rounded-md border border-line bg-surface py-1.5 pr-8 pl-8 text-sm outline-none focus:border-accent"
      />
      {draft && (
        <button
          type="button"
          aria-label="Clear search"
          onClick={() => setDraft('')}
          className="absolute top-1/2 right-1.5 -translate-y-1/2 rounded p-0.5 text-muted hover:text-ink"
        >
          <X className="size-3.5" />
        </button>
      )}
    </div>
  )
}

interface ListingProps {
  folderId: number | null
  view: View
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
  view,
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
  const items: Item[] = [
    ...(tree.children.get(folderId) ?? []).map((folder): Item => ({ kind: 'folder', folder })),
    ...documentsIn(documents, folderId).map((document): Item => ({ kind: 'file', document })),
  ]
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

  // Selection cannot outlive the items it points at.
  const present = new Set(keys)
  const liveSelected = new Set([...selected].filter((key) => present.has(key)))
  const selectedItems = items.filter((item) => liveSelected.has(itemKey(item)))

  useEffect(() => {
    if (cursor) list.current?.querySelector(`[data-key="${CSS.escape(cursor)}"]`)?.scrollIntoView({ block: 'nearest' })
  }, [cursor])

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
    if (item.kind === 'folder') workspace.goToLibrary(item.folder.id)
    else workspace.openDocument(item.document.id)
  }

  function remove(targets: Item[]) {
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

  // How many tiles share the first row; the grid wraps by width, so only the layout knows.
  function columns(): number {
    if (view === 'list') return 1
    const tiles = [...(list.current?.querySelectorAll<HTMLElement>('[data-key]') ?? [])]
    if (!tiles.length) return 1
    return Math.max(1, tiles.filter((tile) => tile.offsetTop === tiles[0].offsetTop).length)
  }

  function onKeyDown(event: KeyboardEvent) {
    if (renaming || creating) return
    const index = cursor ? keys.indexOf(cursor) : -1
    const moveTo = (next: number) => {
      if (!keys.length) return
      event.preventDefault()
      select(keys[Math.max(0, Math.min(keys.length - 1, next))], { ctrlKey: false, metaKey: false, shiftKey: event.shiftKey })
    }
    switch (event.key) {
      case 'ArrowDown':
        return moveTo(index < 0 ? 0 : index + columns())
      case 'ArrowUp':
        if (event.altKey) {
          event.preventDefault()
          return goUp()
        }
        return moveTo(index < 0 ? 0 : index - columns())
      case 'ArrowRight':
        if (view === 'grid') return moveTo(index + 1)
        return
      case 'ArrowLeft':
        if (view === 'grid') return moveTo(index - 1)
        return
      case 'Home':
        return moveTo(0)
      case 'End':
        return moveTo(keys.length - 1)
      case 'Enter': {
        const item = items[index]
        if (item) {
          event.preventDefault()
          open(item)
        }
        return
      }
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
    if (current) workspace.goToLibrary(current.parent_id)
  }

  function onDragStart(event: DragEvent, item: Item) {
    const key = itemKey(item)
    const dragged = liveSelected.has(key) ? selectedItems : [item]
    if (!liveSelected.has(key)) select(key)
    const payload: DragPayload = {
      folderIds: dragged.flatMap((i) => (i.kind === 'folder' ? [i.folder.id] : [])),
      documentIds: dragged.flatMap((i) => (i.kind === 'file' ? [i.document.id] : [])),
    }
    onDragChange(payload)
    event.dataTransfer.setData(ITEMS_TYPE, JSON.stringify(payload))
    event.dataTransfer.effectAllowed = 'move'
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
  }

  function onFolderDrop(event: DragEvent, folder: Folder) {
    setDropTarget(null)
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
      role="listbox"
      aria-multiselectable
      tabIndex={0}
      onKeyDown={onKeyDown}
      onContextMenu={(event) => onContextMenu(event)}
      onClick={(event) => {
        if (event.target === event.currentTarget) setSelected(new Set())
      }}
      className={`min-h-0 flex-1 overflow-y-auto p-2 outline-none ${
        view === 'grid' ? 'grid grid-cols-[repeat(auto-fill,minmax(7.5rem,1fr))] content-start gap-1' : ''
      }`}
    >
      {empty && (
        <div className="col-span-full">
          <EmptyState icon={<FolderIcon className="size-8" />} title={folderId == null ? 'This library is empty' : 'This folder is empty'}>
            Upload files or drop them here. Each one is parsed, outlined and indexed in the background.
            <br />
            {ACCEPTED_TYPES_LABEL}.
          </EmptyState>
        </div>
      )}
      {creating && (
        <GhostFolderRow
          view={view}
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
      {items.map((item) => {
        const key = itemKey(item)
        return (
          <Row
            key={key}
            item={item}
            view={view}
            selected={liveSelected.has(key)}
            isCursor={cursor === key}
            renaming={renaming === key}
            dropping={dropTarget === key}
            onSelect={(event) => select(key, event)}
            onOpen={() => open(item)}
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
            }}
            onDragOver={item.kind === 'folder' ? (event) => onFolderDragOver(event, item.folder) : undefined}
            onDragLeave={(event) => {
              if (!event.currentTarget.contains(event.relatedTarget as Node | null) && dropTarget === key) setDropTarget(null)
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

interface RowProps {
  item: Item
  view: View
  selected: boolean
  isCursor: boolean
  renaming: boolean
  dropping: boolean
  folderCount?: number
  onSelect: (event: MouseEvent) => void
  onOpen: () => void
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
  if (item.kind === 'folder') return <FolderIcon className={`${className} fill-accent/20 text-accent`} />
  return createElement(fileIcon(item.document), { className: `${className} text-muted` })
}

/** The tone and wording of a tile's status line; ready documents say nothing. */
function tileStatus(document: Document): { text: string; tone: string } | null {
  switch (document.status) {
    case 'failed':
      return { text: 'Failed', tone: 'text-bad' }
    case 'processing':
      return { text: 'Indexing…', tone: 'text-accent' }
    case 'queued':
      return { text: 'Queued', tone: 'text-muted' }
    default:
      return null
  }
}

function Row(props: RowProps) {
  const { item, view, selected, isCursor, renaming, dropping, folderCount } = props
  const name = itemName(item)
  const grid = view === 'grid'

  const handlers = {
    role: 'option' as const,
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
  const state = `${selected ? 'bg-accent-soft' : 'hover:bg-sunken'} ${isCursor ? 'ring-1 ring-accent/50' : ''} ${
    dropping ? 'bg-accent-soft ring-2 ring-accent' : ''
  }`
  const menuButton = (
    <IconButton
      label="More actions"
      className={`shrink-0 ${selected ? '' : 'opacity-0 group-hover:opacity-100 focus-visible:opacity-100'} ${
        grid ? 'absolute top-1 right-1 p-1!' : ''
      }`}
      onClick={(event) => {
        event.stopPropagation()
        props.onMenuButton(event.currentTarget)
      }}
    >
      <MoreHorizontal className="size-4" />
    </IconButton>
  )

  if (grid) {
    const status = item.kind === 'file' ? tileStatus(item.document) : null
    return (
      <div {...handlers} className={`group relative flex flex-col items-center gap-1.5 rounded-md px-2 pt-3 pb-2 text-sm select-none ${state}`}>
        <ItemIcon item={item} className="size-10 shrink-0" />
        <div className="w-full min-w-0 text-center">
          {renaming ? (
            <RenameInput value={name} isFile={item.kind === 'file'} onCommit={props.onRename} onCancel={props.onCancelRename} />
          ) : (
            <>
              <p className="line-clamp-2 leading-snug font-medium break-words" title={name}>
                {name}
              </p>
              <p className={`truncate text-xs ${status?.tone ?? 'text-muted'}`}>
                {item.kind === 'folder' ? `${folderCount ?? 0} item${folderCount === 1 ? '' : 's'}` : (status?.text ?? item.document.doc_type ?? '')}
              </p>
            </>
          )}
        </div>
        {menuButton}
      </div>
    )
  }

  return (
    <div {...handlers} className={`group flex items-center gap-2.5 rounded-md px-2 py-1.5 text-sm select-none ${state}`}>
      <ItemIcon item={item} className="size-4.5 shrink-0" />
      <div className="min-w-0 flex-1">
        {renaming ? (
          <RenameInput value={name} isFile={item.kind === 'file'} onCommit={props.onRename} onCancel={props.onCancelRename} />
        ) : (
          <>
            <p className="truncate font-medium" title={name}>
              {name}
            </p>
            <p className="truncate text-xs text-muted">
              {item.kind === 'folder'
                ? `${folderCount ?? 0} item${folderCount === 1 ? '' : 's'}`
                : (item.document.title ?? item.document.summary ?? '')}
            </p>
          </>
        )}
      </div>
      {item.kind === 'file' && (
        <div className="hidden shrink-0 text-xs sm:block">
          <IndexingStatus document={item.document} />
        </div>
      )}
      {item.kind === 'file' && item.document.doc_type && (
        <span className="hidden shrink-0 md:inline">
          <Chip>{item.document.doc_type}</Chip>
        </span>
      )}
      {menuButton}
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
      className="w-full rounded border border-accent bg-surface px-1.5 py-0.5 text-sm outline-none"
    />
  )
}

interface GhostFolderRowProps {
  view: View
  onCommit: (name: string) => void
  onCancel: () => void
}

/** The folder being created, in the place it will appear once named. */
function GhostFolderRow({ view, onCommit, onCancel }: GhostFolderRowProps) {
  const input = <RenameInput value="New folder" isFile={false} onCommit={onCommit} onCancel={onCancel} />
  if (view === 'grid') {
    return (
      <div className="flex flex-col items-center gap-1.5 rounded-md bg-accent-soft px-2 pt-3 pb-2 text-sm">
        <FolderIcon className="size-10 shrink-0 fill-accent/20 text-accent" />
        <div className="w-full min-w-0">{input}</div>
      </div>
    )
  }
  return (
    <div className="flex items-center gap-2.5 rounded-md bg-accent-soft px-2 py-1.5 text-sm">
      <FolderIcon className="size-4.5 shrink-0 fill-accent/20 text-accent" />
      <div className="min-w-0 flex-1">{input}</div>
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
      className="block w-full rounded-lg border border-line bg-surface px-4 py-3 text-left transition hover:border-accent"
    >
      <div className="flex items-baseline justify-between gap-3">
        <p className="min-w-0 truncate font-medium">
          {hit.document_title}
          {hit.doc_type && (
            <span className="ml-2 align-middle">
              <Chip>{hit.doc_type}</Chip>
            </span>
          )}
        </p>
        <span className="shrink-0 text-xs text-muted" title="How this passage was found">
          {found}
        </span>
      </div>
      {location && <p className="text-xs text-muted">{location}</p>}
      <p className="mt-1.5 text-sm">
        {hit.snippet.map((segment, index) =>
          segment.match ? <mark key={index}>{segment.text}</mark> : <span key={index}>{segment.text}</span>,
        )}
      </p>
    </button>
  )
}
