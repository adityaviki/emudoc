import { useEffect, useId, useRef } from 'react'
import type { ReactNode } from 'react'
import { Button } from './ui.tsx'

/** What the page asks before an action: what it is about, what it does, and the button that does it. */
export interface Question {
  title: string
  body: string
  action: string
}

interface ConfirmProps extends Question {
  /** Whether the action destroys something, which gives its button the danger look. */
  danger?: boolean
  onConfirm: () => void
  /** Called once the dialog closed, whichever way: Cancel, Escape or the action. */
  onClose: () => void
  /** What the action needs chosen, between the words and the buttons. */
  children?: ReactNode
  /** Holds the action back while what is chosen could not be done; the words above say why. */
  disabled?: boolean
}

/**
 * Asks before an action, in a modal over the page for as long as it is mounted, Cancel focused
 * first, or what the action needs chosen when it marks itself `data-autofocus`. It is the browser's own dialog, opened with `showModal()`: the page beneath is inert,
 * Tab stays inside, Escape cancels, it sits above every other layer, and focus goes back to what
 * had it once it closes.
 */
export function Confirm({ title, body, action, danger = true, onConfirm, onClose, children, disabled = false }: ConfirmProps) {
  const dialog = useRef<HTMLDialogElement>(null)
  const id = useId()

  useEffect(() => {
    const shown = dialog.current
    // Strict mode runs an effect twice, and a dialog already open stays as it is.
    if (!shown || shown.open) return
    shown.showModal()
    shown.querySelector<HTMLElement>('[data-autofocus], [data-cancel]')?.focus()
  }, [])

  return (
    <dialog
      ref={dialog}
      aria-labelledby={`${id}-title`}
      aria-describedby={`${id}-body`}
      onClose={onClose}
      className="m-auto w-md max-w-[calc(100vw-32px)] rounded-xl border border-line bg-surface p-5 text-ink shadow-xl shadow-ink/8 backdrop:bg-scrim"
    >
      <h2 id={`${id}-title`} className="font-semibold break-words">
        {title}
      </h2>
      <p id={`${id}-body`} className="mt-1.5 text-[13px] leading-relaxed break-words text-muted">
        {body}
      </p>
      {children && <div className="mt-4">{children}</div>}
      <div className="mt-5 flex justify-end gap-2">
        <Button data-cancel onClick={() => dialog.current?.close()}>
          Cancel
        </Button>
        <Button
          variant={danger ? 'danger' : 'primary'}
          disabled={disabled}
          onClick={() => {
            dialog.current?.close()
            onConfirm()
          }}
        >
          {action}
        </Button>
      </div>
    </dialog>
  )
}
