import { FileText, Search, X } from 'lucide-react'
import { useEffect, useRef } from 'react'
import type { KeyboardEvent, MouseEvent } from 'react'
import type { Document } from '../api.ts'
import { StatusDot } from '../components/IndexingStatus.tsx'
import { useContextMenu } from '../components/useContextMenu.tsx'
import { searchTabName } from '../search.ts'
import { fileKind, originalFileItem } from './files.ts'
import { useWorkspace } from './state.ts'
import { searching } from './url.ts'

/**
 * One tab per open document above the middle pane, after the search's while it is open. The tabs
 * are one stop of the keyboard: the shown tab takes focus, the arrows, Home and End show another,
 * and Delete closes it. The close button is the pointer's alone, hidden from screen readers, since
 * a tab's content is read as its name and cannot hold a button of its own.
 */
export function TabStrip() {
  const workspace = useWorkspace()
  const { activeDocumentId, openTabs, url } = workspace
  const strip = useRef<HTMLDivElement>(null)
  const menu = useContextMenu()
  // Set by a key that moves or closes the open tab, whose tab then takes the focus.
  const refocus = useRef(false)
  const search = url.search
  const searchShown = searching(url)
  // The tabs in the order the keys move through them: the search's first, null standing for it.
  const order: (number | null)[] = [...(search ? [null] : []), ...openTabs.map((tab) => tab.id)]

  // Keep the active tab in view when it changes, and in focus when the keyboard changed it.
  useEffect(() => {
    const tab = strip.current?.querySelector<HTMLElement>('[aria-selected="true"]')
    tab?.scrollIntoView({ inline: 'nearest', block: 'nearest' })
    if (refocus.current) tab?.focus()
    refocus.current = false
  }, [activeDocumentId, searchShown])

  function tabMenu(event: MouseEvent, document: Document) {
    const others = openTabs.filter((tab) => tab.id !== document.id).map((tab) => tab.id)
    menu.open(event, [
      { label: 'Close', shortcut: 'Delete', onSelect: () => workspace.closeTabs([document.id]) },
      { label: 'Close others', disabled: others.length === 0, onSelect: () => workspace.closeTabs(others) },
      { label: 'Close all', onSelect: () => workspace.closeTabs(openTabs.map((tab) => tab.id)) },
      'separator',
      originalFileItem(document),
    ])
  }

  function show(id: number | null) {
    if (id == null) {
      if (search) workspace.showSearch(search)
      return
    }
    const tab = openTabs.find((open) => open.id === id)
    workspace.openDocument(id, tab?.focus ?? null)
  }

  function close(id: number | null) {
    // The last tab takes the middle pane with it: the keyboard goes back to the library.
    if (order.length === 1) window.document.querySelector<HTMLElement>('[role="tree"]')?.focus()
    else refocus.current = true
    if (id == null) workspace.closeSearch()
    else workspace.closeTabs([id])
  }

  function onKeyDown(event: KeyboardEvent, id: number | null) {
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
      className="no-scrollbar flex shrink-0 items-stretch overflow-x-auto border-b border-line bg-surface"
    >
      {search && (
        <div
          id={SEARCH_TAB}
          role="tab"
          aria-selected={searchShown}
          aria-controls={searchShown ? DOCUMENT_PANEL : undefined}
          aria-keyshortcuts="Delete"
          tabIndex={searchShown ? 0 : -1}
          title={searchTabName(search.q)}
          onClick={() => show(null)}
          onKeyDown={(event) => onKeyDown(event, null)}
          onAuxClick={(event) => {
            if (event.button === 1) workspace.closeSearch()
          }}
          onMouseDown={(event) => {
            if (event.button === 1) event.preventDefault()
          }}
          onContextMenu={(event) => menu.open(event, [{ label: 'Close', shortcut: 'Delete', onSelect: workspace.closeSearch }])}
          className={`${base} max-w-60 cursor-pointer pr-1.5 ${searchShown ? active : inactive}`}
        >
          <Search className="size-4 shrink-0 text-accent" />
          <span className="min-w-0 truncate">{searchTabName(search.q)}</span>
          <span
            aria-hidden
            title="Close the search"
            onClick={(event) => {
              event.stopPropagation()
              workspace.closeSearch()
            }}
            className={`-mr-0.5 rounded p-0.5 text-muted transition hover:bg-line hover:text-ink ${searchShown ? '' : 'opacity-0 group-hover:opacity-100'}`}
          >
            <X className="size-3.5" />
          </span>
        </div>
      )}
      {openTabs.map((tab) => {
        const document = workspace.openDocuments.get(tab.id)
        const selected = tab.id === activeDocumentId
        const kind = document ? fileKind(document) : null
        const Icon = kind?.icon ?? FileText
        return (
          <div
            key={tab.id}
            id={`${TAB_ID}${tab.id}`}
            role="tab"
            aria-selected={selected}
            aria-controls={selected ? DOCUMENT_PANEL : undefined}
            aria-keyshortcuts="Delete"
            tabIndex={selected ? 0 : -1}
            title={document?.title ?? document?.filename}
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
            className={`${base} max-w-52 cursor-pointer pr-1.5 ${selected ? active : inactive}`}
          >
            <Icon className={`size-4 shrink-0 ${kind?.color ?? ''}`} />
            <span className="min-w-0 truncate">{document?.filename ?? `Document ${tab.id}`}</span>
            {document && <StatusDot document={document} />}
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
          </div>
        )
      })}
      {menu.element}
    </div>
  )
}

/** The panel below the tabs, which shows the open document or the search. */
export const DOCUMENT_PANEL = 'document-panel'

/** The search's tab, which names the panel while it shows. */
export const SEARCH_TAB = 'search-tab'

/** The start of an open document's tab's id, its document's id after it, which names the panel while it is open. */
export const TAB_ID = 'document-tab-'
