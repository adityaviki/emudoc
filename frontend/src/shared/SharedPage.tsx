// The page a share link opens (backend/app/share_links.py), for whoever holds it, signed in or not: it opens the link
// by the token in its fragment, which stays in the address so that a reload opens it again, asks for its password where
// it has one, and shows the document read only, as a member's tab does (components/DocumentViews.tsx), its pages and
// file through the cookie of the view the opening made, until the view ends and the link is opened again.

import { useMutation } from '@tanstack/react-query'
import { Download } from 'lucide-react'
import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { useLocation } from 'react-router'
import { ApiError, api, isImage, isPdf, isSheet, sharedFileUrl, sharedPageUrl } from '../api.ts'
import type { SharedDocument } from '../api.ts'
import { AuthCard, LegalLinks, PasswordField } from '../account/shared.tsx'
import { ImagePreview, PagesView, SheetView, TextView } from '../components/DocumentViews.tsx'
import { ThemeToggle } from '../components/ThemeToggle.tsx'
import { Button, ButtonLink, Chip, Notice, Spinner } from '../components/ui.tsx'
import { formatDate } from '../format.ts'
import { sharedWords, tokenOf, viewLeft } from '../shareLinks.ts'

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
    if (!shared) return
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
            Open the document
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
      <LegalLinks />
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
  let view
  if (isPdf(shared)) {
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
    <div className="w-full max-w-4xl">
      <div className="flex flex-wrap items-start justify-between gap-3">
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
          <p className="mt-1.5 text-[13px] text-muted">{sharedWords(shared)}</p>
        </div>
        <ButtonLink href={sharedFileUrl(linkId)} download={shared.filename} icon={<Download className="size-4" />}>
          Download the file
        </ButtonLink>
      </div>
      {ended && (
        <div className="mt-4">
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
      <div className="mt-5 overflow-hidden rounded-lg border border-line bg-surface">{view}</div>
    </div>
  )
}
