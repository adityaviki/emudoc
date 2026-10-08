import { ExternalLink, FileText, X } from 'lucide-react'
import { useEffect, useRef } from 'react'
import type { MouseEvent } from 'react'
import { fileUrl } from '../api.ts'
import type { Document } from '../api.ts'
import { StatusDot } from '../components/IndexingStatus.tsx'
import { useContextMenu } from '../components/useContextMenu.tsx'
import { fileKind } from './files.ts'
import { useWorkspace } from './state.ts'

/** One tab per open document, above the document pane. */
export function TabStrip() {
  const workspace = useWorkspace()
  const { activeDocumentId, openTabs } = workspace
  const strip = useRef<HTMLDivElement>(null)
  const menu = useContextMenu()

  // Keep the active tab in view when it changes.
  useEffect(() => {
    strip.current
      ?.querySelector('[aria-selected="true"]')
      ?.scrollIntoView({ inline: 'nearest', block: 'nearest' })
  }, [activeDocumentId])

  function tabMenu(event: MouseEvent, document: Document) {
    const others = openTabs.filter((tab) => tab.id !== document.id).map((tab) => tab.id)
    menu.open(event, [
      { label: 'Close', shortcut: 'middle-click', onSelect: () => workspace.closeTabs([document.id]) },
      { label: 'Close others', disabled: others.length === 0, onSelect: () => workspace.closeTabs(others) },
      { label: 'Close all', onSelect: () => workspace.closeTabs(openTabs.map((tab) => tab.id)) },
      'separator',
      {
        label: 'Open original file',
        icon: ExternalLink,
        onSelect: () => window.open(fileUrl(document.id), '_blank', 'noreferrer'),
      },
    ])
  }

  const base = 'group flex h-9 shrink-0 items-center gap-1.5 border-r border-line px-3 text-sm whitespace-nowrap'
  const inactive = 'text-muted hover:bg-sunken hover:text-ink'
  const active = 'bg-page text-ink shadow-[inset_0_-2px_0_var(--color-accent)]'

  return (
    <div
      ref={strip}
      role="tablist"
      // A vertical wheel scrolls the strip sideways; it has no vertical extent anyway.
      onWheel={(event) => {
        event.currentTarget.scrollLeft += event.deltaY
      }}
      className="no-scrollbar flex shrink-0 items-stretch overflow-x-auto border-b border-line bg-surface"
    >
      {openTabs.map((tab) => {
        const document = workspace.documents.find((d) => d.id === tab.id)
        const selected = tab.id === activeDocumentId
        const kind = document ? fileKind(document) : null
        const Icon = kind?.icon ?? FileText
        return (
          <div
            key={tab.id}
            role="tab"
            aria-selected={selected}
            title={document?.title ?? document?.filename}
            onClick={() => workspace.openDocument(tab.id, tab.focus ?? null)}
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
            <button
              type="button"
              aria-label="Close tab"
              onClick={(event) => {
                event.stopPropagation()
                workspace.closeTabs([tab.id])
              }}
              className={`ml-0.5 rounded p-0.5 hover:bg-line ${selected ? '' : 'opacity-0 group-hover:opacity-100 focus-visible:opacity-100'}`}
            >
              <X className="size-3.5" />
            </button>
          </div>
        )
      })}
      {menu.element}
    </div>
  )
}
