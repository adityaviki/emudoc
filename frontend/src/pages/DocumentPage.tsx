import { useQuery } from '@tanstack/react-query'
import { ArrowLeft, ExternalLink, X } from 'lucide-react'
import { Fragment, useEffect, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router'
import { api, fileUrl, pageImageUrl, pageLabel, sectionPath } from '../api.ts'
import type { Block, Document, DocumentContent, Section } from '../api.ts'
import { PipeTable } from '../components/Markdown.tsx'
import { Chip, IconButton, Loading, Notice } from '../components/ui.tsx'
import { formatDate, parsePipeTable } from '../format.ts'
import { useProject } from '../hooks.ts'
import { IndexingStatus } from './DocumentsTab.tsx'

/** The blocks to highlight and scroll to; `end` is exclusive. */
interface Focus {
  start: number
  end: number
}

export function DocumentPage() {
  const project = useProject()
  const documentId = Number(useParams().documentId)
  const [params, setParams] = useSearchParams()
  const passageId = params.get('passage') ? Number(params.get('passage')) : null
  const blockParam = params.get('block')
  const [view, setView] = useState<'text' | 'pages'>('text')

  const doc = useQuery({
    queryKey: ['document', documentId],
    queryFn: () => api.document(documentId),
    refetchInterval: (query) =>
      ['queued', 'processing'].includes(query.state.data?.status ?? '') ? 1500 : false,
  })
  // Derived data is fetched again whenever indexing changes the document's status.
  const status = doc.data?.status
  const indexed = doc.data?.stages.structure?.status === 'done'
  const outline = useQuery({
    queryKey: ['outline', documentId, status],
    queryFn: () => api.outline(documentId),
    enabled: indexed,
  })
  const content = useQuery({
    queryKey: ['content', documentId, status],
    queryFn: () => api.content(documentId),
    enabled: indexed,
  })
  const passage = useQuery({
    queryKey: ['passage', passageId],
    queryFn: () => api.passage(passageId!),
    enabled: passageId != null,
  })

  let focus: Focus | null = null
  if (passage.data && passage.data.document_id === documentId) {
    focus = { start: passage.data.block_start, end: passage.data.block_end }
  } else if (blockParam != null && passageId == null) {
    focus = { start: Number(blockParam), end: Number(blockParam) + 1 }
  }

  const focusStart = focus?.start
  const focusPage = content.data?.blocks.find((block) => block.ordinal === focusStart)?.page
  const loaded = content.isSuccess
  useEffect(() => {
    if (focusStart == null || !loaded) return
    const target = view === 'pages' ? `page-${focusPage}` : `block-${focusStart}`
    document.getElementById(target)?.scrollIntoView({ block: 'center', behavior: 'smooth' })
  }, [focusStart, focusPage, loaded, view])

  if (doc.isPending) return <Loading />
  if (doc.isError) return <Notice tone="bad">{doc.error.message}</Notice>

  const isPdf = doc.data.mime_type === 'application/pdf'
  return (
    <div>
      <Header document={doc.data} projectId={project.id} />

      {!indexed && (
        <Notice tone={doc.data.status === 'failed' ? 'bad' : 'info'}>
          {doc.data.status === 'failed'
            ? (doc.data.error ?? 'Indexing failed.')
            : 'This document is being indexed. Its text will appear here in a moment.'}
        </Notice>
      )}

      {indexed && (
        <div className="grid items-start gap-4 lg:grid-cols-[15rem_minmax(0,1fr)]">
          <aside className="rounded-lg border border-line bg-surface p-2 lg:sticky lg:top-4 lg:max-h-[calc(100vh-2rem)] lg:overflow-y-auto">
            <p className="px-2 pt-1 pb-2 text-xs font-medium tracking-wide text-muted uppercase">Outline</p>
            <ul>
              {outline.data?.map((section) => (
                <OutlineNode
                  key={section.id}
                  section={section}
                  focusStart={focusStart}
                  onSelect={(start) => setParams({ block: String(start) })}
                />
              ))}
            </ul>
          </aside>

          <section className="min-w-0 rounded-lg border border-line bg-surface">
            <div className="flex items-center justify-between gap-3 border-b border-line px-4 py-2">
              <div className="min-w-0 text-sm">
                {passage.data && focus ? (
                  <span className="flex items-center gap-1 text-muted">
                    <span className="truncate">
                      Cited passage:{' '}
                      {[
                        sectionPath(passage.data.context, passage.data.document_title),
                        pageLabel(passage.data.page_start, passage.data.page_end),
                      ]
                        .filter(Boolean)
                        .join(' · ')}
                    </span>
                    <IconButton label="Clear highlight" onClick={() => setParams({})}>
                      <X className="size-3.5" />
                    </IconButton>
                  </span>
                ) : (
                  <span className="text-muted">
                    {content.data ? `${content.data.blocks.length} blocks` : ''}
                  </span>
                )}
              </div>
              {isPdf && (
                <div className="flex shrink-0 rounded-md border border-line p-0.5 text-sm">
                  {(['text', 'pages'] as const).map((mode) => (
                    <button
                      key={mode}
                      onClick={() => setView(mode)}
                      className={`rounded px-2.5 py-0.5 capitalize ${
                        view === mode ? 'bg-sunken font-medium text-ink' : 'text-muted hover:text-ink'
                      }`}
                    >
                      {mode}
                    </button>
                  ))}
                </div>
              )}
            </div>
            {content.isPending && <Loading />}
            {content.data &&
              (view === 'pages' && isPdf ? (
                <PagesView document={doc.data} content={content.data} focus={focus} />
              ) : (
                <TextView blocks={content.data.blocks} focus={focus} />
              ))}
          </section>
        </div>
      )}
    </div>
  )
}

function Header({ document, projectId }: { document: Document; projectId: number }) {
  return (
    <div className="mb-4">
      <Link
        to={`/projects/${projectId}`}
        className="mb-2 inline-flex items-center gap-1 text-sm text-muted hover:text-ink"
      >
        <ArrowLeft className="size-4" />
        All documents
      </Link>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-lg font-semibold">{document.title ?? document.filename}</h2>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted">
            {document.doc_type && <Chip tone="accent">{document.doc_type}</Chip>}
            {document.title && <span>{document.filename}</span>}
            {document.doc_date && <span>dated {formatDate(document.doc_date)}</span>}
            {document.page_count && (
              <span>
                {document.page_count} page{document.page_count === 1 ? '' : 's'}
              </span>
            )}
            <IndexingStatus document={document} />
          </div>
        </div>
        <a
          href={fileUrl(document.id)}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1.5 rounded-md border border-line bg-surface px-3 py-1.5 text-sm font-medium hover:bg-sunken"
        >
          <ExternalLink className="size-4" />
          Original file
        </a>
      </div>
      {document.summary && <p className="mt-2 max-w-3xl text-sm">{document.summary}</p>}
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
          active ? 'bg-accent-soft' : ''
        }`}
      >
        <span className="flex items-baseline justify-between gap-2">
          <span className="text-sm leading-snug font-medium">{section.title}</span>
          <span className="shrink-0 text-xs text-muted">{pageLabel(section.page_start, section.page_end)}</span>
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

const inFocus = (block: Block, focus: Focus | null) =>
  focus != null && block.ordinal >= focus.start && block.ordinal < focus.end

function TextView({ blocks, focus }: { blocks: Block[]; focus: Focus | null }) {
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
    <div className="space-y-2.5 px-6 py-5">
      {blocks.map((block) => {
        return (
          <Fragment key={block.ordinal}>
            {opensPage.has(block.ordinal) && (
              <div className="flex items-center gap-3 pt-2 text-xs text-muted select-none">
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
        )
      })}
    </div>
  )
}

function BlockBody({ block }: { block: Block }) {
  if (block.kind === 'heading') {
    const size = block.level === 1 ? 'text-lg' : block.level === 2 ? 'text-base' : 'text-sm'
    return <p className={`pt-2 font-semibold ${size}`}>{block.text}</p>
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
  focus: Focus | null
}

/** The rendered pages, with the focused blocks marked where they sit on the page. */
function PagesView({ document, content, focus }: PagesViewProps) {
  return (
    <div className="space-y-4 bg-sunken p-4">
      {content.page_sizes.map(([width, height], index) => {
        const page = index + 1
        const marked = content.blocks.filter(
          (block) => block.page === page && block.bbox && inFocus(block, focus),
        )
        return (
          <div
            key={page}
            id={`page-${page}`}
            className="relative mx-auto max-w-3xl overflow-hidden rounded border border-line bg-white shadow-sm"
            style={{ aspectRatio: `${width} / ${height}` }}
          >
            <img
              src={pageImageUrl(document.id, page)}
              alt={`Page ${page}`}
              loading="lazy"
              className="size-full"
            />
            {marked.map((block) => {
              const [x0, top, x1, bottom] = block.bbox!
              return (
                <div
                  key={block.ordinal}
                  className="absolute rounded-sm bg-amber-300/45 mix-blend-multiply"
                  style={{
                    left: `${(x0 / width) * 100}%`,
                    top: `${(top / height) * 100}%`,
                    width: `${((x1 - x0) / width) * 100}%`,
                    height: `${((bottom - top) / height) * 100}%`,
                  }}
                />
              )
            })}
          </div>
        )
      })}
    </div>
  )
}
