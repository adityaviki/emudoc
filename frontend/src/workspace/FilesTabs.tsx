import { FolderLock, Folders } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import type { KeyboardEvent, ReactNode } from 'react'
import { useId, useRef } from 'react'
import { filesSide, tabLeads } from '../myFiles.ts'
import type { FilesSide } from '../myFiles.ts'
import { useWorkspace } from './state.ts'

const SIDES: { side: FilesSide; label: string; icon: LucideIcon; title: string }[] = [
  { side: 'project', label: 'Project files', icon: Folders, title: 'The project’s documents and folders, which every member sees' },
  { side: 'mine', label: 'My files', icon: FolderLock, title: 'Your own Word and Excel files in this project, which only you see' },
]

/**
 * The library's two sides, a tab each above whatever it shows: the project's files, which every member sees (its folders,
 * places and search, the trash, Email in and the connected folders), and the member's own My files, which only they see.
 * The workspace's library pane holds it around the library, so the strip stays as a tab is chosen and the arrows keep its
 * focus; a tab opens its side's list, from the other side or from a page within its own.
 */
export function FilesTabs({ children }: { children: ReactNode }) {
  const workspace = useWorkspace()
  const view = workspace.url.library
  const shown = filesSide(view)
  const id = useId()
  const tabs = useRef<Partial<Record<FilesSide, HTMLButtonElement | null>>>({})
  const open = (side: FilesSide) => {
    if (!tabLeads(view, side)) return
    if (side === 'mine') workspace.showMyFiles()
    else workspace.goToFolder(null)
  }
  // Two tabs: either arrow, Home or End goes to the other one and opens it.
  const onKeyDown = (event: KeyboardEvent) => {
    const to: FilesSide | null =
      event.key === 'Home' ? 'project' : event.key === 'End' ? 'mine' : ['ArrowLeft', 'ArrowRight'].includes(event.key) ? (shown === 'mine' ? 'project' : 'mine') : null
    if (to == null) return
    event.preventDefault()
    tabs.current[to]?.focus()
    if (to !== shown) open(to)
  }

  return (
    <div className="flex h-full min-h-0 flex-col bg-page">
      <div role="tablist" aria-label="Files" onKeyDown={onKeyDown} className="flex h-10 shrink-0 items-end gap-1 border-b border-line bg-surface px-2">
        {SIDES.map(({ side, label, icon: Icon, title }) => {
          const selected = side === shown
          return (
            <button
              key={side}
              ref={(element) => {
                tabs.current[side] = element
              }}
              type="button"
              role="tab"
              id={`${id}-${side}`}
              aria-selected={selected}
              aria-controls={`${id}-panel`}
              tabIndex={selected ? 0 : -1}
              title={title}
              onClick={() => open(side)}
              className={`-mb-px flex h-9 items-center gap-1.5 border-b-2 px-2.5 text-[13px] whitespace-nowrap transition focus-visible:-outline-offset-2! ${
                selected ? 'border-accent font-medium text-ink' : 'border-transparent text-muted hover:text-ink'
              }`}
            >
              <Icon aria-hidden className={`size-4 shrink-0 ${selected ? 'text-accent' : ''}`} />
              {label}
            </button>
          )
        })}
      </div>
      <div role="tabpanel" id={`${id}-panel`} aria-labelledby={`${id}-${shown}`} className="min-h-0 flex-1">
        {children}
      </div>
    </div>
  )
}
