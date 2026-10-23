import { useQuery } from '@tanstack/react-query'
import { CircleAlert, ShieldAlert, X } from 'lucide-react'
import { useState, useSyncExternalStore } from 'react'
import { api } from '../api.ts'
import { Button, IconButton } from '../components/ui.tsx'
import { WAITING_REFRESH } from './listing.ts'
import { useWorkspace } from './state.ts'
import { files, followAfter, followedBatches, followedKey, pathOf, shareWords, sharesOf, standingOf, summed, wholeOf } from './uploads.ts'
import type { UploadProgress, UploadState } from './uploads.ts'

interface UploadPanelProps {
  progress: UploadProgress
  /** The name of the folder the upload goes into. */
  into: string
  onStop: () => void
  /** The upload sent last, whose documents are followed as they are indexed, and the name of the folder it went into. */
  followed: { uploads: number[]; into: string } | null
  onDismiss: () => void
}

/**
 * How far the upload under way has gone, as a whole and, opened, file by file, while it is
 * being sent; then how far its documents are indexed, with those that could not be and why,
 * until it is dismissed or another upload starts. Only the count of files sent and where the
 * documents stand are read out as they change, since the bytes change every moment; the bar
 * says them to whoever asks it.
 */
export function UploadPanel({ progress, into, onStop, followed, onDismiss }: UploadPanelProps) {
  const state = useSyncExternalStore(progress.subscribe, progress.get)
  if (state) return <Sending state={state} into={into} onStop={onStop} />
  return followed && <Following key={followed.uploads.join()} {...followed} onDismiss={onDismiss} />
}

function Sending({ state, into, onStop }: { state: UploadState; into: string; onStop: () => void }) {
  const [open, setOpen] = useState(false)
  const whole = wholeOf(state)
  const shares = open ? sharesOf(state) : []
  const title = `Uploading ${files(state.items.length)} into ${into}`
  return (
    <section aria-label="Upload" className="rounded-md border border-line bg-surface px-3 py-2 text-[13px]">
      <div className="flex items-center justify-between gap-3">
        <span className="min-w-0 truncate font-medium" title={title}>
          {title}
        </span>
        {/* Once a request's files are sent the server stores them whatever the page does. */}
        <Button variant="ghost" className="h-6 shrink-0 px-2" disabled={whole.storing} onClick={onStop}>
          Stop
        </Button>
      </div>
      <Bar label={title} percent={whole.percent} text={`${whole.percent} %, ${whole.bytes}`} />
      <p className="mt-1.5 flex flex-wrap justify-between gap-x-3 text-muted">
        <span role="status">{whole.line}</span>
        <span className="tabular-nums">{whole.bytes}</span>
      </p>
      <details className="mt-1" onToggle={(event) => setOpen(event.currentTarget.open)}>
        <summary className="w-fit cursor-pointer rounded text-muted hover:text-ink">Each file</summary>
        {open && (
          <ul tabIndex={0} aria-label="Each file" className="mt-1 max-h-40 overflow-y-auto rounded">
            {state.items.map((item, index) => (
              <li key={index} className="flex justify-between gap-3 py-0.5">
                <span className="min-w-0 truncate" title={pathOf(item)}>
                  {pathOf(item)}
                </span>
                <span className="shrink-0 text-muted tabular-nums">{shareWords(shares[index])}</span>
              </li>
            ))}
          </ul>
        )}
      </details>
    </section>
  )
}

/**
 * The upload's documents as they are indexed, asked about every moment while one is, and after
 * the soonest retry while only retries wait (`followAfter`), and again when the library indexes
 * or deletes documents. A document that could not be indexed opens in its tab, which says the
 * same; "Index again" is in its menu in the library.
 */
function Following({ uploads, into, onDismiss }: { uploads: number[]; into: string; onDismiss: () => void }) {
  const workspace = useWorkspace()
  const projectId = workspace.project.id
  const status = useQuery({
    queryKey: [...followedKey(projectId), uploads],
    queryFn: async ({ signal }) =>
      summed(await Promise.all(followedBatches(uploads).map((ids) => api.uploadStatus(projectId, ids, signal)))),
    refetchInterval: (query) => (query.state.data ? followAfter(query.state.data) : WAITING_REFRESH),
  })
  const standing = status.data && standingOf(status.data, into)
  const failures = status.data?.failures ?? []
  const title = standing?.title ?? `Indexing the upload into ${into}`
  // The panel goes with the focus on its button: the keyboard goes on from the tree.
  const dismiss = () => {
    onDismiss()
    window.document.querySelector<HTMLElement>('[role="tree"]')?.focus()
  }
  return (
    <section aria-label="Upload" className="rounded-md border border-line bg-surface px-3 py-2 text-[13px]">
      <div className="flex items-center justify-between gap-3">
        <span className="min-w-0 truncate font-medium" title={title}>
          {title}
        </span>
        <IconButton label="Dismiss" onClick={dismiss} className="p-0.5!">
          <X className="size-3.5" />
        </IconButton>
      </div>
      {standing && <Bar label={title} percent={standing.percent} text={standing.line} />}
      <p role="status" className="mt-1.5 text-muted">
        {standing?.line ??
          (status.isError ? 'How far it is indexed cannot be read just now: the library marks each document.' : 'Reading how far it is indexed.')}
      </p>
      {failures.length > 0 && (
        <ul aria-label="Could not be indexed" className="mt-1 max-h-40 space-y-0.5 overflow-y-auto">
          {failures.map((failure) => (
            <li key={failure.id} className="flex items-start gap-1.5">
              {failure.quarantined ? (
                <ShieldAlert aria-hidden className="mt-0.5 size-3.5 shrink-0 text-bad" />
              ) : (
                <CircleAlert aria-hidden className="mt-0.5 size-3.5 shrink-0 text-bad" />
              )}
              <span className="min-w-0">
                <button
                  type="button"
                  className="rounded font-medium break-all hover:underline"
                  onClick={() => workspace.openDocument(failure.id)}
                >
                  {failure.filename}
                </button>
                : {failure.reason}
              </span>
            </li>
          ))}
        </ul>
      )}
      {standing?.more && <p className="mt-1 text-muted">{standing.more}</p>}
    </section>
  )
}

function Bar({ label, percent, text }: { label: string; percent: number; text: string }) {
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={percent}
      aria-valuetext={text}
      className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-sunken"
    >
      <div className="h-full rounded-full bg-accent" style={{ width: `${percent}%` }} />
    </div>
  )
}
