// The page a share link opens (backend/app/share_links.py), for whoever holds it, signed in or not: it opens the link
// by the token in its fragment, which stays in the address so that a reload opens it again, asks for its password where
// it has one, and shows what the link shows, read only. A document as a member's tab shows it
// (components/DocumentViews.tsx), its pages and file through the cookie of the view the opening made, until the view
// ends and the link is opened again; an answer with its question and the passages it cites, each citation leading to
// its passage's text below. Printed, each is what it shows alone (data-print, index.css), as the document tab prints.

import { useMutation } from '@tanstack/react-query'
import { Download, Printer } from 'lucide-react'
import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { useLocation } from 'react-router'
import { ApiError, api, hasPages, isImage, isSheet, sharedFileUrl, sharedPageUrl, sourceLabel } from '../api.ts'
import type { SharedAnswer, SharedDocument, Source } from '../api.ts'
import { AuthCard, PasswordField } from '../account/shared.tsx'
import { ImagePreview, PagesView, SheetView, TextView } from '../components/DocumentViews.tsx'
import { ThemeToggle } from '../components/ThemeToggle.tsx'
import { Button, ButtonLink, Chip, Notice, Segmented, Spinner } from '../components/ui.tsx'
import { formatDate } from '../format.ts'
import { usePrintTitle } from '../hooks.ts'
import { sharedWords, tokenOf, viewLeft } from '../shareLinks.ts'
import { SHOWN_CHOICES, viewContent } from '../views.ts'
import type { Shown } from '../views.ts'
import { CitedText } from '../workspace/CitedText.tsx'

const NOT_A_LINK =
  'This address is not a whole share link. Copy the whole link you were sent, with everything after its #, into the address bar.'

export function SharedPage() {
  const location = useLocation()
  const token = tokenOf(location.hash)
  // Kept for opening the link again once the view ends, never stored.
  const [password, setPassword] = useState('')
  const [sent, setSent] = useState(false)
  const [ended, setEnded] = useState(false)
  const open = useMutation({
    mutationFn: (given?: string) => api.openShared(token, given),
    onSuccess: () => setEnded(false),
  })
  const { mutate } = open
  const shared = open.data

  useEffect(() => {
    if (token) mutate(undefined)
  }, [token, mutate])

  // The view's cookie ends with it, and the pages after it would not load: the page says so first.
  useEffect(() => {
    if (shared?.kind !== 'document') return
    const timer = window.setTimeout(() => setEnded(true), viewLeft(shared))
    return () => window.clearTimeout(timer)
  }, [shared])

  function submit(event: FormEvent) {
    event.preventDefault()
    setSent(true)
    mutate(password)
  }

  let body
  if (!token) {
    body = <Gone message={NOT_A_LINK} />
  } else if (shared?.kind === 'answer') {
    body = <SharedAnswerView shared={shared} />
  } else if (shared) {
    body = <SharedView shared={shared} ended={ended} opening={open.isPending} onOpenAgain={() => mutate(password || undefined)} />
  } else if (open.error instanceof ApiError && open.error.status === 401) {
    body = (
      <AuthCard title="This link has a password" intro="Enter the password whoever sent you the link gave you.">
        <form onSubmit={submit} className="space-y-4">
          <PasswordField
            label="Password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete="off"
            required
            autoFocus
          />
          {sent && !open.isPending && (
            <p role="alert" className="text-[13px] text-bad">
              {open.error.message}
            </p>
          )}
          <Button variant="primary" type="submit" disabled={open.isPending} className="w-full justify-center">
            Open the link
          </Button>
        </form>
      </AuthCard>
    )
  } else if (open.isError) {
    body = <Gone message={open.error.message} />
  } else {
    body = (
      <div className="flex flex-1 items-center justify-center py-16">
        <Spinner className="size-5" />
      </div>
    )
  }

  return (
    <div className="flex min-h-dvh flex-col">
      <header className="flex items-center justify-between gap-3 border-b border-line bg-surface px-4 py-2.5">
        <span className="flex items-center gap-2 text-[15px] font-semibold tracking-tight">
          <img src="/favicon.svg" alt="" className="size-5" />
          Emudoc
        </span>
        <ThemeToggle />
      </header>
      <main className="flex flex-1 flex-col items-center px-4 pt-6 pb-10">{body}</main>
    </div>
  )
}

function Gone({ message }: { message: string }) {
  return (
    <AuthCard title="The link does not open">
      <p className="text-[13px] leading-relaxed">{message}</p>
    </AuthCard>
  )
}

interface SharedViewProps {
  shared: SharedDocument
  ended: boolean
  opening: boolean
  onOpenAgain: () => void
}

/** The document the link shows, read only: who shared it and until when, its file to download, and its content. */
function SharedView({ shared, ended, opening, onOpenAgain }: SharedViewProps) {
  const { link_id: linkId, content } = shared
  usePrintTitle(shared.title || shared.filename)
  // A Word, PowerPoint or Excel file whose view was made is shown as it looks, with a switch to its text
  // (backend/app/conversion.py).
  const [shown, setShown] = useState<Shown>('original')
  const viewReady = content.view_page_sizes.length > 0
  const viewShown = viewReady && shown === 'original'
  let view
  if (viewShown) {
    view = <PagesView pageUrl={(page) => sharedPageUrl(linkId, page)} content={viewContent(content.view_page_sizes)} focus={null} />
  } else if (hasPages(shared)) {
    view = <PagesView pageUrl={(page) => sharedPageUrl(linkId, page)} content={content} focus={null} />
  } else if (isImage(shared)) {
    view = (
      <>
        <ImagePreview src={sharedFileUrl(linkId)} alt={shared.filename} />
        <TextView blocks={content.blocks} focus={null} />
      </>
    )
  } else if (isSheet(shared)) {
    view = <SheetView label={`Rows of ${shared.filename}`} blocks={content.blocks} focus={null} />
  } else {
    view = <TextView blocks={content.blocks} focus={null} />
  }

  return (
    <div data-print className="w-full max-w-4xl">
      {/* A PDF's pages say on themselves what it is; its first page starts the first sheet. */}
      <div className={`flex flex-wrap items-start justify-between gap-3 ${hasPages(shared) || viewShown ? 'print:hidden' : ''}`}>
        <div className="min-w-0">
          <h1 className="text-xl font-semibold tracking-tight break-words">{shared.title || shared.filename}</h1>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
            {shared.doc_type && <Chip tone="accent">{shared.doc_type}</Chip>}
            {shared.title && <span className="break-all">{shared.filename}</span>}
            {shared.doc_date && <span>dated {formatDate(shared.doc_date)}</span>}
            {shared.page_count && (
              <span>
                {shared.page_count} page{shared.page_count === 1 ? '' : 's'}
              </span>
            )}
          </div>
          <p className="mt-1.5 text-[13px] text-muted print:hidden">{sharedWords(shared)}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2 print:hidden">
          {viewReady && <Segmented label="Show the document as" value={shown} onChange={setShown} options={SHOWN_CHOICES} />}
          <Button icon={<Printer className="size-4" />} onClick={() => window.print()}>
            Print
          </Button>
          <ButtonLink href={sharedFileUrl(linkId)} download={shared.filename} icon={<Download className="size-4" />}>
            Download the file
          </ButtonLink>
        </div>
      </div>
      {ended && (
        <div className="mt-4 print:hidden">
          <Notice tone="info">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span>This page was opened an hour ago, and its pages no longer load until it is opened again.</span>
              <Button onClick={onOpenAgain} disabled={opening}>
                Open it again
              </Button>
            </div>
          </Notice>
        </div>
      )}
      <div className="mt-5 overflow-hidden rounded-lg border border-line bg-surface print:mt-0 print:overflow-visible print:rounded-none print:border-0">
        {view}
      </div>
    </div>
  )
}

/**
 * The answer the link shows, read only: its question, its text, each citation numbered and leading to its passage below,
 * and nothing else of the documents.
 */
function SharedAnswerView({ shared }: { shared: SharedAnswer }) {
  const sources: Source[] = shared.passages.map((passage) => ({
    id: passage.id,
    document_id: 0,
    document_title: passage.document_title,
    context: passage.context,
    page_start: passage.page_start,
    page_end: passage.page_end,
    excerpt: passage.text,
  }))
  usePrintTitle(`An answer about the documents of ${shared.organization}`)
  function show(source: Source) {
    const passage = window.document.getElementById(`passage-${source.id}`)
    // Smoothly, unless the system asks for less motion, which CSS cannot say for a script's scroll.
    const behavior = window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'
    passage?.scrollIntoView({ block: 'start', behavior })
    passage?.focus({ preventScroll: true })
  }

  return (
    <div data-print className="w-full max-w-3xl">
      <h1 className="text-xl font-semibold tracking-tight">An answer about the documents of {shared.organization}</h1>
      <p className="mt-1.5 text-[13px] text-muted">
        {sharedWords(shared)} Answered on {formatDate(shared.answered_at)}.
      </p>
      <div className="mt-5 space-y-4 rounded-lg border border-line bg-surface p-5">
        <div>
          <p className="mb-1 text-[11px] font-medium tracking-wide text-muted uppercase">Question</p>
          <p className="leading-relaxed whitespace-pre-wrap">{shared.question}</p>
        </div>
        <div className="space-y-2.5">
          <p className="text-[11px] font-medium tracking-wide text-muted uppercase">Answer</p>
          <CitedText text={shared.answer} sources={sources} onOpen={show} opens="Show the passage" />
        </div>
        <p className="text-xs text-muted">Written by an AI model from the documents, which it can misread: check the passages it cites.</p>
      </div>
      {sources.length > 0 && (
        <section aria-labelledby="cited-passages" className="mt-6">
          <h2 id="cited-passages" className="text-[15px] font-semibold tracking-tight">
            The passages it cites
          </h2>
          <ol className="mt-3 space-y-3">
            {sources.map((source, index) => (
              <li
                key={source.id}
                id={`passage-${source.id}`}
                tabIndex={-1}
                aria-label={`Passage ${index + 1}: ${sourceLabel(source)}`}
                className="rounded-lg border border-line bg-surface p-4 text-[13px]"
              >
                <p className="flex gap-2 font-medium">
                  <span className="font-semibold text-accent tabular-nums">{index + 1}</span>
                  {sourceLabel(source)}
                </p>
                <p className="mt-2 leading-relaxed whitespace-pre-wrap">{source.excerpt}</p>
              </li>
            ))}
          </ol>
        </section>
      )}
    </div>
  )
}
