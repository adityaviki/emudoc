import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Copy, Download, FilePlus, FileSpreadsheet, FileText, FileUp, Pencil, RotateCcw, Trash2 } from 'lucide-react'
import { useEffect, useId, useRef, useState } from 'react'
import { ApiError, api, isPlanLimit, mayChange, myFilePageUrl, myFileUrl } from '../api.ts'
import type { Document, MyFile, MyFileDetail } from '../api.ts'
import { useMe } from '../auth.ts'
import { folderChoices } from '../automations.ts'
import { Confirm } from '../components/Confirm.tsx'
import { PagesView } from '../components/DocumentViews.tsx'
import { PlanLimitNotice } from '../components/PlanLimit.tsx'
import { Button, EmptyState, FIELD, IconButton, Loading, Notice, SelectField, Spinner } from '../components/ui.tsx'
import { useStatus } from '../hooks.ts'
import {
  MY_FILES_NOTE,
  MY_FILE_ACCEPT,
  MY_FILE_POLL,
  copiedWords,
  heldWords,
  myFileDeletionQuestion,
  myFileKey,
  myFileWords,
  myFilesKey,
  myVersionName,
  myVersionWords,
  retryable,
  servable,
  unshownWords,
  waiting,
} from '../myFiles.ts'
import { viewContent } from '../views.ts'
import { listingsKey } from './listing.ts'
import { useWorkspace } from './state.ts'

/** A refusal as the page shows it: a plan's with the way past it, any other in its own words. */
function Refusal({ error }: { error: Error }) {
  return isPlanLimit(error) ? <PlanLimitNotice refusal={{ message: error.message, limit: error.limit }} /> : <Notice tone="bad">{error.message}</Notice>
}

const KindIcon = ({ file }: { file: MyFile }) =>
  file.kind === 'xlsx' ? <FileSpreadsheet aria-hidden className="size-4 shrink-0 text-muted" /> : <FileText aria-hidden className="size-4 shrink-0 text-muted" />

/**
 * The member's own files in the project, in the library's place: the Word documents and Excel workbooks they keep here,
 * which nobody else sees, the one changed last first, each opening in the same place; and a file added from this
 * computer.
 */
export function MyFilesView() {
  const workspace = useWorkspace()
  const { project } = workspace
  const queryClient = useQueryClient()
  const heading = useRef<HTMLHeadingElement>(null)
  const picker = useRef<HTMLInputElement>(null)
  // Opened from a button that went with the view before it: the keyboard goes on from here.
  useEffect(() => {
    if (window.document.activeElement === window.document.body) heading.current?.focus()
  }, [])
  const files = useQuery({
    queryKey: myFilesKey(project.id),
    queryFn: () => api.myFiles(project.id),
    refetchInterval: (query) => (query.state.data?.files.some((file) => waiting(file.latest)) ? MY_FILE_POLL : false),
  })
  const add = useMutation({
    mutationFn: (file: File) => api.addMyFile(project.id, file),
    onSuccess: (added) => {
      queryClient.setQueryData(myFileKey(added.id), added)
      void queryClient.invalidateQueries({ queryKey: myFilesKey(project.id) })
      workspace.showMyFile(added.id)
    },
  })
  const listed = files.data?.files ?? []

  return (
    <div className="flex h-full min-h-0 flex-col bg-page">
      <div className="flex h-11 shrink-0 items-center gap-1.5 border-b border-line bg-surface px-2">
        <IconButton label="Back to the library" onClick={() => workspace.goToFolder(null)}>
          <ArrowLeft className="size-4" />
        </IconButton>
        <h2 ref={heading} tabIndex={-1} className="flex-1 truncate text-[13px] font-semibold">
          My files
        </h2>
        <Button icon={<FilePlus className="size-4" />} disabled={add.isPending} onClick={() => picker.current?.click()}>
          {add.isPending ? 'Adding…' : 'Add a file'}
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
      </div>
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3">
        <p className="text-xs text-muted">{MY_FILES_NOTE}</p>
        {add.isPaused && <Notice tone="info">The file is added once you are back online.</Notice>}
        {add.isError && <Refusal error={add.error} />}
        {files.isPending && <Loading />}
        {files.isError && <Notice tone="bad">Could not load your files: {files.error.message}</Notice>}
        {files.data && listed.length === 0 && (
          <EmptyState icon={<FileText />} title="No files yet">
            Add a Word document or an Excel workbook to keep it here.
          </EmptyState>
        )}
        {listed.length > 0 && (
          <section aria-labelledby="my-files-list" className="space-y-2">
            <h3 id="my-files-list" className="text-xs font-semibold text-muted">
              {listed.length} of {files.data?.limit} files
            </h3>
            <ul aria-label="My files" className="space-y-1.5">
              {listed.map((file) => (
                <li key={file.id}>
                  <button
                    type="button"
                    onClick={() => workspace.showMyFile(file.id)}
                    className="flex w-full items-start gap-2 rounded-md border border-line bg-surface px-3 py-2 text-left transition hover:bg-sunken"
                  >
                    <KindIcon file={file} />
                    <span className="min-w-0 flex-1">
                      <span className="block text-[13px] font-medium break-words">{file.name}</span>
                      <span className="flex items-center gap-1.5 text-xs text-muted">
                        {waiting(file.latest) && <Spinner className="size-3" />}
                        {myFileWords(file)}
                      </span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>
    </div>
  )
}

/**
 * One of the member's files, in the library's place: its pages as they look, its versions to open and download, a new
 * version uploaded from this computer, and the file renamed, deleted or copied into the project's library.
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
  const file = useQuery({
    queryKey: myFileKey(fileId),
    queryFn: () => api.myFile(fileId),
    refetchInterval: (query) => (query.state.data && waiting(query.state.data.latest) ? MY_FILE_POLL : false),
  })
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
      queryClient.removeQueries({ queryKey: myFileKey(fileId) })
      void queryClient.invalidateQueries({ queryKey: myFilesKey(project.id) })
      workspace.showMyFiles()
    },
    onError: (error) => setRefused(error),
  })
  const newest = data?.latest.number ?? 1
  const version = data?.history.find((one) => one.number === (chosen ?? newest)) ?? data?.latest
  const unshown = version ? unshownWords(version, views) : null

  return (
    <div className="flex h-full min-h-0 flex-col bg-page">
      <div className="flex h-11 shrink-0 items-center gap-1.5 border-b border-line bg-surface px-2">
        <IconButton label="Back to My files" onClick={workspace.showMyFiles}>
          <ArrowLeft className="size-4" />
        </IconButton>
        <h2 ref={heading} tabIndex={-1} className="flex-1 truncate text-[13px] font-semibold">
          {data?.name ?? 'File'}
        </h2>
        {data && (
          <>
            <IconButton label="Rename this file" onClick={() => setRenaming(data.name)}>
              <Pencil className="size-4" />
            </IconButton>
            <IconButton label="Delete this file" danger disabled={remove.isPending} onClick={() => setDeleting(true)}>
              <Trash2 className="size-4" />
            </IconButton>
          </>
        )}
      </div>
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
            <div className="space-y-0.5 text-xs text-muted">
              <p>{myFileWords(data)}</p>
              {data.origin_name && <p>Copied from “{data.origin_name}” in the library.</p>}
            </div>
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
                  Copy to the library
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

/** Where a copy of the file goes in the library: a folder for a new document, or the document it was copied from as its next version. */
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
        Copy “{file.name}” to the library
      </h3>
      <p className="text-xs text-muted">Its newest version becomes a document the project’s members see, read and indexed as any upload. Your file stays here.</p>
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
          <option value="">The library’s top</option>
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
