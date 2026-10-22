import { useState, useSyncExternalStore } from 'react'
import { Button } from '../components/ui.tsx'
import { files, pathOf, shareWords, sharesOf, wholeOf } from './uploads.ts'
import type { UploadProgress, UploadState } from './uploads.ts'

interface UploadPanelProps {
  progress: UploadProgress
  /** The name of the folder the upload goes into. */
  into: string
  onStop: () => void
}

/**
 * How far the upload under way has gone, as a whole and, opened, file by file, while it is
 * being sent; nothing otherwise. Only the count of files sent is read out as it changes, since
 * the bytes change every moment; the bar says them to whoever asks it.
 */
export function UploadPanel({ progress, into, onStop }: UploadPanelProps) {
  const state = useSyncExternalStore(progress.subscribe, progress.get)
  return state && <Sending state={state} into={into} onStop={onStop} />
}

function Sending({ state, into, onStop }: Omit<UploadPanelProps, 'progress'> & { state: UploadState }) {
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
      <div
        role="progressbar"
        aria-label={title}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={whole.percent}
        aria-valuetext={`${whole.percent} %, ${whole.bytes}`}
        className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-sunken"
      >
        <div className="h-full rounded-full bg-accent" style={{ width: `${whole.percent}%` }} />
      </div>
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
