import { FileText, Pin, PinOff, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import type { DragEvent, KeyboardEvent, MouseEvent } from 'react'
import type { Document } from '../api.ts'
import { StatusDot } from '../components/IndexingStatus.tsx'
import { useContextMenu } from '../components/useContextMenu.tsx'
import { onMac, tabMoveKeys, tabMoveOf } from '../shortcuts.ts'
import { fileKind, originalFileItem } from './files.ts'
import { closedByMenu, droppedAt, moved, useWorkspace } from './state.ts'

/**
 * One tab per open document above the middle pane. The tabs are one stop of the keyboard: the shown tab takes focus, the arrows, Home and End show another,
 * and Delete closes it. The close button is the pointer's alone, hidden from screen readers, since
 * a tab's content is read as its name and cannot hold a button of its own. A document's tab is
 * put in another place by a drag, by Ctrl+Shift and an arrow, or by its menu's "Move left" and
 * "Move right". A pinned tab stays before the
 * others, shows its pin where the others' close button is, and is left open by "Close others" and
 * "Close all"; it closes by its menu's "Close", by Delete and by a middle click.
 */
export function TabStrip() {
  const workspace = useWorkspace()
  const { activeDocumentId, openTabs } = workspace
  const strip = useRef<HTMLDivElement>(null)
  const menu = useContextMenu()
  // Set by a key that moves or closes the open tab, whose tab then takes the focus.
  const refocus = useRef(false)
  // The document whose tab is being dragged, and the gap it would drop into (the number of the
  // documents' tabs before it), while that would move it.
  const dragged = useRef<number | null>(null)
  const [gap, setGap] = useState<number | null>(null)
  // The tabs in the order the keys move through them.
  const order = openTabs.map((tab) => tab.id)
  const mac = onMac()
  const moveKeys = tabMoveKeys(mac)
  // A tab moved by a key is moved in the page too, and may lose the focus as it goes.
  const orderKey = order.join()

  // Keep the active tab in view when it changes, and in focus when the keyboard changed it.
  useEffect(() => {
    const tab = strip.current?.querySelector<HTMLElement>('[aria-selected="true"]')
    tab?.scrollIntoView({ inline: 'nearest', block: 'nearest' })
    if (refocus.current) tab?.focus()
    refocus.current = false
  }, [activeDocumentId, orderKey])

  function tabMenu(event: MouseEvent, document: Document) {
    const others = closedByMenu(openTabs, document.id)
    const all = closedByMenu(openTabs)
    const index = openTabs.findIndex((tab) => tab.id === document.id)
    const pinned = openTabs[index]?.pinned ?? false
    menu.open(event, [
      { label: 'Close', shortcut: 'Delete', onSelect: () => workspace.closeTabs([document.id]) },
      { label: 'Close others', disabled: others.length === 0, onSelect: () => workspace.closeTabs(others) },
      { label: 'Close all', disabled: all.length === 0, onSelect: () => workspace.closeTabs(all) },
      'separator',
      pinned
        ? { label: 'Unpin', icon: PinOff, onSelect: () => workspace.pinTab(document.id, false) }
        : { label: 'Pin', icon: Pin, onSelect: () => workspace.pinTab(document.id, true) },
      // The ways to move a tab without a drag (WCAG 2.5.7), as its keys are the keyboard's; a
      // tab moves among those pinned as it is, or not.
      {
        label: 'Move left',
        shortcut: moveKeys.left,
        disabled: moved(openTabs, document.id, index - 1) === openTabs,
        onSelect: () => workspace.moveTab(document.id, index - 1),
      },
      {
        label: 'Move right',
        shortcut: moveKeys.right,
        disabled: moved(openTabs, document.id, index + 1) === openTabs,
        onSelect: () => workspace.moveTab(document.id, index + 1),
      },
      'separator',
      originalFileItem(document),
    ])
  }

  function show(id: number) {
    const tab = openTabs.find((open) => open.id === id)
    workspace.openDocument(id, tab?.focus ?? null)
  }

  function close(id: number) {
    // The last tab takes the middle pane with it: the keyboard goes back to the library.
    if (order.length === 1) window.document.querySelector<HTMLElement>('[role="tree"]')?.focus()
    else refocus.current = true
    workspace.closeTabs([id])
  }

  function onKeyDown(event: KeyboardEvent, id: number) {
    const step = tabMoveOf(event, mac)
    if (step != null) {
      // The arrows with these keys show no other tab.
      event.preventDefault()
      const index = openTabs.findIndex((tab) => tab.id === id)
      // At an end of its group the tab stays, and nothing is drawn again to take the focus back.
      if (moved(openTabs, id, index + step) === openTabs) return
      refocus.current = true
      workspace.moveTab(id, index + step)
      return
    }
    const index = order.indexOf(id)
    const moves: Record<string, number> = { ArrowLeft: index - 1, ArrowRight: index + 1, Home: 0, End: order.length - 1 }
    if (event.key in moves) {
      event.preventDefault()
      refocus.current = true
      show(order[(moves[event.key] + order.length) % order.length])
    } else if (event.key === 'Delete') {
      event.preventDefault()
      close(id)
    }
  }

  // The gap among the documents' tabs a point of the strip is nearest: before the first tab whose
  // middle is past it, else after the last.
  function gapAt(x: number): number {
    const tabs = [...(strip.current?.querySelectorAll<HTMLElement>(`[id^="${TAB_ID}"]`) ?? [])]
    const after = tabs.findIndex((tab) => {
      const box = tab.getBoundingClientRect()
      return x < box.left + box.width / 2
    })
    return after < 0 ? tabs.length : after
  }

  // A drag over the strip is heard only for a tab of its own: files and the library's rows go
  // to the library, and another window's tab belongs to its own strip.
  function draggingTab(event: DragEvent): number | null {
    return event.dataTransfer.types.includes(TAB_TYPE) ? dragged.current : null
  }

  function onDragOver(event: DragEvent<HTMLDivElement>) {
    const id = draggingTab(event)
    if (id == null) return
    event.preventDefault()
    event.dataTransfer.dropEffect = 'move'
    // Near an end, a strip wider than the pane scrolls on: dragover comes again and again while the pointer rests.
    const box = event.currentTarget.getBoundingClientRect()
    if (event.clientX < box.left + EDGE) event.currentTarget.scrollLeft -= SCROLL_STEP
    else if (event.clientX > box.right - EDGE) event.currentTarget.scrollLeft += SCROLL_STEP
    // The line shows where the tab lands, which for one dragged past the end of its group, the
    // pinned or the rest, is that end.
    const from = openTabs.findIndex((tab) => tab.id === id)
    const to = moved(openTabs, id, droppedAt(from, gapAt(event.clientX))).findIndex((tab) => tab.id === id)
    setGap(to === from ? null : to > from ? to + 1 : to)
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    const id = draggingTab(event)
    setGap(null)
    if (id == null) return
    event.preventDefault()
    const from = openTabs.findIndex((tab) => tab.id === id)
    if (from >= 0) workspace.moveTab(id, droppedAt(from, gapAt(event.clientX)))
  }

  const base =
    'group flex h-9 shrink-0 items-center gap-2 border-r border-line px-3 text-[13px] whitespace-nowrap transition-colors focus-visible:-outline-offset-2!'
  const inactive = 'text-muted hover:bg-sunken/60 hover:text-ink'
  const active = 'bg-page font-medium text-ink shadow-[inset_0_-2px_0_var(--color-accent)]'

  return (
    <div
      ref={strip}
      role="tablist"
      aria-label="Open documents"
      // A vertical wheel scrolls the strip sideways; it has no vertical extent anyway.
      onWheel={(event) => {
        event.currentTarget.scrollLeft += event.deltaY
      }}
      onDragOver={onDragOver}
      onDragLeave={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setGap(null)
      }}
      onDrop={onDrop}
      className="no-scrollbar flex shrink-0 items-stretch overflow-x-auto border-b border-line bg-surface"
    >
      {openTabs.map((tab, index) => {
        const document = workspace.openDocuments.get(tab.id)
        const selected = tab.id === activeDocumentId
        const kind = document ? fileKind(document) : null
        const Icon = kind?.icon ?? FileText
        // Where the dragged tab would drop: a line at this tab's left edge, or the last's right.
        const mark = gap === index ? DROP_BEFORE : gap === openTabs.length && index === openTabs.length - 1 ? DROP_AFTER : ''
        return (
          <div
            key={tab.id}
            id={`${TAB_ID}${tab.id}`}
            role="tab"
            aria-selected={selected}
            aria-controls={selected ? DOCUMENT_PANEL : undefined}
            aria-keyshortcuts={`Delete ${moveKeys.named}`}
            tabIndex={selected ? 0 : -1}
            title={document?.title ?? document?.filename}
            draggable
            onDragStart={(event) => {
              dragged.current = tab.id
              event.dataTransfer.setData(TAB_TYPE, String(tab.id))
              event.dataTransfer.effectAllowed = 'move'
            }}
            onDragEnd={() => {
              dragged.current = null
              setGap(null)
            }}
            onClick={() => workspace.openDocument(tab.id, tab.focus ?? null)}
            onKeyDown={(event) => onKeyDown(event, tab.id)}
            onAuxClick={(event) => {
              if (event.button === 1) workspace.closeTabs([tab.id])
            }}
            // Middle-clicking would otherwise start the browser's autoscroll.
            onMouseDown={(event) => {
              if (event.button === 1) event.preventDefault()
            }}
            onContextMenu={(event) => document && tabMenu(event, document)}
            className={`${base} relative max-w-52 cursor-pointer pr-1.5 ${selected ? active : inactive} ${mark}`}
          >
            <Icon className={`size-4 shrink-0 ${kind?.color ?? ''}`} />
            <span className="min-w-0 truncate">{document?.filename ?? `Document ${tab.id}`}</span>
            {tab.pinned && <span className="sr-only">, pinned</span>}
            {document && <StatusDot document={document} />}
            {tab.pinned ? (
              <span
                aria-hidden
                title="Unpin tab"
                onClick={(event) => {
                  event.stopPropagation()
                  workspace.pinTab(tab.id, false)
                }}
                className="-mr-0.5 rounded p-0.5 text-muted transition hover:bg-line hover:text-ink"
              >
                <Pin className="size-3.5" />
              </span>
            ) : (
              <span
                aria-hidden
                title="Close tab"
                onClick={(event) => {
                  event.stopPropagation()
                  workspace.closeTabs([tab.id])
                }}
                className={`-mr-0.5 rounded p-0.5 text-muted transition hover:bg-line hover:text-ink ${selected ? '' : 'opacity-0 group-hover:opacity-100'}`}
              >
                <X className="size-3.5" />
              </span>
            )}
          </div>
        )
      })}
      {menu.element}
    </div>
  )
}

// What a dragged tab carries, so that nothing else takes it for files or the library's rows.
const TAB_TYPE = 'application/x-emudoc-tab'
// How near an end of the strip a dragged tab scrolls it, and by how much each time, in pixels.
const EDGE = 40
const SCROLL_STEP = 12
// The line that shows where a dragged tab would drop, over the border between two tabs.
const DROP_BEFORE = 'before:absolute before:inset-y-1.5 before:-left-px before:w-0.5 before:rounded-full before:bg-accent'
const DROP_AFTER = 'after:absolute after:inset-y-1.5 after:-right-px after:w-0.5 after:rounded-full after:bg-accent'

/** The panel below the tabs, which shows the open document. */
export const DOCUMENT_PANEL = 'document-panel'

/** The start of an open document's tab's id, its document's id after it, which names the panel while it is open. */
export const TAB_ID = 'document-tab-'
