import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Copy, Download, FilePlus, FileUp, FolderLock, Lock, Pencil, RotateCcw, Trash2, X } from 'lucide-react'
import { useEffect, useId, useRef, useState } from 'react'
import { flushSync } from 'react-dom'
import { ApiError, api, mayChange, myFilePageUrl, myFileUrl } from '../api.ts'
import type { Document, MyFile, MyFileDetail } from '../api.ts'
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
  copiedWords,
  copyQuestion,
  following,
  heldWords,
  myFileDeletionQuestion,
  myFileKey,
  myFileLine,
  myFileQuery,
  myFileWords,
  myFilesCount,
  myFilesKey,
  myVersionName,
  myVersionWords,
  retryable,
  servable,
  unshownWords,
  waiting,
} from '../myFiles.ts'
import { viewContent } from '../views.ts'
import { myFileDownloadItem, myFileKind } from './files.ts'
import { listingsKey } from './listing.ts'
import { myFileTab, shownAfter, tabKey, useWorkspace } from './state.ts'
import { TAB_ID } from './TabStrip.tsx'

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

/**
 * The member's own files in the project, in the library under its search and above the project's files: the Word
 * documents and Excel workbooks they keep here, which nobody else sees, the one changed last first, each a row as the library's are and
 * opening, as theirs do, in a tab of the middle pane; and a file added from this computer, opened there too. Its heading
 * opens and closes the list; the count and "Add a file" stay on it while it is closed.
 */
export function MyFilesSection({ open, onToggle, capped }: MyFilesSectionProps) {
  const workspace = useWorkspace()
  const { project } = workspace
  const changes = mayChange(useMe().role)
  const queryClient = useQueryClient()
  const picker = useRef<HTMLInputElement>(null)
  const versionInput = useRef<HTMLInputElement>(null)
  // The file whose new version the file picker is choosing.
  const versionOf = useRef<MyFile | null>(null)
  const body = useId()
  const rowId = (fileId: number) => `${body}-file-${fileId}`
  const menu = useContextMenu()
  // The row whose menu is open, drawn as the library's is.
  const [menuFor, setMenuFor] = useState<number | null>(null)
  const [renaming, setRenaming] = useState<number | null>(null)
  // Enter or Escape ended a rename: the keyboard goes back to the file's row.
  const backTo = useRef<number | null>(null)
  const [deleting, setDeleting] = useState<MyFile | null>(null)
  const [copying, setCopying] = useState<MyFile | null>(null)
  const [refused, setRefused] = useState<Error | null>(null)
  useEffect(() => {
    if (renaming != null || backTo.current == null) return
    window.document.getElementById(rowId(backTo.current))?.focus()
    backTo.current = null
  })
  const files = useQuery({
    queryKey: myFilesKey(project.id),
    queryFn: () => api.myFiles(project.id),
    refetchInterval: (query) => (query.state.data && following(query.state.data) ? MY_FILE_POLL : false),
  })
  const add = useMutation({
    mutationFn: (file: File) => api.addMyFile(project.id, file),
    onSuccess: (added) => {
      queryClient.setQueryData(myFileKey(added.id), added)
      void queryClient.invalidateQueries({ queryKey: myFilesKey(project.id) })
      workspace.openMyFile(added.id)
    },
  })
  const kept = (detail: MyFileDetail) => {
    queryClient.setQueryData(myFileKey(detail.id), detail)
    void queryClient.invalidateQueries({ queryKey: myFilesKey(project.id) })
  }
  const rename = useMutation({
    mutationFn: ({ file, name }: { file: MyFile; name: string }) => api.renameMyFile(file.id, name),
    onMutate: () => setRefused(null),
    onSuccess: kept,
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
  const remove = useMutation({
    mutationFn: (file: MyFile) => api.deleteMyFile(file.id),
    onMutate: () => setRefused(null),
    onSuccess: (_, file) => {
      void queryClient.invalidateQueries({ queryKey: myFilesKey(project.id) })
      // Its tab goes with it, and the keyboard on from the heading of My files, whose list held it: the question closed
      // first, since the page beneath it takes no focus while it is open.
      flushSync(() => setDeleting(null))
      workspace.closeTabs([myFileTab(file.id)])
      queryClient.removeQueries({ queryKey: myFileKey(file.id) })
      window.document.getElementById(MY_FILES_TOGGLE)?.focus()
    },
    onError: (error) => {
      setDeleting(null)
      setRefused(error)
    },
  })
  const listed = files.data?.files ?? []
  const adding = add.isPending ? 'Adding…' : 'Add a file'

  function pickVersion(file: MyFile) {
    const input = versionInput.current
    if (!input) return
    versionOf.current = file
    input.accept = file.kind === 'xlsx' ? '.xlsx' : '.docx'
    input.click()
  }

  /** What a file's row offers, as the project's files' menu does: what My files can do to one of its files. */
  function fileMenu(file: MyFile): MenuItem[] {
    return [
      { label: 'Open', onSelect: () => workspace.openMyFile(file.id) },
      myFileDownloadItem(file),
      { label: 'Rename', icon: Pencil, shortcut: 'F2', onSelect: () => setRenaming(file.id) },
      { label: 'Upload a new version…', icon: FileUp, disabled: newVersion.isPending, onSelect: () => pickVersion(file) },
      // Whoever may change the project's files, once the newest version may be downloaded.
      ...(changes
        ? [{ label: 'Copy to project files…', icon: Copy, disabled: !servable(file.latest), onSelect: () => setCopying(file) }]
        : []),
      'separator',
      { label: 'Delete', icon: Trash2, shortcut: 'Del', danger: true, onSelect: () => setDeleting(file) },
    ]
  }

  // The list's empty space, as the project files' does.
  const backgroundMenu: MenuItem[] = [{ label: 'Add a file', icon: FilePlus, disabled: add.isPending, onSelect: () => picker.current?.click() }]

  function renamed(file: MyFile, name: string) {
    const trimmed = name.trim()
    setRenaming(null)
    if (trimmed && trimmed !== file.name) rename.mutate({ file, name: trimmed })
  }

  return (
    <section aria-labelledby={MY_FILES_TOGGLE} className={`@container flex min-h-0 flex-col ${capped ? 'max-h-[35%] shrink-0' : ''}`}>
      <SectionBar id={MY_FILES_TOGGLE} controls={body} open={open} onToggle={onToggle} icon={<FolderLock />} title="My files">
        {files.data && listed.length > 0 && (
          <span className="mr-1 hidden shrink-0 text-xs text-muted @3xs:inline">{myFilesCount(listed.length, files.data.limit)}</span>
        )}
        {/* A narrow pane keeps the icon only. */}
        <Button
          variant="ghost"
          icon={<FilePlus className="size-4" />}
          disabled={add.isPending}
          onClick={() => picker.current?.click()}
          title="Add a Word document or an Excel workbook of your own from this computer"
          aria-label={adding}
          className="h-7 px-1.5 @xs:px-2"
        >
          <span className="hidden @xs:inline">{adding}</span>
        </Button>
        <input
          ref={picker}
          type="file"
          name="my-file"
          hidden
          accept={MY_FILE_ACCEPT}
          onChange={(event) => {
            const file = event.target.files?.[0]
            event.target.value = ''
            if (file) add.mutate(file)
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
      <div
        id={body}
        hidden={!open}
        className="min-h-0 overflow-y-auto px-2 pb-2"
        onContextMenu={(event) => {
          setMenuFor(null)
          menu.open(event, backgroundMenu)
        }}
      >
        <div className="space-y-2 pb-1 empty:hidden">
          {add.isPaused && <Notice tone="info">The file is added once you are back online.</Notice>}
          {add.isError && <Refusal error={add.error} />}
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
        {files.data && listed.length === 0 && (
          // Said in a box of the project files' kind, so that the section opened is not taken for one closed.
          <div className="flex items-center justify-center gap-2 rounded-lg border border-dashed border-ink/20 px-3 py-4 text-[13px] text-muted">
            <FolderLock aria-hidden className="size-4 shrink-0" />
            No files yet
          </div>
        )}
        {listed.length > 0 && (
          <ul aria-label="My files">
            {listed.map((file) => (
              <li key={file.id}>
                {renaming === file.id ? (
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
                          backTo.current = file.id
                        }}
                      />
                    </div>
                  </div>
                ) : (
                <button
                  id={rowId(file.id)}
                  type="button"
                  title={myFileWords(file)}
                  onClick={() => workspace.openMyFile(file.id)}
                  onContextMenu={(event) => {
                    setMenuFor(file.id)
                    menu.open(event, fileMenu(file))
                  }}
                  onKeyDown={(event) => {
                    if (event.key === 'F2') {
                      event.preventDefault()
                      setRenaming(file.id)
                    } else if (event.key === 'Delete') {
                      event.preventDefault()
                      setDeleting(file)
                    }
                  }}
                  className={`flex h-8 w-full items-center gap-2 rounded-md px-1.5 text-left text-[13px] transition hover:bg-sunken ${
                    menu.isOpen && menuFor === file.id ? 'bg-sunken' : ''
                  }`}
                >
                  <KindIcon file={file} />
                  <span className="min-w-0 flex-1 truncate">{file.name}</span>
                  {waiting(file.latest) && <Spinner className="size-3" />}
                  {/* A narrow pane keeps the names, the rest in the row's title. */}
                  <span className="hidden shrink-0 text-xs text-muted @xs:inline">{myFileLine(file)}</span>
                </button>
                )}
              </li>
            ))}
          </ul>
        )}
        {menu.element}
      </div>
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
      {copying && <CopyDialog file={copying} onCopy={(choice) => copy.mutate({ file: copying, choice })} onClose={() => setCopying(null)} />}
    </section>
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
