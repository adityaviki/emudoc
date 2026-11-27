import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ChevronRight,
  Copy,
  Download,
  FileUp,
  Folder as FolderIcon,
  FolderInput,
  FolderLock,
  FolderPlus,
  FolderUp,
  Lock,
  Pencil,
  RotateCcw,
  Trash2,
  Upload,
  X,
} from 'lucide-react'
import { useEffect, useId, useRef, useState } from 'react'
import { flushSync } from 'react-dom'
import { ApiError, api, mayChange, myFilePageUrl, myFileUrl } from '../api.ts'
import type { Document, MyFile, MyFileDetail, MyFiles, MyFilesAdded, MyFolder, UploadItem } from '../api.ts'
import { useMe } from '../auth.ts'
import { folderChoices } from '../library.ts'
import { Confirm } from '../components/Confirm.tsx'
import { PagesView } from '../components/DocumentViews.tsx'
import type { MenuItem } from '../components/Menu.tsx'
import { RenameInput } from '../components/RenameInput.tsx'
import { Button, FIELD, IconButton, Loading, Notice, SectionBar, SelectField, Spinner } from '../components/ui.tsx'
import { useContextMenu } from '../components/useContextMenu.tsx'
import { MY_FILES_TOGGLE, focusLibrary } from '../focus.ts'
import { useStatus } from '../hooks.ts'
import {
  COPY_WORDS,
  MY_FILE_ACCEPT,
  MY_FILE_NAME_CHARS,
  MY_FILE_NOTE,
  MY_FILE_POLL,
  TOP_OF_MY_FILES,
  addingWords,
  copiedWords,
  copyQuestion,
  following,
  heldWords,
  leftOutWords,
  myFileDeletionQuestion,
  myFileKey,
  myFileLine,
  myFileQuery,
  myFileUploads,
  myFileWords,
  myFilesCount,
  myFilesKey,
  myFilesTree,
  myFolderDeletionQuestion,
  myVersionName,
  myVersionWords,
  retryable,
  servable,
  unshownWords,
  waiting,
} from '../myFiles.ts'
import type { MyFilesTree, MyFolderNode } from '../myFiles.ts'
import { viewContent } from '../views.ts'
import { chosenFolder } from './dropped.ts'
import { myFileDownloadItem, myFileKind } from './files.ts'
import { listingsKey } from './listing.ts'
import { myFileTab, shownAfter, tabKey, useWorkspace } from './state.ts'
import { TAB_ID } from './TabStrip.tsx'
import { DEFAULT_LIMITS, NOTHING_TO_UPLOAD, batches } from './uploads.ts'

/** A refusal as the page shows it, in its own words. */
function Refusal({ error }: { error: Error }) {
  return <Notice tone="bad">{error.message}</Notice>
}

/** A file's icon in the colour the library gives a document of its kind. */
function KindIcon({ file }: { file: MyFile }) {
  const { icon: Icon, color } = myFileKind(file.kind)
  return <Icon aria-hidden className={`size-4 shrink-0 ${color}`} />
}

interface MyFilesSectionProps {
  open: boolean
  onToggle: () => void
  /** Whether the project's files are open below, to which the list leaves most of the pane, scrolling past its share. */
  capped: boolean
}

/** Where the browser keeps which of the member's folders of My files are open, a list of ids for each project. */
const OPEN_FOLDERS = 'emudoc.my-folders-open'

/** Which of the member's folders are open, kept in the browser for each project, as the library keeps its own. */
function useOpenFolders(projectId: number) {
  const key = `${OPEN_FOLDERS}.${projectId}`
  const [openFolders, setOpenFolders] = useState<Set<number>>(() => {
    try {
      const kept: unknown = JSON.parse(window.localStorage.getItem(key) ?? '[]')
      return new Set(Array.isArray(kept) ? kept.filter((id): id is number => typeof id === 'number') : [])
    } catch {
      return new Set()
    }
  })
  useEffect(() => {
    try {
      window.localStorage.setItem(key, JSON.stringify([...openFolders]))
    } catch {
      // Storage blocked: the folders stay as they are while the page is.
    }
  }, [key, openFolders])
  return [openFolders, setOpenFolders] as const
}

/** What a row of the list is, by the key its menu and the keyboard know it by: a file's `f<id>`, a folder's `d<id>`. */
const fileKey = (fileId: number) => `f${fileId}`
const folderKey = (folderId: number) => `d${folderId}`

/**
 * The member's own files in the project, in the library under its search and above the project's files: the Word
 * documents and Excel workbooks they keep here, which nobody else sees, in folders of their own if they like (the folders
 * by name, then the files, the one changed last first), each a row as the library's are and opening, as theirs do, in a
 * tab of the middle pane. Its heading opens and closes the list; what the project files' right-click offers, it offers
 * for its own: on its empty space or its heading a new folder, files uploaded and a folder uploaded whole, on a folder
 * the same into it with its rename and deletion, and on a file what may be done to it.
 */
export function MyFilesSection({ open, onToggle, capped }: MyFilesSectionProps) {
  const workspace = useWorkspace()
  const { project } = workspace
  const changes = mayChange(useMe().role)
  const limits = useStatus().data?.uploads ?? DEFAULT_LIMITS
  const queryClient = useQueryClient()
  const filesInput = useRef<HTMLInputElement>(null)
  const folderInput = useRef<HTMLInputElement | null>(null)
  // The folder the pickers upload into, null for the top.
  const into = useRef<number | null>(null)
  const versionInput = useRef<HTMLInputElement>(null)
  // The file whose new version the file picker is choosing.
  const versionOf = useRef<MyFile | null>(null)
  const body = useId()
  const rowId = (key: string) => `${body}-${key}`
  const menu = useContextMenu()
  // The row whose menu is open, drawn as the library's is.
  const [menuFor, setMenuFor] = useState<string | null>(null)
  const [openFolders, setOpenFolders] = useOpenFolders(project.id)
  const [renaming, setRenaming] = useState<string | null>(null)
  // The folder a new folder is being named in, null for the top; undefined while none is.
  const [creating, setCreating] = useState<number | null | undefined>(undefined)
  // Enter or Escape ended a name being typed: the keyboard goes back to this element of the list once it is drawn.
  const backTo = useRef<string | null>(null)
  // A new folder named by Enter, whose row takes the keyboard once made.
  const named = useRef(false)
  const [deleting, setDeleting] = useState<MyFile | null>(null)
  const [deletingFolder, setDeletingFolder] = useState<MyFolderNode | null>(null)
  const [copying, setCopying] = useState<MyFile | null>(null)
  const [moving, setMoving] = useState<MyFile | null>(null)
  const [refused, setRefused] = useState<Error | null>(null)
  // What a choice of files or a folder held that My files does not keep, by their paths.
  const [leftOut, setLeftOut] = useState<string[]>([])
  useEffect(() => {
    if (renaming != null || creating !== undefined || backTo.current == null) return
    const element = window.document.getElementById(backTo.current)
    if (!element) return
    element.focus()
    backTo.current = null
  })
  const files = useQuery({
    queryKey: myFilesKey(project.id),
    queryFn: () => api.myFiles(project.id),
    refetchInterval: (query) => (query.state.data && following(query.state.data) ? MY_FILE_POLL : false),
  })
  const listed = files.data?.files ?? []
  const folders = files.data?.folders ?? []
  const tree = myFilesTree({ files: listed, folders })

  /** Open the folder and every folder it is in, so that what goes into it is seen. */
  function reveal(folderId: number | null) {
    const parents = new Map(folders.map((folder) => [folder.id, folder.parent_id]))
    const shown: number[] = []
    for (let at = folderId; at != null && !shown.includes(at); at = parents.get(at) ?? null) shown.push(at)
    if (shown.length) setOpenFolders((current) => new Set([...current, ...shown]))
  }

  const add = useMutation({
    // In as few requests as the deployment's limits allow, since each counts against the member's uploads a minute.
    mutationFn: async ({ items, folderId }: { items: UploadItem[]; folderId: number | null }) => {
      const answered: MyFilesAdded = { files: [], rejected: [] }
      for (const batch of batches(items, limits)) {
        const answer = await api.addMyFiles(project.id, batch, folderId)
        answered.files.push(...answer.files)
        answered.rejected.push(...answer.rejected)
      }
      return answered
    },
    onMutate: () => setRefused(null),
    onSuccess: (answered, { items, folderId }) => {
      for (const added of answered.files) queryClient.setQueryData(myFileKey(added.id), added)
      reveal(folderId)
      // One file chosen opens, as a file added always did; several stay in the list.
      if (items.length === 1 && answered.files.length === 1) workspace.openMyFile(answered.files[0].id)
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: myFilesKey(project.id) })
    },
  })
  const kept = (detail: MyFileDetail) => {
    queryClient.setQueryData(myFileKey(detail.id), detail)
    void queryClient.invalidateQueries({ queryKey: myFilesKey(project.id) })
  }
  const listedAgain = () => void queryClient.invalidateQueries({ queryKey: myFilesKey(project.id) })
  const rename = useMutation({
    mutationFn: ({ file, name }: { file: MyFile; name: string }) => api.renameMyFile(file.id, name),
    onMutate: () => setRefused(null),
    onSuccess: kept,
    onError: (error) => setRefused(error),
  })
  const move = useMutation({
    mutationFn: ({ file, folderId }: { file: MyFile; folderId: number | null }) => api.moveMyFile(file.id, folderId),
    onMutate: () => setRefused(null),
    onSuccess: (detail) => {
      kept(detail)
      reveal(detail.folder_id)
    },
    onError: (error) => setRefused(error),
  })
  const makeFolder = useMutation({
    mutationFn: ({ name, parentId }: { name: string; parentId: number | null }) => api.createMyFolder(project.id, name, parentId),
    onMutate: () => setRefused(null),
    onSuccess: (made) => {
      queryClient.setQueryData<MyFiles>(myFilesKey(project.id), (current) => current && { ...current, folders: [...current.folders, made] })
      listedAgain()
      reveal(made.parent_id)
      if (named.current) backTo.current = rowId(folderKey(made.id))
    },
    onError: (error) => setRefused(error),
    onSettled: () => {
      named.current = false
    },
  })
  const renameFolder = useMutation({
    mutationFn: ({ folder, name }: { folder: MyFolder; name: string }) => api.renameMyFolder(folder.id, name),
    onMutate: () => setRefused(null),
    onSuccess: (renamed) => {
      queryClient.setQueryData<MyFiles>(
        myFilesKey(project.id),
        (current) => current && { ...current, folders: current.folders.map((one) => (one.id === renamed.id ? renamed : one)) },
      )
      listedAgain()
    },
    onError: (error) => setRefused(error),
  })
  const newVersion = useMutation({
    mutationFn: ({ file, picked }: { file: MyFile; picked: File }) => api.addMyFileVersion(file.id, picked, file.latest.number),
    onMutate: () => setRefused(null),
    onSuccess: (detail) => {
      kept(detail)
      workspace.openMyFile(detail.id)
    },
    onError: async (error, { file }) => {
      setRefused(error)
      // Another version came meanwhile: the list shows it, and the member uploads theirs again after it.
      if (error instanceof ApiError && error.status === 409) {
        await queryClient.invalidateQueries({ queryKey: myFileKey(file.id) })
        await queryClient.invalidateQueries({ queryKey: myFilesKey(project.id) })
      }
    },
  })
  const copy = useMutation({
    mutationFn: ({ file, choice }: { file: MyFile; choice: CopyChoice }) => api.copyMyFile(file.id, choice),
    onMutate: () => setRefused(null),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: listingsKey(project.id) })
      void queryClient.invalidateQueries({ queryKey: ['folders', project.id] })
      void queryClient.invalidateQueries({ queryKey: ['project', project.id] })
    },
    onError: (error) => setRefused(error),
  })
  /** Close the tabs of files that are gone and forget them, the keyboard on from the heading of My files, whose list held them. */
  function gone(fileIds: number[], close: () => void) {
    listedAgain()
    // The question closed first, since the page beneath it takes no focus while it is open.
    flushSync(close)
    workspace.closeTabs(fileIds.map(myFileTab))
    for (const fileId of fileIds) queryClient.removeQueries({ queryKey: myFileKey(fileId) })
    window.document.getElementById(MY_FILES_TOGGLE)?.focus()
  }
  const remove = useMutation({
    mutationFn: (file: MyFile) => api.deleteMyFile(file.id),
    onMutate: () => setRefused(null),
    onSuccess: (_, file) => gone([file.id], () => setDeleting(null)),
    onError: (error) => {
      setDeleting(null)
      setRefused(error)
    },
  })
  const removeFolder = useMutation({
    mutationFn: (node: MyFolderNode) => api.deleteMyFolder(node.folder.id),
    onMutate: () => setRefused(null),
    onSuccess: (_, node) => {
      const inside = (one: MyFolderNode): number[] => [...one.files.map((file) => file.id), ...one.folders.flatMap(inside)]
      gone(inside(node), () => setDeletingFolder(null))
    },
    onError: (error) => {
      setDeletingFolder(null)
      setRefused(error)
    },
  })

  /** Send what was chosen: the Word documents and workbooks into the folder the picker was opened for, the rest said. */
  function send(items: UploadItem[]) {
    const { items: sent, left } = myFileUploads(items)
    setLeftOut(left)
    if (sent.length) add.mutate({ items: sent, folderId: into.current })
    else if (!left.length) setRefused(new Error(NOTHING_TO_UPLOAD))
  }

  function pick(input: HTMLInputElement | null, folderId: number | null) {
    into.current = folderId
    input?.click()
  }

  function pickVersion(file: MyFile) {
    const input = versionInput.current
    if (!input) return
    versionOf.current = file
    input.accept = file.kind === 'xlsx' ? '.xlsx' : '.docx'
    input.click()
  }

  function startFolder(parentId: number | null) {
    if (!open) onToggle()
    reveal(parentId)
    setCreating(parentId)
  }

  /** What adds to a place of My files, its top or a folder: a folder made in it, files uploaded into it, a folder uploaded whole. */
  function addingMenu(folderId: number | null): MenuItem[] {
    return [
      { label: 'New folder', icon: FolderPlus, onSelect: () => startFolder(folderId) },
      { label: 'Upload files', icon: Upload, disabled: add.isPending, onSelect: () => pick(filesInput.current, folderId) },
      { label: 'Upload a folder', icon: FolderUp, disabled: add.isPending, onSelect: () => pick(folderInput.current, folderId) },
    ]
  }

  /** What a file's row offers, as the project's files' menu does: what My files can do to one of its files. */
  function fileMenu(file: MyFile): MenuItem[] {
    return [
      { label: 'Open', onSelect: () => workspace.openMyFile(file.id) },
      myFileDownloadItem(file),
      { label: 'Rename', icon: Pencil, shortcut: 'F2', onSelect: () => setRenaming(fileKey(file.id)) },
      { label: 'Move to…', icon: FolderInput, disabled: folders.length === 0 && file.folder_id == null, onSelect: () => setMoving(file) },
      { label: 'Upload a new version…', icon: FileUp, disabled: newVersion.isPending, onSelect: () => pickVersion(file) },
      // Whoever may change the project's files, once the newest version may be downloaded.
      ...(changes
        ? [{ label: 'Copy to project files…', icon: Copy, disabled: !servable(file.latest), onSelect: () => setCopying(file) }]
        : []),
      'separator',
      { label: 'Delete', icon: Trash2, shortcut: 'Del', danger: true, onSelect: () => setDeleting(file) },
    ]
  }

  /** What a folder's row offers: what adds to it, and its rename and deletion. */
  function folderMenu(node: MyFolderNode): MenuItem[] {
    return [
      ...addingMenu(node.folder.id),
      'separator',
      { label: 'Rename', icon: Pencil, shortcut: 'F2', onSelect: () => setRenaming(folderKey(node.folder.id)) },
      { label: 'Delete', icon: Trash2, shortcut: 'Del', danger: true, onSelect: () => setDeletingFolder(node) },
    ]
  }

  function renamed(file: MyFile, name: string) {
    const trimmed = name.trim()
    setRenaming(null)
    if (trimmed && trimmed !== file.name) rename.mutate({ file, name: trimmed })
  }

  function renamedFolder(folder: MyFolder, name: string) {
    const tidied = name.split(/\s+/).filter(Boolean).join(' ')
    setRenaming(null)
    if (tidied && tidied !== folder.name) renameFolder.mutate({ folder, name: tidied })
  }

  function toggleFolder(folderId: number) {
    setOpenFolders((current) => {
      const next = new Set(current)
      if (!next.delete(folderId)) next.add(folderId)
      return next
    })
  }

  /** The folder being made, named where it will appear once made. */
  function newFolderRow(parentId: number | null) {
    return (
      <li>
        <div className="flex h-8 items-center gap-2 rounded-md bg-accent-soft px-1.5">
          <span className="w-3.5 shrink-0" />
          <FolderIcon aria-hidden className="size-4 shrink-0 fill-accent/15 text-accent" />
          <div className="min-w-0 flex-1">
            <RenameInput
              value="New folder"
              isFile={false}
              onCommit={(name) => {
                setCreating(undefined)
                if (name.trim()) makeFolder.mutate({ name, parentId })
              }}
              onCancel={() => setCreating(undefined)}
              onKeys={() => {
                named.current = true
                // Escape gives the keyboard back to the folder it was opened in, or the heading; Enter, to the new folder once made.
                backTo.current = parentId == null ? MY_FILES_TOGGLE : rowId(folderKey(parentId))
              }}
            />
          </div>
        </div>
      </li>
    )
  }

  /** The rows of a place of My files: a folder being made in it, its folders, then its files. */
  function rows(place: MyFilesTree, parentId: number | null) {
    return (
      <>
        {creating === parentId && newFolderRow(parentId)}
        {place.folders.map((node) => folderRow(node))}
        {place.files.map((file) => (
          <li key={fileKey(file.id)}>{renaming === fileKey(file.id) ? renameFileRow(file) : fileRow(file)}</li>
        ))}
      </>
    )
  }

  function folderRow(node: MyFolderNode) {
    const { folder } = node
    const key = folderKey(folder.id)
    const expanded = openFolders.has(folder.id)
    const inside = `${rowId(key)}-in`
    const empty = node.folders.length === 0 && node.files.length === 0 && creating !== folder.id
    return (
      <li key={key}>
        {renaming === key ? (
          <div className="flex h-8 items-center gap-2 rounded-md bg-accent-soft px-1.5">
            <span className="w-3.5 shrink-0" />
            <FolderIcon aria-hidden className="size-4 shrink-0 fill-accent/15 text-accent" />
            <div className="min-w-0 flex-1">
              <RenameInput
                value={folder.name}
                isFile={false}
                onCommit={(name) => renamedFolder(folder, name)}
                onCancel={() => setRenaming(null)}
                onKeys={() => {
                  backTo.current = rowId(key)
                }}
              />
            </div>
          </div>
        ) : (
          <button
            id={rowId(key)}
            type="button"
            aria-expanded={expanded}
            aria-controls={inside}
            onClick={() => toggleFolder(folder.id)}
            onContextMenu={(event) => {
              setMenuFor(key)
              menu.open(event, folderMenu(node))
            }}
            onKeyDown={(event) => {
              if (event.key === 'F2') {
                event.preventDefault()
                setRenaming(key)
              } else if (event.key === 'Delete') {
                event.preventDefault()
                setDeletingFolder(node)
              }
            }}
            className={`flex h-8 w-full items-center gap-2 rounded-md px-1.5 text-left text-[13px] transition hover:bg-sunken ${
              menu.isOpen && menuFor === key ? 'bg-sunken' : ''
            }`}
          >
            <ChevronRight aria-hidden className={`size-3.5 shrink-0 text-muted transition-transform ${expanded ? 'rotate-90' : ''}`} />
            <FolderIcon aria-hidden className="size-4 shrink-0 fill-accent/15 text-accent" />
            <span className="min-w-0 flex-1 truncate">{folder.name}</span>
          </button>
        )}
        {/* Drawn while closed too, hidden, so that the button always names what it opens. */}
        <ul id={inside} aria-label={folder.name} hidden={!expanded} className="pl-4">
          {expanded && rows(node, folder.id)}
          {expanded && empty && <li className="flex h-7 items-center pl-7 text-xs text-muted">Empty</li>}
        </ul>
      </li>
    )
  }

  function renameFileRow(file: MyFile) {
    return (
      <div className="flex h-8 items-center gap-2 rounded-md bg-accent-soft px-1.5">
        <KindIcon file={file} />
        <div className="min-w-0 flex-1">
          <RenameInput
            value={file.name}
            isFile
            maxLength={MY_FILE_NAME_CHARS}
            onCommit={(name) => renamed(file, name)}
            onCancel={() => setRenaming(null)}
            onKeys={() => {
              backTo.current = rowId(fileKey(file.id))
            }}
          />
        </div>
      </div>
    )
  }

  function fileRow(file: MyFile) {
    const key = fileKey(file.id)
    return (
      <button
        id={rowId(key)}
        type="button"
        title={myFileWords(file)}
        onClick={() => workspace.openMyFile(file.id)}
        onContextMenu={(event) => {
          setMenuFor(key)
          menu.open(event, fileMenu(file))
        }}
        onKeyDown={(event) => {
          if (event.key === 'F2') {
            event.preventDefault()
            setRenaming(key)
          } else if (event.key === 'Delete') {
            event.preventDefault()
            setDeleting(file)
          }
        }}
        className={`flex h-8 w-full items-center gap-2 rounded-md px-1.5 text-left text-[13px] transition hover:bg-sunken ${
          menu.isOpen && menuFor === key ? 'bg-sunken' : ''
        }`}
      >
        <KindIcon file={file} />
        <span className="min-w-0 flex-1 truncate">{file.name}</span>
        {waiting(file.latest) && <Spinner className="size-3" />}
        {/* A narrow pane keeps the names, the rest in the row's title. */}
        <span className="hidden shrink-0 text-xs text-muted @xs:inline">{myFileLine(file)}</span>
      </button>
    )
  }

  const nothing = files.data != null && listed.length === 0 && folders.length === 0 && creating !== null

  return (
    <>
      <section
        aria-labelledby={MY_FILES_TOGGLE}
        className={`@container flex min-h-0 flex-col ${capped ? 'max-h-[35%] shrink-0' : ''}`}
        // A right-click on the heading or the list's empty space, or the menu key on the heading, offers what adds to the
        // top of My files; a row offers its own, and a name being typed the browser's.
        onContextMenu={(event) => {
          if (event.target instanceof HTMLInputElement) return
          setMenuFor(null)
          menu.open(event, addingMenu(null))
        }}
      >
        <SectionBar id={MY_FILES_TOGGLE} controls={body} open={open} onToggle={onToggle} icon={<FolderLock />} title="My files">
          {files.data && listed.length > 0 && (
            <span className="mr-1 hidden shrink-0 text-xs text-muted @3xs:inline">{myFilesCount(listed.length, files.data.limit)}</span>
          )}
          {/* A phone or a tablet, which opens no menu by a right-click, keeps a button for uploading, as the project files do. */}
          <Button
            variant="ghost"
            icon={add.isPending ? <Spinner /> : <Upload className="size-4" />}
            disabled={add.isPending}
            onClick={() => pick(filesInput.current, null)}
            title="Upload Word documents or Excel workbooks of your own from this device"
            aria-label={add.isPending ? 'Uploading…' : 'Upload files'}
            className="h-7 px-1.5 pointer-fine:hidden"
          />
          <input
            ref={filesInput}
            type="file"
            name="my-files"
            multiple
            hidden
            accept={MY_FILE_ACCEPT}
            onChange={(event) => {
              const picked = [...(event.target.files ?? [])]
              event.target.value = ''
              if (picked.length) send(picked.map((file) => ({ file, folder: '' })))
            }}
          />
          {/* A folder's files, each with its path from the folder chosen, which the browser asks the member to allow. */}
          <input
            ref={(input) => {
              folderInput.current = input
              if (input) input.webkitdirectory = true
            }}
            type="file"
            name="my-folder"
            hidden
            onChange={(event) => {
              const picked = [...(event.target.files ?? [])]
              event.target.value = ''
              if (picked.length) send(chosenFolder(picked))
            }}
          />
          <input
            ref={versionInput}
            type="file"
            name="my-file-next-version"
            hidden
            onChange={(event) => {
              const picked = event.target.files?.[0]
              const file = versionOf.current
              event.target.value = ''
              if (picked && file) newVersion.mutate({ file, picked })
            }}
          />
        </SectionBar>
        <div id={body} hidden={!open} className="min-h-0 overflow-y-auto px-2 pb-2">
          <div className="space-y-2 pb-1 empty:hidden">
            {add.isPending && !add.isPaused && (
              <Notice tone="info">
                <span role="status" className="flex items-center gap-2">
                  <Spinner className="size-3" />
                  {addingWords(add.variables.items.length)}
                </span>
              </Notice>
            )}
            {add.isPaused && <Notice tone="info">The files are added once you are back online.</Notice>}
            {add.isError && <Refusal error={add.error} />}
            {add.data && add.data.rejected.length > 0 && (
              <Notice>
                <div className="flex items-start justify-between gap-3">
                  <ul>
                    {add.data.rejected.map((one) => (
                      <li key={`${one.index}-${one.filename}`}>
                        <span className="font-medium">{one.filename}</span>: {one.reason}
                      </li>
                    ))}
                  </ul>
                  <IconButton label="Dismiss" onClick={() => add.reset()} className="p-0.5!">
                    <X className="size-3.5" />
                  </IconButton>
                </div>
              </Notice>
            )}
            {leftOut.length > 0 && (
              <Notice tone="info">
                <div className="flex items-start justify-between gap-3">
                  <span>{leftOutWords(leftOut)}</span>
                  <IconButton label="Dismiss" onClick={() => setLeftOut([])} className="p-0.5!">
                    <X className="size-3.5" />
                  </IconButton>
                </div>
              </Notice>
            )}
            {refused && <Refusal error={refused} />}
            {copy.data && copy.variables && (
              <Notice tone="info">
                <div role="status" className="flex flex-wrap items-center justify-between gap-2">
                  <span>{copiedWords(copy.data, 'as_version' in copy.variables.choice)}</span>
                  <span className="flex items-center gap-1">
                    <Button onClick={() => workspace.openDocument(copy.data.id)}>Open it</Button>
                    <IconButton label="Dismiss" onClick={() => copy.reset()} className="p-0.5!">
                      <X className="size-3.5" />
                    </IconButton>
                  </span>
                </div>
              </Notice>
            )}
            {files.isError && <Notice tone="bad">Could not load your files: {files.error.message}</Notice>}
          </div>
          {files.isPending && (
            <div className="flex justify-center py-2">
              <Spinner />
            </div>
          )}
          {nothing && (
            // Said in a box of the project files' kind, so that the section opened is not taken for one closed.
            <div className="flex items-center justify-center gap-2 rounded-lg border border-dashed border-ink/20 px-3 py-4 text-[13px] text-muted">
              <FolderLock aria-hidden className="size-4 shrink-0" />
              No files yet
            </div>
          )}
          {files.data && !nothing && <ul aria-label="My files">{rows(tree, null)}</ul>}
        </div>
      </section>
      {menu.element}
      {deleting && (
        <Confirm
          title="Delete this file?"
          body={myFileDeletionQuestion(deleting)}
          action="Delete for good"
          disabled={remove.isPending}
          onConfirm={() => remove.mutate(deleting)}
          onClose={() => setDeleting(null)}
        />
      )}
      {deletingFolder && (
        <Confirm
          {...myFolderDeletionQuestion(deletingFolder)}
          disabled={removeFolder.isPending}
          onConfirm={() => removeFolder.mutate(deletingFolder)}
          onClose={() => setDeletingFolder(null)}
        />
      )}
      {moving && (
        <MoveDialog
          file={moving}
          folders={folders}
          onMove={(folderId) => {
            if (folderId !== moving.folder_id) move.mutate({ file: moving, folderId })
          }}
          onClose={() => setMoving(null)}
        />
      )}
      {copying && <CopyDialog file={copying} onCopy={(choice) => copy.mutate({ file: copying, choice })} onClose={() => setCopying(null)} />}
    </>
  )
}

/** "Move to…" from a file's row: the folder of My files it goes into chosen in a dialog, the top first, then each folder by its path. */
function MoveDialog({ file, folders, onMove, onClose }: { file: MyFile; folders: MyFolder[]; onMove: (folderId: number | null) => void; onClose: () => void }) {
  const [target, setTarget] = useState<number | null>(file.folder_id)
  return (
    <Confirm
      title={`Move “${file.name}”`}
      body="Choose the folder of My files it goes into."
      action="Move"
      danger={false}
      onConfirm={() => onMove(target)}
      onClose={onClose}
    >
      <SelectField label="Folder" data-autofocus value={target ?? ''} onChange={(event) => setTarget(event.target.value ? Number(event.target.value) : null)}>
        <option value="">{TOP_OF_MY_FILES}</option>
        {folderChoices(folders).map((choice) => (
          <option key={choice.id} value={choice.id}>
            {choice.label}
          </option>
        ))}
      </SelectField>
    </Confirm>
  )
}

/**
 * One of the member's files, in a tab of the middle pane beside the project's documents, which says it is theirs alone:
 * its pages as they look, its versions to open and download, a new version uploaded from this computer, and the file
 * renamed, deleted (its tab closed with it) or copied into the project's library.
 */
export function MyFileView({ fileId }: { fileId: number }) {
  const workspace = useWorkspace()
  const { project } = workspace
  const changes = mayChange(useMe().role)
  const views = useStatus().data?.views ?? false
  const queryClient = useQueryClient()
  const heading = useRef<HTMLHeadingElement>(null)
  const picker = useRef<HTMLInputElement>(null)
  const [renaming, setRenaming] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [copying, setCopying] = useState(false)
  // The version shown, null for the newest.
  const [chosen, setChosen] = useState<number | null>(null)
  const [refused, setRefused] = useState<Error | null>(null)
  useEffect(() => {
    if (window.document.activeElement === window.document.body) heading.current?.focus()
  }, [])
  const file = useQuery(myFileQuery(fileId))
  const data = file.data
  const kept = (detail: MyFileDetail) => {
    queryClient.setQueryData(myFileKey(fileId), detail)
    void queryClient.invalidateQueries({ queryKey: myFilesKey(project.id) })
  }
  const upload = useMutation({
    mutationFn: (picked: File) => api.addMyFileVersion(fileId, picked, data?.latest.number ?? 1),
    onMutate: () => setRefused(null),
    onSuccess: (detail) => {
      kept(detail)
      setChosen(null)
    },
    onError: async (error) => {
      setRefused(error)
      // Another version came meanwhile: the page shows it, and the member uploads theirs again after it.
      if (error instanceof ApiError && error.status === 409) await queryClient.invalidateQueries({ queryKey: myFileKey(fileId) })
    },
  })
  const rename = useMutation({
    mutationFn: (name: string) => api.renameMyFile(fileId, name),
    onMutate: () => setRefused(null),
    onSuccess: (detail) => {
      kept(detail)
      setRenaming(null)
      heading.current?.focus()
    },
    onError: (error) => setRefused(error),
  })
  const retry = useMutation({
    mutationFn: (number: number) => api.showMyFile(fileId, number),
    onMutate: () => setRefused(null),
    onSuccess: kept,
    onError: (error) => setRefused(error),
  })
  const remove = useMutation({
    mutationFn: () => api.deleteMyFile(fileId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: myFilesKey(project.id) })
      // The tab goes with the file, and the keyboard on from the tab shown next, else from the heading of My files, whose
      // list held it, or the library while a search shows no My files: the question closed first, since the page beneath
      // it takes no focus while it is open.
      const key = myFileTab(fileId)
      const next = shownAfter(workspace.openTabs, key, new Set([key]))
      flushSync(() => setDeleting(false))
      workspace.closeTabs([key])
      queryClient.removeQueries({ queryKey: myFileKey(fileId) })
      const shown = window.document.getElementById(next ? `${TAB_ID}${tabKey(next)}` : MY_FILES_TOGGLE)
      if (shown) shown.focus()
      else focusLibrary()
    },
    onError: (error) => setRefused(error),
  })
  const newest = data?.latest.number ?? 1
  const version = data?.history.find((one) => one.number === (chosen ?? newest)) ?? data?.latest
  const unshown = version ? unshownWords(version, views) : null

  return (
    <div className="flex h-full min-h-0 flex-col bg-page">
      {/* As a document's tab heads it, with whose the file is, since its tab sits among the project's documents'. */}
      <header className="shrink-0 border-b border-line bg-surface px-4 py-2.5">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 ref={heading} tabIndex={-1} title={data?.name} className="truncate text-[15px] font-semibold tracking-tight">
              {data?.name ?? 'File'}
            </h2>
            <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
              <span className="flex items-center gap-1">
                <Lock aria-hidden className="size-3.5 shrink-0" />
                {MY_FILE_NOTE}
              </span>
              {data && <span>{myFileWords(data)}</span>}
            </div>
          </div>
          {data && (
            <div className="flex shrink-0 items-center gap-1">
              <IconButton label="Rename this file" onClick={() => setRenaming(data.name)}>
                <Pencil className="size-4" />
              </IconButton>
              <IconButton label="Delete this file" danger disabled={remove.isPending} onClick={() => setDeleting(true)}>
                <Trash2 className="size-4" />
              </IconButton>
            </div>
          )}
        </div>
      </header>
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3">
        {file.isPending && <Loading />}
        {file.isError && <Notice tone="bad">Could not load the file: {file.error.message}</Notice>}
        {data && version && (
          <>
            {renaming != null && (
              <form
                className="flex items-end gap-2"
                onSubmit={(event) => {
                  event.preventDefault()
                  if (renaming.trim()) rename.mutate(renaming.trim())
                }}
              >
                <label className="min-w-0 flex-1 text-xs font-medium text-muted">
                  Name
                  <input
                    className={`${FIELD} mt-1`}
                    value={renaming}
                    maxLength={MY_FILE_NAME_CHARS}
                    // The form opened at the member's press: the keyboard goes to what it asks.
                    autoFocus
                    onChange={(event) => setRenaming(event.target.value)}
                  />
                </label>
                <Button type="submit" variant="primary" disabled={rename.isPending || !renaming.trim()}>
                  Save
                </Button>
                <Button onClick={() => setRenaming(null)}>Cancel</Button>
              </form>
            )}
            {data.origin_name && <p className="text-xs text-muted">Copied from “{data.origin_name}” in the library.</p>}
            <div className="flex flex-wrap gap-2">
              {servable(data.latest) && (
                <a
                  href={myFileUrl(fileId, data.latest.number)}
                  download
                  className="inline-flex h-8 items-center gap-1.5 rounded-md border border-line bg-surface px-3 text-[13px] text-ink transition hover:bg-sunken"
                >
                  <Download aria-hidden className="size-4" />
                  Download
                </a>
              )}
              <Button icon={<FileUp className="size-4" />} disabled={upload.isPending} onClick={() => picker.current?.click()}>
                {upload.isPending ? 'Uploading…' : 'Upload a new version'}
              </Button>
              <input
                ref={picker}
                type="file"
                name="my-file-version"
                hidden
                accept={data.kind === 'xlsx' ? '.xlsx' : '.docx'}
                onChange={(event) => {
                  const picked = event.target.files?.[0]
                  event.target.value = ''
                  if (picked) upload.mutate(picked)
                }}
              />
              {changes && servable(data.latest) && (
                <Button icon={<Copy className="size-4" />} onClick={() => setCopying((open) => !open)} aria-expanded={copying}>
                  Copy to project files
                </Button>
              )}
            </div>
            {heldWords(data.latest) && <p className="text-xs text-muted">{heldWords(data.latest)}</p>}
            {copying && <CopyForm file={data} onClose={() => setCopying(false)} />}
            {refused && <Refusal error={refused} />}

            <section aria-labelledby={`my-file-${fileId}-pages`} className="space-y-2">
              <h3 id={`my-file-${fileId}-pages`} className="text-xs font-semibold text-muted">
                {myVersionName(version, newest)}
              </h3>
              {unshown ? (
                <Notice tone={version.scan === 'flagged' ? 'bad' : 'info'}>
                  <div className="flex items-start justify-between gap-3">
                    <p className="flex items-center gap-1.5">
                      {waiting(version) && <Spinner className="size-3" />}
                      {unshown}
                    </p>
                    {retryable(version) && (
                      <Button icon={<RotateCcw className="size-4" />} disabled={retry.isPending} onClick={() => retry.mutate(version.number)}>
                        Try again
                      </Button>
                    )}
                  </div>
                </Notice>
              ) : (
                <div className="overflow-hidden rounded-md border border-line">
                  <PagesView pageUrl={(page) => myFilePageUrl(fileId, version.number, page)} content={viewContent(version.view_page_sizes)} focus={null} />
                </div>
              )}
            </section>

            <section aria-labelledby={`my-file-${fileId}-versions`} className="space-y-2">
              <h3 id={`my-file-${fileId}-versions`} className="text-xs font-semibold text-muted">
                Versions
              </h3>
              <ul aria-label="Versions" className="space-y-1.5">
                {data.history.map((one) => (
                  <li key={one.number} className="flex items-start gap-2 rounded-md border border-line bg-surface px-3 py-2">
                    <div className="min-w-0 flex-1">
                      <p className="text-[13px] font-medium">{myVersionName(one, newest)}</p>
                      <p className="text-xs break-words text-muted">{myVersionWords(one)}</p>
                    </div>
                    {one.number !== version.number && (
                      <Button onClick={() => setChosen(one.number === newest ? null : one.number)}>Show</Button>
                    )}
                    {servable(one) && (
                      <a
                        href={myFileUrl(fileId, one.number)}
                        download
                        aria-label={`Download version ${one.number}`}
                        className="inline-flex h-8 items-center rounded-md border border-line bg-surface px-2 text-ink transition hover:bg-sunken"
                      >
                        <Download aria-hidden className="size-4" />
                      </a>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          </>
        )}
      </div>
      {deleting && data && (
        <Confirm
          title="Delete this file?"
          body={myFileDeletionQuestion(data)}
          action="Delete for good"
          disabled={remove.isPending}
          onConfirm={() => remove.mutate()}
          onClose={() => setDeleting(false)}
        />
      )}
    </div>
  )
}

/** Where a copy goes in the project's files: a folder for a new document, or the next version of the document it came from. */
type CopyChoice = { as_version: true } | { folder_id: number | null }

interface CopyChoicesProps {
  file: MyFile
  asVersion: boolean
  onAsVersion: (asVersion: boolean) => void
  folder: string
  onFolder: (folder: string) => void
}

/** The choice of where a copy goes: as a new version of its origin while it has one, else as a new document in a folder. */
function CopyChoices({ file, asVersion, onAsVersion, folder, onFolder }: CopyChoicesProps) {
  const { folders } = useWorkspace()
  const id = useId()
  return (
    <div className="space-y-2">
      {file.origin_name && (
        <fieldset className="space-y-1 text-[13px]">
          <legend className="sr-only">As what</legend>
          <label className="flex items-center gap-2">
            <input type="radio" name={`${id}-as`} checked={asVersion} onChange={() => onAsVersion(true)} />
            As a new version of “{file.origin_name}”
          </label>
          <label className="flex items-center gap-2">
            <input type="radio" name={`${id}-as`} checked={!asVersion} onChange={() => onAsVersion(false)} />
            As a new document
          </label>
        </fieldset>
      )}
      {!asVersion && (
        <SelectField label="Folder" value={folder} onChange={(event) => onFolder(event.target.value)}>
          <option value="">All files, at the top</option>
          {folderChoices(folders).map((choice) => (
            <option key={choice.id} value={choice.id}>
              {choice.label}
            </option>
          ))}
        </SelectField>
      )}
    </div>
  )
}

const choiceOf = (asVersion: boolean, folder: string): CopyChoice => (asVersion ? { as_version: true } : { folder_id: folder ? Number(folder) : null })

/** "Copy to project files…" from a file's row: asked in a dialog, where the copy goes chosen in it; what came of it, the list says. */
function CopyDialog({ file, onCopy, onClose }: { file: MyFile; onCopy: (choice: CopyChoice) => void; onClose: () => void }) {
  const [folder, setFolder] = useState('')
  const [asVersion, setAsVersion] = useState(file.origin_name != null)
  return (
    <Confirm {...copyQuestion(file)} danger={false} onConfirm={() => onCopy(choiceOf(asVersion, folder))} onClose={onClose}>
      <CopyChoices file={file} asVersion={asVersion} onAsVersion={setAsVersion} folder={folder} onFolder={setFolder} />
    </Confirm>
  )
}

/** Where a copy of the open file goes in the project's files, under its buttons, and what came of it. */
function CopyForm({ file, onClose }: { file: MyFileDetail; onClose: () => void }) {
  const workspace = useWorkspace()
  const { project } = workspace
  const queryClient = useQueryClient()
  const id = useId()
  const [folder, setFolder] = useState('')
  const [asVersion, setAsVersion] = useState(file.origin_name != null)
  const copy = useMutation({
    mutationFn: () => api.copyMyFile(file.id, choiceOf(asVersion, folder)),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: listingsKey(project.id) })
      void queryClient.invalidateQueries({ queryKey: ['folders', project.id] })
      void queryClient.invalidateQueries({ queryKey: ['project', project.id] })
    },
  })
  const copied: Document | undefined = copy.data

  return (
    <section aria-labelledby={`${id}-heading`} className="space-y-2 rounded-md border border-line bg-surface p-3">
      <h3 id={`${id}-heading`} className="text-[13px] font-semibold">
        Copy “{file.name}” to project files
      </h3>
      <p className="text-xs text-muted">{COPY_WORDS}</p>
      <CopyChoices file={file} asVersion={asVersion} onAsVersion={setAsVersion} folder={folder} onFolder={setFolder} />
      <div className="flex gap-2">
        <Button variant="primary" disabled={copy.isPending || copied != null} onClick={() => copy.mutate()}>
          {copy.isPending ? 'Copying…' : 'Copy'}
        </Button>
        <Button onClick={onClose}>{copied ? 'Close' : 'Cancel'}</Button>
      </div>
      {copy.isError && <Refusal error={copy.error} />}
      {copied && (
        <div role="status" className="flex items-center justify-between gap-2 text-[13px] text-good">
          <span>{copiedWords(copied, asVersion)}</span>
          <Button onClick={() => workspace.openDocument(copied.id)}>Open it</Button>
        </div>
      )}
    </section>
  )
}
