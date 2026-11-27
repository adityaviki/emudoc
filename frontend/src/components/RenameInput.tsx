import { useRef } from 'react'

interface RenameInputProps {
  value: string
  isFile: boolean
  onCommit: (value: string) => void
  onCancel: () => void
  /** The longest name the field takes, a file's or a folder's otherwise. */
  maxLength?: number
  /** Called once Enter or Escape ended the editing, where the keyboard goes then; else to the listing the field is in. */
  onKeys?: () => void
}

/** An inline name editor. Enter or leaving the field commits; Escape cancels and nothing else fires. */
export function RenameInput({ value, isFile, onCommit, onCancel, maxLength, onKeys }: RenameInputProps) {
  // Once committed or cancelled, the blur that follows changes nothing.
  const done = useRef(false)
  function finish(name: string | null) {
    if (done.current) return
    done.current = true
    if (name === null) onCancel()
    else onCommit(name)
  }
  return (
    <input
      autoFocus
      defaultValue={value}
      aria-label="New name"
      maxLength={maxLength ?? (isFile ? 500 : 200)}
      onFocus={(event) => {
        // Select the name but not the extension, which is kept anyway.
        const dot = isFile ? value.lastIndexOf('.') : -1
        event.target.setSelectionRange(0, dot > 0 ? dot : value.length)
      }}
      onClick={(event) => event.stopPropagation()}
      onDoubleClick={(event) => event.stopPropagation()}
      onKeyDown={(event) => {
        event.stopPropagation()
        if (event.key !== 'Enter' && event.key !== 'Escape') return
        // Nor does the key reach what takes the keyboard next, a button that Enter would press.
        event.preventDefault()
        finish(event.key === 'Enter' ? event.currentTarget.value : null)
        // The field goes, and the keyboard goes on in the listing it was in.
        if (onKeys) onKeys()
        else event.currentTarget.closest<HTMLElement>('[role="tree"]')?.focus()
      }}
      onBlur={(event) => finish(event.target.value)}
      className="field rounded px-1.5 py-0.5 text-[13px]"
    />
  )
}
