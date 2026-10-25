import { X } from 'lucide-react'
import { useEffect, useId, useRef, useState } from 'react'
import { IconButton } from '../components/ui.tsx'
import { askForComposer, keysHeld, onMac, shortcutList, shortcutOf } from '../shortcuts.ts'
import { useWorkspace } from './state.ts'

/** The workspace's keyboard shortcuts (src/shortcuts.ts), heard from anywhere on the page, and the dialog that lists them. */
export function WorkspaceShortcuts() {
  const workspace = useWorkspace()
  const [listing, setListing] = useState(false)
  // The workspace as it is now, for the one listener the page keeps.
  const current = useRef(workspace)
  useEffect(() => {
    current.current = workspace
  })

  useEffect(() => {
    const mac = onMac()
    function onKeyDown(event: KeyboardEvent) {
      const shortcut = shortcutOf(event, mac)
      if (shortcut == null || event.defaultPrevented || event.repeat || keysHeld()) return
      event.preventDefault()
      if (shortcut === 'shortcuts') {
        setListing(true)
        return
      }
      // A new chat shows the chat pane on a narrow screen by itself.
      if (shortcut === 'newChat') {
        current.current.openChat('new')
        askForComposer('new')
      } else {
        current.current.setMobilePane('chat')
        askForComposer()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [])

  return listing ? <ShortcutsDialog onClose={() => setListing(false)} /> : null
}

/**
 * The shortcuts, in a modal over the page for as long as it is mounted, as `Confirm` is: the
 * browser's own dialog, Escape closing it and the focus going back to what had it.
 */
function ShortcutsDialog({ onClose }: { onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null)
  const heading = useRef<HTMLHeadingElement>(null)
  const id = useId()

  useEffect(() => {
    const shown = dialog.current
    // Strict mode runs an effect twice, and a dialog already open stays as it is.
    if (!shown || shown.open) return
    shown.showModal()
    heading.current?.focus()
  }, [])

  return (
    <dialog
      ref={dialog}
      aria-labelledby={`${id}-title`}
      onClose={onClose}
      className="m-auto w-md max-w-[calc(100vw-32px)] rounded-xl border border-line bg-surface p-5 text-ink shadow-xl shadow-ink/8 backdrop:bg-scrim"
    >
      <div className="flex items-start justify-between gap-3">
        <h2 ref={heading} tabIndex={-1} id={`${id}-title`} className="font-semibold">
          Keyboard shortcuts
        </h2>
        <IconButton label="Close" onClick={() => dialog.current?.close()}>
          <X className="size-4" />
        </IconButton>
      </div>
      <dl className="mt-3 divide-y divide-line border-y border-line text-[13px]">
        {shortcutList(onMac()).map(({ keys, does }) => (
          <div key={does} className="flex items-center justify-between gap-4 py-2">
            <dt className="flex shrink-0 items-center gap-1">
              {keys.map((key, index) => (
                <kbd key={index} className="rounded border border-line bg-sunken px-1.5 py-0.5 font-sans text-xs">
                  {key}
                </kbd>
              ))}
            </dt>
            <dd className="text-right text-muted">{does}</dd>
          </div>
        ))}
      </dl>
    </dialog>
  )
}
