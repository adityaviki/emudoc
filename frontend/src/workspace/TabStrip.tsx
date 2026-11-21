import { Download, FileText, Lock, Pin, PinOff, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import type { DragEvent, KeyboardEvent, MouseEvent } from 'react'
import { myFileUrl } from '../api.ts'
import type { MyFileDetail } from '../api.ts'
import { StatusDot } from '../components/IndexingStatus.tsx'
import type { MenuItem } from '../components/Menu.tsx'
import { useContextMenu } from '../components/useContextMenu.tsx'
import { servable } from '../myFiles.ts'
import { onMac, tabMoveKeys, tabMoveOf } from '../shortcuts.ts'
import { fileKind, myFileKind, originalFileItem } from './files.ts'
import { closedByMenu, droppedAt, moved, tabKey, useWorkspace } from './state.ts'
import type { OpenTab, TabKey } from './state.ts'

/**
 * One tab per open document above the middle pane, and per open file of the member's My files among them, marked by a
 * lock as theirs alone. The tabs are one stop of the keyboard: the shown tab takes focus, the arrows, Home and End show another,
 * and Delete closes it. The close button is the pointer's alone, hidden from screen readers, since
 * a tab's content is read as its name and cannot hold a button of its own. A document's tab is
 * put in another place by a drag, by Ctrl+Shift and an arrow, or by its menu's "Move left" and
 * "Move right". A pinned tab stays before the
 * others, shows its pin where the others' close button is, and is left open by "Close others" and
 * "Close all"; it closes by its menu's "Close", by Delete and by a middle click. The tabs share the
 * strip: each as wide as its name (up to a cap) while they fit, then narrower together as more
 * open, the name and then the status dot giving way, down to the icon and the close button, past
 * which the strip scrolls. A tab the mouse closes leaves the others as wide as they were until it
 * leaves the strip, so the next tab's close button comes under it.
 */
export function TabStrip() {
  const workspace = useWorkspace()
  const { activeTab, openTabs } = workspace
  const strip = useRef<HTMLDivElement>(null)
  const menu = useContextMenu()
  // Set by a key that moves or closes the open tab, whose tab then takes the focus.
  const refocus = useRef(false)
  // The tab being dragged, and the gap it would drop into (the number of the tabs before it),
  // while that would move it.
  const dragged = useRef<TabKey | null>(null)
  const [gap, setGap] = useState<number | null>(null)
  // The tabs' widths by key when the mouse closed one, kept while it stays on the strip, so
  // that they do not widen into the room left (Chrome's way); and the kind of pointer last pressed
  // there, since a touch leaves nothing resting on the strip to wait for.
  const [held, setHeld] = useState<ReadonlyMap<TabKey, number> | null>(null)
  const pointer = useRef('')
  // The tabs in the order the keys move through them.
  const order = openTabs.map(tabKey)
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
  }, [activeTab, orderKey])

  function tabMenu(event: MouseEvent, tab: OpenTab) {
    const key = tabKey(tab)
    const others = closedByMenu(openTabs, key)
    const all = closedByMenu(openTabs)
    const index = openTabs.findIndex((open) => tabKey(open) === key)
    const pinned = openTabs[index]?.pinned ?? false
    const document = tab.myFile ? undefined : workspace.openDocuments.get(tab.id)
    const file = tab.myFile ? workspace.openMyFiles.get(tab.id) : undefined
    menu.open(event, [
      { label: 'Close', shortcut: 'Delete', onSelect: () => workspace.closeTabs([key]) },
      { label: 'Close others', disabled: others.length === 0, onSelect: () => workspace.closeTabs(others) },
      { label: 'Close all', disabled: all.length === 0, onSelect: () => workspace.closeTabs(all) },
      'separator',
      pinned
        ? { label: 'Unpin', icon: PinOff, onSelect: () => workspace.pinTab(key, false) }
        : { label: 'Pin', icon: Pin, onSelect: () => workspace.pinTab(key, true) },
      // The ways to move a tab without a drag (WCAG 2.5.7), as its keys are the keyboard's; a
      // tab moves among those pinned as it is, or not.
      {
        label: 'Move left',
        shortcut: moveKeys.left,
        disabled: moved(openTabs, key, index - 1) === openTabs,
        onSelect: () => workspace.moveTab(key, index - 1),
      },
      {
        label: 'Move right',
        shortcut: moveKeys.right,
        disabled: moved(openTabs, key, index + 1) === openTabs,
        onSelect: () => workspace.moveTab(key, index + 1),
      },
      ...(document ? ['separator' as const, originalFileItem(document)] : []),
      ...(file ? ['separator' as const, myFileDownloadItem(file)] : []),
    ])
  }

  function show(key: TabKey) {
    const tab = openTabs.find((open) => tabKey(open) === key)
    if (tab?.myFile) workspace.openMyFile(tab.id)
    else if (tab) workspace.openDocument(tab.id, tab.focus ?? null)
  }

  function close(key: TabKey) {
    // The last tab takes the middle pane with it: the keyboard goes back to the library.
    if (order.length === 1) window.document.querySelector<HTMLElement>('[role="tree"]')?.focus()
    else refocus.current = true
    workspace.closeTabs([key])
  }

  // A close by the cross or a middle click.
  function closeByPointer(key: TabKey) {
    if (pointer.current === 'mouse') {
      setHeld(new Map(tabElements().map((tab) => [tab.id.slice(TAB_ID.length), tab.getBoundingClientRect().width])))
    }
    workspace.closeTabs([key])
  }

  function onKeyDown(event: KeyboardEvent, key: TabKey) {
    const step = tabMoveOf(event, mac)
    if (step != null) {
      // The arrows with these keys show no other tab.
      event.preventDefault()
      const index = order.indexOf(key)
      // At an end of its group the tab stays, and nothing is drawn again to take the focus back.
      if (moved(openTabs, key, index + step) === openTabs) return
      refocus.current = true
      workspace.moveTab(key, index + step)
      return
    }
    const index = order.indexOf(key)
    const moves: Record<string, number> = { ArrowLeft: index - 1, ArrowRight: index + 1, Home: 0, End: order.length - 1 }
    if (event.key in moves) {
      event.preventDefault()
      refocus.current = true
      show(order[(moves[event.key] + order.length) % order.length])
    } else if (event.key === 'Delete') {
      event.preventDefault()
      close(key)
    }
  }

  function tabElements(): HTMLElement[] {
    return [...(strip.current?.querySelectorAll<HTMLElement>(`[id^="${TAB_ID}"]`) ?? [])]
  }

  // The gap among the tabs a point of the strip is nearest: before the first tab whose
  // middle is past it, else after the last.
  function gapAt(x: number): number {
    const tabs = tabElements()
    const after = tabs.findIndex((tab) => {
      const box = tab.getBoundingClientRect()
      return x < box.left + box.width / 2
    })
    return after < 0 ? tabs.length : after
  }

  // A drag over the strip is heard only for a tab of its own: files and the library's rows go
  // to the library, and another window's tab belongs to its own strip.
  function draggingTab(event: DragEvent): TabKey | null {
    return event.dataTransfer.types.includes(TAB_TYPE) ? dragged.current : null
  }

  function onDragOver(event: DragEvent<HTMLDivElement>) {
    const key = draggingTab(event)
    if (key == null) return
    event.preventDefault()
    event.dataTransfer.dropEffect = 'move'
    // Near an end, a strip wider than the pane scrolls on: dragover comes again and again while the pointer rests.
    const box = event.currentTarget.getBoundingClientRect()
    if (event.clientX < box.left + EDGE) event.currentTarget.scrollLeft -= SCROLL_STEP
    else if (event.clientX > box.right - EDGE) event.currentTarget.scrollLeft += SCROLL_STEP
    // The line shows where the tab lands, which for one dragged past the end of its group, the
    // pinned or the rest, is that end.
    const from = order.indexOf(key)
    const to = moved(openTabs, key, droppedAt(from, gapAt(event.clientX))).map(tabKey).indexOf(key)
    setGap(to === from ? null : to > from ? to + 1 : to)
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    const key = draggingTab(event)
    setGap(null)
    if (key == null) return
    event.preventDefault()
    const from = order.indexOf(key)
    if (from >= 0) workspace.moveTab(key, droppedAt(from, gapAt(event.clientX)))
  }

  // Each tab grows from nothing to its share of the strip (flex-1), no wider than its content
  // (max-w-fit, the name capped by NAME_WIDTH) and no narrower than its padding, icon, gaps and
  // close button (min-w-14.75, 59px: 12 + 16 + 4 + 4 + 16 + 6 and the 1px border).
  const base =
    'group flex h-9 min-w-14.75 max-w-fit flex-1 items-center gap-1 border-r border-line px-3 text-[13px] whitespace-nowrap transition-colors focus-visible:-outline-offset-2!'
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
      onPointerDown={(event) => {
        pointer.current = event.pointerType
      }}
      onPointerLeave={() => setHeld(null)}
      onDragOver={onDragOver}
      onDragLeave={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setGap(null)
      }}
      onDrop={onDrop}
      className="no-scrollbar flex shrink-0 items-stretch overflow-x-auto border-b border-line bg-surface"
    >
      {openTabs.map((tab, index) => {
        const key = tabKey(tab)
        const document = tab.myFile ? undefined : workspace.openDocuments.get(tab.id)
        const file = tab.myFile ? workspace.openMyFiles.get(tab.id) : undefined
        const selected = key === activeTab
        const kind = document ? fileKind(document) : file ? myFileKind(file.kind) : null
        const Icon = kind?.icon ?? FileText
        const name = document?.filename ?? file?.name ?? (tab.myFile ? `File ${tab.id}` : `Document ${tab.id}`)
        // Where the dragged tab would drop: a line at this tab's left edge, or the last's right.
        const mark = gap === index ? DROP_BEFORE : gap === openTabs.length && index === openTabs.length - 1 ? DROP_AFTER : ''
        const width = held?.get(key)
        return (
          <div
            key={key}
            id={`${TAB_ID}${key}`}
            role="tab"
            aria-selected={selected}
            aria-controls={selected ? DOCUMENT_PANEL : undefined}
            aria-keyshortcuts={`Delete ${moveKeys.named}`}
            tabIndex={selected ? 0 : -1}
            title={document?.title ?? document?.filename ?? file?.name}
            draggable
            onDragStart={(event) => {
              dragged.current = key
              event.dataTransfer.setData(TAB_TYPE, key)
              event.dataTransfer.effectAllowed = 'move'
            }}
            onDragEnd={() => {
              dragged.current = null
              setGap(null)
            }}
            onClick={() => show(key)}
            onKeyDown={(event) => onKeyDown(event, key)}
            onAuxClick={(event) => {
              if (event.button === 1) closeByPointer(key)
            }}
            // Middle-clicking would otherwise start the browser's autoscroll.
            onMouseDown={(event) => {
              if (event.button === 1) event.preventDefault()
            }}
            onContextMenu={(event) => tabMenu(event, tab)}
            style={width == null ? undefined : { flex: 'none', width }}
            className={`${base} relative cursor-pointer pr-1.5 ${selected ? active : inactive} ${mark}`}
          >
            <Icon className={`size-4 shrink-0 ${kind?.color ?? ''}`} />
            {/* The name, then the dot, clipped as the tab narrows; their spacing is their own
                (pl-1, gap-2, mr-1), so that nothing of it is left once both are gone. */}
            <span className={`flex min-w-0 items-center gap-2 overflow-hidden ${NAME_WIDTH}`}>
              <span className="min-w-0 truncate pl-1">{name}</span>
              {tab.myFile && <span className="sr-only">, in My files</span>}
              {tab.pinned && <span className="sr-only">, pinned</span>}
              {document && <StatusDot document={document} className="mr-1 size-1.5" />}
              {tab.myFile && <Lock aria-hidden className="mr-1 size-3 shrink-0 text-muted" />}
            </span>
            {tab.pinned ? (
              <span
                aria-hidden
                title="Unpin tab"
                onClick={(event) => {
                  event.stopPropagation()
                  workspace.pinTab(key, false)
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
                  closeByPointer(key)
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

// The widest the name and its dot are, 149px, which makes a tab at most 13rem with the 59px of
// padding, icon, gaps, close button and border beside them (the narrowest a tab goes, in `base`).
const NAME_WIDTH = 'max-w-37.25'

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

/** The start of an open tab's id, its key after it (`tabKey`), which names the panel while it is open. */
export const TAB_ID = 'document-tab-'

/** A file of My files' tab's last item: its newest version downloaded, once it may be. */
function myFileDownloadItem(file: MyFileDetail): MenuItem {
  return {
    label: 'Download',
    icon: Download,
    disabled: !servable(file.latest),
    // Followed as a click on the link would be, so the download leaves the page where it is.
    onSelect: () => {
      const anchor = window.document.createElement('a')
      Object.assign(anchor, { href: myFileUrl(file.id, file.latest.number), download: '' })
      anchor.click()
    },
  }
}
