import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Copy, Download, FilePlus, FileUp, FolderLock, Lock, Pencil, RotateCcw, Trash2 } from 'lucide-react'
import { useEffect, useId, useRef, useState } from 'react'
import { flushSync } from 'react-dom'
import { ApiError, api, mayChange, myFilePageUrl, myFileUrl } from '../api.ts'
import type { Document, MyFile, MyFileDetail } from '../api.ts'
import { useMe } from '../auth.ts'
import { folderChoices } from '../library.ts'
import { Confirm } from '../components/Confirm.tsx'
import { PagesView } from '../components/DocumentViews.tsx'
import { Button, FIELD, IconButton, Loading, Notice, SectionBar, SelectField, Spinner } from '../components/ui.tsx'
import { MY_FILES_TOGGLE, focusLibrary } from '../focus.ts'
import { useStatus } from '../hooks.ts'
import {
  MY_FILES_NOTE,
  MY_FILE_ACCEPT,
  MY_FILE_NOTE,
  MY_FILE_POLL,
  copiedWords,
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
import { myFileKind } from './files.ts'
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
  const queryClient = useQueryClient()
  const picker = useRef<HTMLInputElement>(null)
  const body = useId()
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
  const listed = files.data?.files ?? []
  const adding = add.isPending ? 'Adding…' : 'Add a file'

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
      </SectionBar>
      <div id={body} hidden={!open} className="min-h-0 overflow-y-auto px-2 pb-2">
        <div className="space-y-2 pb-1 empty:hidden">
          {add.isPaused && <Notice tone="info">The file is added once you are back online.</Notice>}
          {add.isError && <Refusal error={add.error} />}
          {files.isError && <Notice tone="bad">Could not load your files: {files.error.message}</Notice>}
        </div>
        {files.isPending && (
          <div className="flex justify-center py-2">
            <Spinner />
          </div>
        )}
        {files.data && listed.length === 0 && (
          <p className="px-1.5 py-1 text-[13px] text-muted">
            No files of your own yet. Add a Word document or an Excel workbook, or ask in a chat for a letter or a workbook:
            the agent writes it here.
          </p>
        )}
        {listed.length > 0 && (
          <ul aria-label="My files">
            {listed.map((file) => (
              <li key={file.id}>
                <button
                  type="button"
                  title={myFileWords(file)}
                  onClick={() => workspace.openMyFile(file.id)}
                  className="flex h-8 w-full items-center gap-2 rounded-md px-1.5 text-left text-[13px] transition hover:bg-sunken"
                >
                  <KindIcon file={file} />
                  <span className="min-w-0 flex-1 truncate">{file.name}</span>
                  {waiting(file.latest) && <Spinner className="size-3" />}
                  {/* A narrow pane keeps the names, the rest in the row's title. */}
                  <span className="hidden shrink-0 text-xs text-muted @xs:inline">{myFileLine(file)}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
        {/* Who sees them, said where they are, so that nobody takes them for the project's below. */}
        <p className="flex items-start gap-1.5 px-1.5 pt-2 pb-0.5 text-xs text-muted">
          <Lock aria-hidden className="mt-px size-3.5 shrink-0" />
          {MY_FILES_NOTE}
        </p>
      </div>
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
                    maxLength={300}
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

/** Where a copy of the file goes in the project's files: a folder for a new document, or the document it was copied from as its next version. */
function CopyForm({ file, onClose }: { file: MyFileDetail; onClose: () => void }) {
  const workspace = useWorkspace()
  const { project, folders } = workspace
  const queryClient = useQueryClient()
  const id = useId()
  const [folder, setFolder] = useState('')
  const [asVersion, setAsVersion] = useState(file.origin_name != null)
  const copy = useMutation({
    mutationFn: () => api.copyMyFile(file.id, asVersion ? { as_version: true } : { folder_id: folder ? Number(folder) : null }),
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
      <p className="text-xs text-muted">Its newest version becomes a document every member of the project sees, read and indexed as any upload. Your file stays here.</p>
      {file.origin_name && (
        <fieldset className="space-y-1 text-[13px]">
          <legend className="sr-only">As what</legend>
          <label className="flex items-center gap-2">
            <input type="radio" name={`${id}-as`} checked={asVersion} onChange={() => setAsVersion(true)} />
            As a new version of “{file.origin_name}”
          </label>
          <label className="flex items-center gap-2">
            <input type="radio" name={`${id}-as`} checked={!asVersion} onChange={() => setAsVersion(false)} />
            As a new document
          </label>
        </fieldset>
      )}
      {!asVersion && (
        <SelectField label="Folder" value={folder} onChange={(event) => setFolder(event.target.value)}>
          <option value="">All files, at the top</option>
          {folderChoices(folders).map((choice) => (
            <option key={choice.id} value={choice.id}>
              {choice.label}
            </option>
          ))}
        </SelectField>
      )}
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
