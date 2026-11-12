// A document's versions (backend/app/versions.py): the dialog its tab opens, which gives it a new file and lists the
// files it had before, each to download or to read its text beside the current one.

import { useQuery } from '@tanstack/react-query'
import { FileUp, X } from 'lucide-react'
import { useEffect, useId, useRef, useState } from 'react'
import { api, mayChange, versionFileUrl } from '../api.ts'
import type { Document, DocumentVersion } from '../api.ts'
import { useMe } from '../auth.ts'
import { Scrolled, TextView } from '../components/DocumentViews.tsx'
import { Button, IconButton, Loading, Notice } from '../components/ui.tsx'
import { ONLY_VERSION, VERSION_EXTENSIONS, givenWords, versionHeld, versionName, versionWords, versionsKey } from '../versions.ts'
import { useUploadVersion } from './mutations.ts'
import { useWorkspace } from './state.ts'

/**
 * The document's versions, in a modal over the page for as long as it is mounted, as `Confirm` is: the browser's own
 * dialog, Escape closing it and the focus going back to the button that opened it.
 */
export function VersionsDialog({ document, onClose }: { document: Document; onClose: () => void }) {
  const me = useMe()
  const { project } = useWorkspace()
  // The sample project's documents are Emudoc's own and take no new file.
  const changes = mayChange(me.role) && !project.sample
  const dialog = useRef<HTMLDialogElement>(null)
  const heading = useRef<HTMLHeadingElement>(null)
  const picker = useRef<HTMLInputElement>(null)
  const id = useId()
  const versions = useQuery({ queryKey: versionsKey(document.id), queryFn: () => api.versions(document.id) })
  const upload = useUploadVersion(document.project_id)
  // The earlier version whose text is open, one at a time.
  const [reading, setReading] = useState<number | null>(null)
  const indexing = document.status === 'processing'

  useEffect(() => {
    const shown = dialog.current
    // Strict mode runs an effect twice, and a dialog already open stays as it is.
    if (!shown || shown.open) return
    shown.showModal()
    ;(shown.querySelector<HTMLElement>('[data-autofocus]') ?? heading.current)?.focus()
  }, [])

  return (
    <dialog
      ref={dialog}
      aria-labelledby={`${id}-title`}
      aria-describedby={`${id}-body`}
      onClose={onClose}
      className="m-auto w-xl max-w-[calc(100vw-32px)] rounded-xl border border-line bg-surface p-5 text-ink shadow-xl shadow-ink/8 backdrop:bg-scrim"
    >
      <div className="flex items-start justify-between gap-3">
        <h2 ref={heading} tabIndex={-1} id={`${id}-title`} className="font-semibold break-words">
          Versions of “{document.filename}”
        </h2>
        <IconButton label="Close" onClick={() => dialog.current?.close()}>
          <X className="size-4" />
        </IconButton>
      </div>
      <p id={`${id}-body`} className="mt-1 text-sm text-muted">
        Only the current version is searched, asked about and read for facts; the earlier ones stay here to open.
      </p>

      {changes && (
        <div className="mt-4 space-y-2">
          <Button variant="primary" icon={<FileUp className="size-4" />} onClick={() => picker.current?.click()} disabled={upload.isPending || indexing} data-autofocus>
            Upload a new version
          </Button>
          <input
            ref={picker}
            type="file"
            name="version"
            hidden
            accept={VERSION_EXTENSIONS.join(',')}
            onChange={(event) => {
              const file = event.target.files?.[0]
              event.target.value = ''
              if (file) upload.mutate({ id: document.id, file }, { onSuccess: () => heading.current?.focus() })
            }}
          />
          {indexing && <p className="text-sm text-muted">It takes a new version once it is read.</p>}
          {upload.isSuccess && (
            <p role="status" className="text-sm text-good">
              {givenWords(upload.data)}
            </p>
          )}
          {upload.isError && <Notice tone="bad">{upload.error.message}</Notice>}
        </div>
      )}

      {versions.isPending ? (
        <Loading />
      ) : versions.isError ? (
        <div className="mt-4">
          <Notice tone="bad">Could not load the versions: {versions.error.message}</Notice>
        </div>
      ) : (
        <>
          <ol className="mt-4 divide-y divide-line border-y border-line">
            {versions.data.map((version) => (
              <VersionItem
                key={version.number}
                document={document}
                version={version}
                reading={reading === version.number}
                onRead={() => setReading((open) => (open === version.number ? null : version.number))}
              />
            ))}
          </ol>
          {versions.data.length === 1 && <p className="mt-3 text-sm text-muted">{ONLY_VERSION}</p>}
        </>
      )}
    </dialog>
  )
}

interface VersionItemProps {
  document: Document
  version: DocumentVersion
  reading: boolean
  onRead: () => void
}

/** One version: what it was, and for an earlier one its file to download and its text to read. */
function VersionItem({ document, version, reading, onRead }: VersionItemProps) {
  const name = versionName(version)
  const held = version.current ? null : versionHeld(version)
  const text = useQuery({
    queryKey: [...versionsKey(document.id), version.number],
    queryFn: () => api.versionContent(document.id, version.number),
    enabled: reading,
  })
  return (
    <li className="py-3">
      <p className="text-sm font-medium">{name}</p>
      <p className="mt-0.5 text-xs break-words text-muted">{versionWords(version)}</p>
      {!version.current && (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          {version.served && (
            <a
              href={versionFileUrl(document.id, version.number)}
              download=""
              aria-label={`Download version ${version.number}`}
              className="inline-flex h-7 items-center rounded-md border border-line px-2.5 text-xs font-medium transition hover:bg-sunken"
            >
              Download
            </a>
          )}
          {version.has_text && (
            <Button onClick={onRead} aria-expanded={reading} aria-label={`${reading ? 'Hide' : 'Show'} the text of version ${version.number}`}>
              {reading ? 'Hide the text' : 'Show the text'}
            </Button>
          )}
          {held && <span className="text-xs text-muted">{held}</span>}
        </div>
      )}
      {reading &&
        (text.isPending ? (
          <Loading />
        ) : text.isError ? (
          <div className="mt-2">
            <Notice tone="bad">Could not load its text: {text.error.message}</Notice>
          </div>
        ) : (
          <Scrolled label={`Text of ${name.toLowerCase()}`} className="mt-2 max-h-80 overflow-y-auto rounded-md border border-line bg-page">
            <TextView blocks={text.data.blocks} focus={null} />
          </Scrolled>
        ))}
    </li>
  )
}
