// A document's content as the pages show it, read only: its extracted text, a PDF's rendered pages with the text laid
// over them, a spreadsheet's sheets, and an image. The document tab shows a member's (workspace/DocumentTab.tsx), and
// a share link's page its visitor's (shared/SharedPage.tsx), each giving where its files are.

import { Fragment, useState } from 'react'
import type { CSSProperties, ReactNode } from 'react'
import type { Block, DocumentContent } from '../api.ts'
import { parsePipeTable } from '../format.ts'
import { PipeTable } from './Markdown.tsx'

/** The blocks to highlight and scroll to; `end` is exclusive. */
export interface BlockFocus {
  start: number
  end: number
}

/** An image document as it was uploaded, which opens whole in a new tab. */
export function ImagePreview({ src, alt }: { src: string; alt: string }) {
  return (
    <div className="flex justify-center bg-sunken p-4">
      <a href={src} target="_blank" rel="noreferrer" title="Open the original image">
        <img src={src} alt={alt} className="max-h-[50vh] max-w-full rounded object-contain shadow-sm" />
      </a>
    </div>
  )
}



const inFocus = (block: Block, focus: BlockFocus | null) =>
  focus != null && block.ordinal >= focus.start && block.ordinal < focus.end

export function TextView({ blocks, focus }: { blocks: Block[]; focus: BlockFocus | null }) {
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
    <div className="mx-auto max-w-3xl space-y-3 px-8 py-6 leading-relaxed print:max-w-none print:px-0">
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
            data-block={block.ordinal}
            className={`rounded transition-colors ${inFocus(block, focus) ? '-mx-2 bg-mark/50 px-2 py-0.5 print-exact' : ''}`}
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
    return <p className={`pt-3 font-semibold tracking-tight print:break-after-avoid ${size}`}>{block.text}</p>
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
  /** Where the image of a page is, by its number from 1. */
  pageUrl: (page: number) => string
  content: DocumentContent
  focus: BlockFocus | null
}

/**
 * The rendered pages. Each block's text lies invisibly over its place on the page, so the text
 * can be selected, copied and found; the focused blocks are marked where they sit. Printed, each
 * page is a sheet of its own, as large as the sheet holds it.
 */
export function PagesView({ pageUrl, content, focus }: PagesViewProps) {
  return (
    <div className="space-y-5 bg-sunken p-5 print:space-y-0 print:bg-transparent print:p-0">
      {content.page_sizes.map(([width, height], index) => {
        const page = index + 1
        const placed = content.blocks.filter((block) => block.page === page && block.bbox)
        return (
          <div
            key={page}
            id={`page-${page}`}
            // Printed, no wider than the sheet's width or than its height allows: vh is the sheet's in print.
            className="relative mx-auto max-w-3xl overflow-hidden rounded-md bg-white shadow-md shadow-ink/8 ring-1 ring-ink/8 @container print:w-[min(100%,100vh*var(--page-ratio))] print:break-inside-avoid print:break-after-page print:rounded-none print:shadow-none print:ring-0 print:last:break-after-auto"
            style={{ aspectRatio: `${width} / ${height}`, '--page-ratio': width / height } as CSSProperties}
          >
            <img
              src={pageUrl(page)}
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
                    data-block={block.ordinal}
                    // Sized in container units so the text scales with the page.
                    style={{ ...box, fontSize: `${(fitFontSize(block.text, x1 - x0, bottom - top) / width) * 100}cqw` }}
                  >
                    {block.text}
                  </span>
                  {inFocus(block, focus) && (
                    <div className="pointer-events-none absolute rounded-sm bg-amber-300/45 mix-blend-multiply print-exact" style={box} />
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

/** A spreadsheet: one tab per sheet (its heading block), the sheet's tables below; printed, the sheet shown under its name. */
export function SheetView({ label, blocks, focus }: { label: string; blocks: Block[]; focus: BlockFocus | null }) {
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
    <div className="flex min-h-0 flex-1 flex-col print:block">
      {sheets.length > 1 && (
        <div className="no-scrollbar flex shrink-0 gap-1 overflow-x-auto border-b border-line bg-surface px-3 py-1.5 text-[13px] print:hidden">
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
      <Scrolled label={label} className="min-h-0 flex-1 overflow-auto p-4 print:px-0">
        {sheets.length > 1 && sheet && <p className="mb-3 hidden text-base font-semibold tracking-tight print:block">{sheet.title}</p>}
        <div className="space-y-4">
          {sheet?.blocks.map((block) => (
            <div
              key={block.ordinal}
              id={`block-${block.ordinal}`}
              data-block={block.ordinal}
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
 * and Page Down scroll a text that holds nothing else to focus. Printed, it runs on over the sheets.
 */
export function Scrolled({ label, className, children }: { label: string; className: string; children: ReactNode }) {
  return (
    <div role="region" aria-label={label} tabIndex={0} className={`focus-visible:-outline-offset-2! print:overflow-visible ${className}`}>
      {children}
    </div>
  )
}
