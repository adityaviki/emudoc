import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { History, Link2, ListChecks, ListTree, MessageSquare, MessageSquarePlus, PenLine, Printer, StickyNote, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { api, fileUrl, hasPages, isImage, isSheet, mayChange, pageImageUrl, pageLabel, sectionPath } from '../api.ts'
import type { Document, Section } from '../api.ts'
import { useMe } from '../auth.ts'
import { chosenIn, openWords, quoted, threadCounts, threadsKey } from '../comments.ts'
import type { ThreadAbout } from '../comments.ts'
import { noteKey } from '../details.ts'
import { ImagePreview, PagesView, Scrolled, SheetView, TextView } from '../components/DocumentViews.tsx'
import type { BlockFocus } from '../components/DocumentViews.tsx'
import { IndexingStatus } from '../components/IndexingStatus.tsx'
import { Button, Chip, IconButton, Loading, Notice } from '../components/ui.tsx'
import { formatDate } from '../format.ts'
import { useLocalStorage, usePrintTitle, useStatus } from '../hooks.ts'
import { notIndexedWords, restHeld, scanPassed, unfinishedStages } from '../indexing.ts'
import { offersLinks } from '../shareLinks.ts'
import { describeDocumentUsage } from '../usage.ts'
import { CommentsPanel } from './Comments.tsx'
import { Copies, DetailsDialog, NotePanel } from './Details.tsx'
import { originalFile } from './files.ts'
import { documentKey, listingsKey } from './listing.ts'
import { ReviewControls } from './Reviews.tsx'
import { ShareDialog } from './ShareLinks.tsx'
import { VersionsDialog } from './Versions.tsx'
import { useWorkspace } from './state.ts'
import { followedKey } from './uploads.ts'
import type { Focus } from './url.ts'

// Whether the comments beside a document are shown, as the member left them last.
const COMMENTS_KEY = 'emudoc.comments-shown'
const isBoolean = (value: unknown): value is boolean => typeof value === 'boolean'

interface DocumentTabProps {
  document: Document
  focus: Focus | null
  onClearFocus: () => void
}

/**
 * One open document: a PDF's rendered pages, or anything else's extracted text with the outline beside it.
 * Printed, it is the document alone (data-print, index.css): a PDF its pages, anything else its
 * heading and text, and none of the controls.
 */
export function DocumentTab({ document, focus, onClearFocus }: DocumentTabProps) {
  const workspace = useWorkspace()
  const me = useMe()
  const changes = mayChange(me.role)
  // Its review, while reviews are rolled out to the organisation (backend/app/flags.py).
  const reviews = me.organization.flags_on.includes('extraction_templates')
  // Where the keyboard goes on from once a button of the header it held is gone.
  const heading = useRef<HTMLHeadingElement>(null)
  const [outlineOpen, setOutlineOpen] = useState(true)
  const [sharing, setSharing] = useState(false)
  const [versioning, setVersioning] = useState(false)
  const [detailing, setDetailing] = useState(false)
  const [writing, setWriting] = useState(false)
  // Its note, which everyone who reads the project reads.
  const note = useQuery({ queryKey: noteKey(document.id), queryFn: () => api.note(document.id) })
  // Its comments, which everyone who reads the project writes, while they are rolled out to the organisation.
  const commenting = me.organization.flags_on.includes('comments')
  const threads = useQuery({ queryKey: threadsKey(document.id), queryFn: () => api.threads(document.id), enabled: commenting })
  const counts = threadCounts(threads.data ?? [])
  const [commentsShown, setCommentsShown] = useLocalStorage(COMMENTS_KEY, false, isBoolean)
  const commentsOpen = commenting && commentsShown
  const [about, setAbout] = useState<ThreadAbout | null>(null)
  // What the comments read the words chosen in.
  const body = useRef<HTMLDivElement>(null)
  const threadId = focus && 'thread' in focus ? focus.thread : null
  // A link to a thread shows the comments, this time, whatever the member left them as.
  useEffect(() => {
    if (threadId != null) setCommentsShown(true, { persist: false })
  }, [threadId, setCommentsShown])
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
  // A PDF saved from the print is named after the document rather than the app.
  usePrintTitle(document.title ?? document.filename)
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

  // A PDF or a scan shows its rendered pages, with the text laid over them; everything else shows its text.
  const pages = hasPages(document)
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

  // What a new thread is about: the passage or the block the document is shown at, else the document as a whole.
  const aboutFocus = (): ThreadAbout => {
    if (passage.data && passage.data.document_id === document.id) {
      return { passage_id: passage.data.id, shown: quoted(passage.data.text) }
    }
    if (focus && 'block' in focus) {
      const block = content.data?.blocks.find((shown) => shown.ordinal === focus.block)
      return { block: focus.block, shown: block ? quoted(block.text) : undefined }
    }
    return {}
  }
  const commentOn = (on: ThreadAbout) => {
    setCommentsShown(true)
    setAbout(on)
  }

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
    <div data-print className="flex h-full min-h-0 flex-col">
      {/* A PDF's pages say on themselves what it is; its first page starts the first sheet. */}
      <header className={`shrink-0 border-b border-line bg-surface px-4 py-2.5 ${pages ? 'print:hidden' : 'print:px-0'}`}>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2
              ref={heading}
              tabIndex={-1}
              className="truncate text-[15px] font-semibold tracking-tight print:whitespace-normal"
              title={document.title ?? document.filename}
            >
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
              {document.version > 1 && <span>version {document.version}</span>}
              {/* What the document cost and where its indexing stands are the app's, not the document's. */}
              <span className="contents print:hidden">
                {usage && (
                  <span title={usage.detail}>{usage.summary}</span>
                )}
                <IndexingStatus document={document} />
                {changes && unfinishedStages(document).length > 0 && (
                  <IndexTheRest document={document} onStarted={() => heading.current?.focus()} />
                )}
              </span>
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-1.5 print:hidden">
            {hasOutline && outline.data && outline.data.length > 0 && (
              <IconButton label={outlineOpen ? 'Hide outline' : 'Show outline'} onClick={() => setOutlineOpen((open) => !open)}>
                <ListTree className="size-4" />
              </IconButton>
            )}
            {content.isSuccess && (
              <IconButton label="Print" onClick={() => window.print()}>
                <Printer className="size-4" />
              </IconButton>
            )}
            {changes && (
              <IconButton label="Edit details" onClick={() => setDetailing(true)}>
                <PenLine className="size-4" />
              </IconButton>
            )}
            {changes && !writing && note.data && !note.data.text && (
              <IconButton label="Add a note" onClick={() => setWriting(true)}>
                <StickyNote className="size-4" />
              </IconButton>
            )}
            {commenting && (
              <button
                type="button"
                aria-pressed={commentsOpen}
                aria-label={`Comments, ${openWords(counts.open)}`}
                title="Comments"
                onClick={() => setCommentsShown((shown) => !shown)}
                className="inline-flex h-7 shrink-0 items-center gap-1 rounded-md px-1.5 text-muted transition hover:bg-sunken hover:text-ink aria-pressed:bg-sunken aria-pressed:text-ink"
              >
                <MessageSquare className="size-4" />
                {counts.open > 0 && <span className="text-xs tabular-nums">{counts.open}</span>}
              </button>
            )}
            <IconButton label="Versions" onClick={() => setVersioning(true)}>
              <History className="size-4" />
            </IconButton>
            {offersLinks(me) && indexed && openable && (
              <IconButton label="Share by link" onClick={() => setSharing(true)}>
                <Link2 className="size-4" />
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
        <Copies document={document} />
        {reviews && (
          <div className="mt-2 print:hidden">
            <ReviewControls document={document} />
          </div>
        )}
        <NotePanel
          document={document}
          note={note.data}
          changes={changes}
          writing={writing}
          onWrite={() => setWriting(true)}
          onDone={() => {
            setWriting(false)
            heading.current?.focus()
          }}
        />
        {detailing && <DetailsDialog document={document} onClose={() => setDetailing(false)} />}
        {sharing && <ShareDialog target={{ kind: 'document', id: document.id, name: document.filename }} onClose={() => setSharing(false)} />}
        {versioning && <VersionsDialog document={document} onClose={() => setVersioning(false)} />}
        {focusLabel && (
          <p className="mt-1.5 inline-flex max-w-full items-center gap-1 rounded-md bg-mark/40 py-0.5 pr-0.5 pl-2 text-xs">
            <span className="truncate">Showing: {focusLabel}</span>
            {commenting && (
              <IconButton label="Comment on this" onClick={() => commentOn(aboutFocus())} className="size-5! print:hidden">
                <MessageSquarePlus className="size-3.5" />
              </IconButton>
            )}
            <IconButton label="Clear highlight" onClick={onClearFocus} className="size-5! print:hidden">
              <X className="size-3.5" />
            </IconButton>
          </p>
        )}
      </header>

      {/* The comments lie over the text in a narrow tab, and beside it where the text keeps its room. */}
      <div className="@container relative flex min-h-0 flex-1 print:block">
        <div ref={body} className="flex min-w-0 flex-1 flex-col print:block">
          {!indexed ? (
            <div className="p-4">
              <Notice tone={document.status === 'failed' || document.status === 'quarantined' ? 'bad' : 'info'}>
                {document.status === 'quarantined'
                  ? (document.error ?? 'The malware scan flagged this file. It is quarantined: not indexed, and nobody can open it.')
                  : document.status === 'failed'
                    ? (document.error ?? 'Indexing failed.')
                    : notIndexedWords(document)}
              </Notice>
              {isImage(document) && openable && <ImagePreview src={fileUrl(document.id)} alt={document.filename} />}
            </div>
          ) : content.isPending ? (
            <Loading />
          ) : content.isError ? (
            <div className="p-4">
              <Notice tone="bad">Could not load the text: {content.error.message}</Notice>
            </div>
          ) : pages ? (
            <Scrolled label={`Pages of ${document.filename}`} className="min-h-0 flex-1 overflow-y-auto">
              <PagesView pageUrl={(page) => pageImageUrl(document.id, page)} content={content.data} focus={blockFocus} />
            </Scrolled>
          ) : isImage(document) ? (
            <Scrolled label={`Text of ${document.filename}`} className="min-h-0 flex-1 overflow-y-auto">
              <ImagePreview src={fileUrl(document.id)} alt={document.filename} />
              <TextView blocks={content.data.blocks} focus={blockFocus} />
            </Scrolled>
          ) : isSheet(document) ? (
            <SheetView label={`Rows of ${document.filename}`} blocks={content.data.blocks} focus={blockFocus} />
          ) : (
            <div className="flex min-h-0 flex-1 print:block">
              {outlineOpen && outline.data && outline.data.length > 0 && (
                <aside aria-label="Outline" className="w-60 shrink-0 overflow-y-auto border-r border-line bg-surface p-2 print:hidden">
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
        {commentsOpen && (
          <CommentsPanel
            document={document}
            about={about}
            onAbout={setAbout}
            aboutNow={() => chosenIn(body.current) ?? aboutFocus()}
            marked={threadId}
            onClose={() => {
              setCommentsShown(false)
              setAbout(null)
              if (threadId != null) onClearFocus()
              heading.current?.focus()
            }}
          />
        )}
      </div>
    </div>
  )
}

/**
 * "Index the rest" of a partly indexed document: the run from the first stage it went without,
 * which keeps what it has meanwhile, or why it waits for the service its rest needs.
 */
function IndexTheRest({ document, onStarted }: { document: Document; onStarted: () => void }) {
  const queryClient = useQueryClient()
  const health = useStatus()
  const unfinished = unfinishedStages(document)
  const held = restHeld(unfinished, health.data)
  const run = useMutation({
    mutationFn: () => api.reprocessDocument(document.id, unfinished[0]),
    // Queued, it is no longer partly indexed, and the button goes at once.
    onSuccess: (queued) => {
      queryClient.setQueryData(documentKey(document.id), queued)
      onStarted()
    },
    onSettled: () => {
      for (const queryKey of [
        listingsKey(document.project_id),
        documentKey(document.id),
        ['project', document.project_id],
        followedKey(document.project_id),
      ]) {
        void queryClient.invalidateQueries({ queryKey })
      }
    },
  })
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
      <Button icon={<ListChecks className="size-4" />} onClick={() => run.mutate()} disabled={run.isPending || held != null}>
        Index the rest
      </Button>
      {held && <span className="max-w-xs text-muted">{held}</span>}
      {run.isError && (
        <span role="alert" className="max-w-xs text-bad">
          {run.error.message}
        </span>
      )}
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

