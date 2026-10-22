import { useQuery } from '@tanstack/react-query'
import { ListTree, X } from 'lucide-react'
import { Fragment, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { api, fileUrl, isImage, isPdf, isSheet, pageImageUrl, pageLabel, sectionPath } from '../api.ts'
import type { Block, Document, DocumentContent, Section } from '../api.ts'
import { IndexingStatus } from '../components/IndexingStatus.tsx'
import { PipeTable } from '../components/Markdown.tsx'
import { Chip, IconButton, Loading, Notice } from '../components/ui.tsx'
import { formatDate, parsePipeTable } from '../format.ts'
import { useStatus } from '../hooks.ts'
import { scanPassed } from '../indexing.ts'
import { describeDocumentUsage } from '../usage.ts'
import { originalFile } from './files.ts'
import { useWorkspace } from './state.ts'
import type { Focus } from './url.ts'

/** The blocks to highlight and scroll to; `end` is exclusive. */
interface BlockFocus {
  start: number
  end: number
}

interface DocumentTabProps {
  document: Document
  focus: Focus | null
  onClearFocus: () => void
}

/** One open document: a PDF's rendered pages, or anything else's extracted text with the outline beside it. */
export function DocumentTab({ document, focus, onClearFocus }: DocumentTabProps) {
  const workspace = useWorkspace()
  const [outlineOpen, setOutlineOpen] = useState(true)
  const status = document.status
  const indexed = document.stages.structure?.status === 'done'
  // The original is served once its malware scan has passed, and never when it was flagged.
  const openable = scanPassed(document) && status !== 'quarantined'
  const original = originalFile(document)
  // Derived data is fetched again whenever indexing changes the document's status.
  const outline = useQuery({
    queryKey: ['outline', document.id, status],
    queryFn: () => api.outline(document.id),
    enabled: indexed,
  })
  // For the currency the document's cost is in.
  const health = useStatus()
  const usage = describeDocumentUsage(document.usage, health.data?.currency, document.page_count !== null)
  const content = useQuery({
    queryKey: ['content', document.id, status],
    queryFn: () => api.content(document.id),
    enabled: indexed,
  })
  const passageId = focus && 'passage' in focus ? focus.passage : null
  const passage = useQuery({
    queryKey: ['passage', passageId],
    queryFn: () => api.passage(passageId!),
    enabled: passageId != null,
  })

  let blockFocus: BlockFocus | null = null
  if (passage.data && passage.data.document_id === document.id) {
    blockFocus = { start: passage.data.block_start, end: passage.data.block_end }
  } else if (focus && 'block' in focus) {
    blockFocus = { start: focus.block, end: focus.block + 1 }
  }
  const focusStart = blockFocus?.start
  const focusPage =
    passage.data?.page_start ??
    content.data?.blocks.find((block) => block.ordinal === focusStart)?.page ??
    (focus && 'page' in focus ? focus.page : undefined)

  // A PDF shows its rendered pages, with the text laid over them; everything else shows its text.
  const pages = isPdf(document)
  const hasOutline = !pages && !isImage(document) && !isSheet(document)

  // The outline, when it arrives, narrows the text beside it and moves every block down, so the
  // scroll to the focused block waits for it as well as for the text.
  const loaded = content.isSuccess && (!hasOutline || !outline.isPending)
  useEffect(() => {
    if (focusStart == null || !loaded) return
    const target = pages ? `page-${focusPage}` : `block-${focusStart}`
    // Smoothly, unless the system asks for less motion, which CSS cannot say for a script's scroll.
    const behavior = window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'
    window.document.getElementById(target)?.scrollIntoView({ block: 'center', behavior })
  }, [focusStart, focusPage, loaded, pages])

  const focusLabel =
    passage.data && blockFocus
      ? [sectionPath(passage.data.context, passage.data.document_title), pageLabel(passage.data.page_start, passage.data.page_end)]
          .filter(Boolean)
          .join(' · ')
      : focus && 'page' in focus
        ? `Page ${focus.page}`
        : focus && 'block' in focus
          ? 'Selected text'
          : null

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="shrink-0 border-b border-line bg-surface px-4 py-2.5">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="truncate text-[15px] font-semibold tracking-tight" title={document.title ?? document.filename}>
              {document.title ?? document.filename}
            </h2>
            <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
              {document.doc_type && <Chip tone="accent">{document.doc_type}</Chip>}
              {document.title && <span className="truncate">{document.filename}</span>}
              {document.doc_date && <span>dated {formatDate(document.doc_date)}</span>}
              {document.page_count && (
                <span>
                  {document.page_count} page{document.page_count === 1 ? '' : 's'}
                </span>
              )}
              {usage && (
                <span title={usage.detail}>{usage.summary}</span>
              )}
              <IndexingStatus document={document} />
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-1.5">
            {hasOutline && outline.data && outline.data.length > 0 && (
              <IconButton label={outlineOpen ? 'Hide outline' : 'Show outline'} onClick={() => setOutlineOpen((open) => !open)}>
                <ListTree className="size-4" />
              </IconButton>
            )}
            {openable && (
              <a
                {...original.link}
                title={original.label}
                className="inline-flex size-7 items-center justify-center rounded-md text-muted transition hover:bg-sunken hover:text-ink"
              >
                <original.icon className="size-4" />
              </a>
            )}
          </div>
        </div>
        {focusLabel && (
          <p className="mt-1.5 inline-flex max-w-full items-center gap-1 rounded-md bg-mark/40 py-0.5 pr-0.5 pl-2 text-xs">
            <span className="truncate">Showing: {focusLabel}</span>
            <IconButton label="Clear highlight" onClick={onClearFocus} className="size-5!">
              <X className="size-3.5" />
            </IconButton>
          </p>
        )}
      </header>

      {!indexed ? (
        <div className="p-4">
          <Notice tone={document.status === 'failed' || document.status === 'quarantined' ? 'bad' : 'info'}>
            {document.status === 'quarantined'
              ? (document.error ?? 'The malware scan flagged this file. It is quarantined: not indexed, and nobody can open it.')
              : document.status === 'failed'
                ? (document.error ?? 'Indexing failed.')
                : 'This document is being indexed. Its text will appear here in a moment.'}
          </Notice>
          {isImage(document) && openable && <ImagePreview document={document} />}
        </div>
      ) : content.isPending ? (
        <Loading />
      ) : content.isError ? (
        <div className="p-4">
          <Notice tone="bad">Could not load the text: {content.error.message}</Notice>
        </div>
      ) : pages ? (
        <Scrolled label={`Pages of ${document.filename}`} className="min-h-0 flex-1 overflow-y-auto">
          <PagesView document={document} content={content.data} focus={blockFocus} />
        </Scrolled>
      ) : isImage(document) ? (
        <Scrolled label={`Text of ${document.filename}`} className="min-h-0 flex-1 overflow-y-auto">
          <ImagePreview document={document} />
          <TextView blocks={content.data.blocks} focus={blockFocus} />
        </Scrolled>
      ) : isSheet(document) ? (
        <SheetView label={`Rows of ${document.filename}`} blocks={content.data.blocks} focus={blockFocus} />
      ) : (
        <div className="flex min-h-0 flex-1">
          {outlineOpen && outline.data && outline.data.length > 0 && (
            <aside aria-label="Outline" className="w-60 shrink-0 overflow-y-auto border-r border-line bg-surface p-2">
              <p className="px-2 pt-1 pb-2 text-[11px] font-medium tracking-wide text-muted uppercase">Outline</p>
              <ul>
                {outline.data.map((section) => (
                  <OutlineNode
                    key={section.id}
                    section={section}
                    focusStart={focusStart}
                    onSelect={(start) => workspace.openDocument(document.id, { block: start }, { replace: true })}
                  />
                ))}
              </ul>
            </aside>
          )}
          <Scrolled label={`Text of ${document.filename}`} className="min-w-0 flex-1 overflow-y-auto">
            <TextView blocks={content.data.blocks} focus={blockFocus} />
          </Scrolled>
        </div>
      )}
    </div>
  )
}

function ImagePreview({ document }: { document: Document }) {
  return (
    <div className="flex justify-center bg-sunken p-4">
      <a href={fileUrl(document.id)} target="_blank" rel="noreferrer" title="Open the original image">
        <img src={fileUrl(document.id)} alt={document.filename} className="max-h-[50vh] max-w-full rounded object-contain shadow-sm" />
      </a>
    </div>
  )
}

interface OutlineNodeProps {
  section: Section
  focusStart: number | undefined
  onSelect: (blockStart: number) => void
}

function OutlineNode({ section, focusStart, onSelect }: OutlineNodeProps) {
  const inside = focusStart != null && focusStart >= section.block_start && focusStart < section.block_end
  // The deepest section holding the focus is the active one.
  const active = inside && !section.children.some((c) => focusStart >= c.block_start && focusStart < c.block_end)
  return (
    <li>
      <button
        onClick={() => onSelect(section.block_start)}
        className={`block w-full rounded-md px-2 py-1.5 text-left transition hover:bg-sunken ${
          active ? 'bg-accent-soft text-accent' : ''
        }`}
      >
        <span className="flex items-baseline justify-between gap-2">
          <span className="text-[13px] leading-snug font-medium">{section.title}</span>
          <span className="shrink-0 text-[11px] text-muted tabular-nums">{pageLabel(section.page_start, section.page_end)}</span>
        </span>
        {section.summary && (
          <span className="mt-0.5 line-clamp-2 text-xs leading-snug text-muted">{section.summary}</span>
        )}
      </button>
      {section.children.length > 0 && (
        <ul className="ml-2.5 border-l border-line pl-1.5">
          {section.children.map((child) => (
            <OutlineNode key={child.id} section={child} focusStart={focusStart} onSelect={onSelect} />
          ))}
        </ul>
      )}
    </li>
  )
}

const inFocus = (block: Block, focus: BlockFocus | null) =>
  focus != null && block.ordinal >= focus.start && block.ordinal < focus.end

function TextView({ blocks, focus }: { blocks: Block[]; focus: BlockFocus | null }) {
  // The blocks that open a new page get a divider above them.
  const opensPage = new Set<number>()
  let lastPage: number | null = null
  for (const block of blocks) {
    if (block.page != null && block.page !== lastPage) {
      opensPage.add(block.ordinal)
      lastPage = block.page
    }
  }
  return (
    <div className="mx-auto max-w-3xl space-y-3 px-8 py-6 leading-relaxed">
      {blocks.map((block) => (
        <Fragment key={block.ordinal}>
          {opensPage.has(block.ordinal) && (
            <div className="flex items-center gap-3 pt-3 text-[11px] font-medium tracking-wide text-muted uppercase select-none">
              <span className="h-px flex-1 bg-line" />
              Page {block.page}
              <span className="h-px flex-1 bg-line" />
            </div>
          )}
          <div
            id={`block-${block.ordinal}`}
            className={`rounded transition-colors ${inFocus(block, focus) ? '-mx-2 bg-mark/50 px-2 py-0.5' : ''}`}
          >
            <BlockBody block={block} />
          </div>
        </Fragment>
      ))}
    </div>
  )
}

function BlockBody({ block }: { block: Block }) {
  if (block.kind === 'heading') {
    const size = block.level === 1 ? 'text-lg' : block.level === 2 ? 'text-base' : 'text-sm'
    return <p className={`pt-3 font-semibold tracking-tight ${size}`}>{block.text}</p>
  }
  if (block.kind === 'table') {
    const rows = parsePipeTable(block.text)
    if (rows) return <PipeTable rows={rows} />
  }
  return (
    <p className={`whitespace-pre-wrap ${block.kind === 'list_item' ? 'pl-5' : ''}`}>{block.text}</p>
  )
}

interface PagesViewProps {
  document: Document
  content: DocumentContent
  focus: BlockFocus | null
}

/**
 * The rendered pages. Each block's text lies invisibly over its place on the page, so the text
 * can be selected, copied and found; the focused blocks are marked where they sit.
 */
function PagesView({ document, content, focus }: PagesViewProps) {
  return (
    <div className="space-y-5 bg-sunken p-5">
      {content.page_sizes.map(([width, height], index) => {
        const page = index + 1
        const placed = content.blocks.filter((block) => block.page === page && block.bbox)
        return (
          <div
            key={page}
            id={`page-${page}`}
            className="relative mx-auto max-w-3xl overflow-hidden rounded-md bg-white shadow-md shadow-ink/8 ring-1 ring-ink/8 @container"
            style={{ aspectRatio: `${width} / ${height}` }}
          >
            <img
              src={pageImageUrl(document.id, page)}
              alt={`Page ${page}`}
              loading="lazy"
              draggable={false}
              className="size-full select-none"
            />
            {placed.map((block) => {
              const [x0, top, x1, bottom] = block.bbox!
              const box = {
                left: `${(x0 / width) * 100}%`,
                top: `${(top / height) * 100}%`,
                width: `${((x1 - x0) / width) * 100}%`,
                height: `${((bottom - top) / height) * 100}%`,
              }
              return (
                <Fragment key={block.ordinal}>
                  <span
                    className="page-text"
                    // Sized in container units so the text scales with the page.
                    style={{ ...box, fontSize: `${(fitFontSize(block.text, x1 - x0, bottom - top) / width) * 100}cqw` }}
                  >
                    {block.text}
                  </span>
                  {inFocus(block, focus) && (
                    <div className="pointer-events-none absolute rounded-sm bg-amber-300/45 mix-blend-multiply" style={box} />
                  )}
                </Fragment>
              )
            })}
          </div>
        )
      })}
    </div>
  )
}

/**
 * A font size at which `text` about fills a box of `width` x `height`, in the box's units. Taking
 * a glyph as 0.5em wide and a line as 1em tall, the glyphs cover 0.5·size² each, so size² ≈ area / (0.5·n).
 */
function fitFontSize(text: string, width: number, height: number): number {
  const chars = Math.max(1, text.length)
  return Math.min(height, Math.sqrt((width * height) / (0.5 * chars)))
}

interface Sheet {
  title: string
  blocks: Block[]
}

/** A spreadsheet: one tab per sheet (its heading block), the sheet's tables below. */
function SheetView({ label, blocks, focus }: { label: string; blocks: Block[]; focus: BlockFocus | null }) {
  const sheets: Sheet[] = []
  for (const block of blocks) {
    if (block.kind === 'heading' && block.level === 1) {
      sheets.push({ title: block.text, blocks: [] })
    } else {
      if (!sheets.length) sheets.push({ title: 'Data', blocks: [] })
      sheets[sheets.length - 1].blocks.push(block)
    }
  }
  const focusedIndex = sheets.findIndex((sheet) => sheet.blocks.some((block) => inFocus(block, focus)))
  const [selected, setSelected] = useState(Math.max(0, focusedIndex))
  // A new focus brings its sheet to the front; the user may then switch sheets freely.
  const [lastFocused, setLastFocused] = useState(focusedIndex)
  if (focusedIndex !== lastFocused) {
    setLastFocused(focusedIndex)
    if (focusedIndex >= 0) setSelected(focusedIndex)
  }
  const sheet = sheets[Math.min(selected, sheets.length - 1)]

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {sheets.length > 1 && (
        <div className="no-scrollbar flex shrink-0 gap-1 overflow-x-auto border-b border-line bg-surface px-3 py-1.5 text-[13px]">
          {sheets.map((item, index) => (
            <button
              key={index}
              type="button"
              aria-pressed={index === selected}
              onClick={() => setSelected(index)}
              className={`h-7 shrink-0 rounded-md px-2.5 transition ${
                index === selected ? 'bg-sunken font-medium text-ink' : 'text-muted hover:bg-sunken/60 hover:text-ink'
              }`}
            >
              {item.title}
            </button>
          ))}
        </div>
      )}
      <Scrolled label={label} className="min-h-0 flex-1 overflow-auto p-4">
        <div className="space-y-4">
          {sheet?.blocks.map((block) => (
            <div
              key={block.ordinal}
              id={`block-${block.ordinal}`}
              className={`rounded ${inFocus(block, focus) ? 'ring-2 ring-accent/60' : ''}`}
            >
              <BlockBody block={block} />
            </div>
          ))}
        </div>
      </Scrolled>
    </div>
  )
}

/**
 * What scrolls in the pane: a stop of the keyboard, named for screen readers, so that the arrows
 * and Page Down scroll a text that holds nothing else to focus.
 */
function Scrolled({ label, className, children }: { label: string; className: string; children: ReactNode }) {
  return (
    <div role="region" aria-label={label} tabIndex={0} className={`focus-visible:-outline-offset-2! ${className}`}>
      {children}
    </div>
  )
}
