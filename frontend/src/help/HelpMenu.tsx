// The question mark in the header: the way to the help pages (src/help/HelpPage.tsx), the status page and the code's
// source, and to write for help from
// wherever the member is, to SUPPORT_EMAIL or the workspace's owners and admins, with the page
// and the last request that failed attached (src/help/feedback.ts, backend/app/api/help.py). What they typed is kept while the panel is
// closed, which a click elsewhere or a switch to another window does, until it is sent. Where the install mails nothing, the
// menu offers no way to write and is its links alone (the owner's word of 2026-11-27); the help pages say whom to write to
// from the member's own mail.

import { useMutation } from '@tanstack/react-query'
import { Activity, BookOpen, CircleHelp, Code } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import { Link, useLocation } from 'react-router'
import { api, lastFailedRequest } from '../api.ts'
import type { FeedbackKind } from '../api.ts'
import { useMe } from '../auth.ts'
import { Floating } from '../components/Menu.tsx'
import { Button, FIELD, Notice, Segmented } from '../components/ui.tsx'
import { useHelp } from '../hooks.ts'
import {
  FEEDBACK_KINDS,
  MAX_FEEDBACK_CHARS,
  attachedWords,
  feedbackOf,
  sentTo,
  writeHeading,
} from './feedback.ts'

export function HelpMenu() {
  const help = useHelp()
  const [anchor, setAnchor] = useState<DOMRect | null>(null)
  const button = useRef<HTMLButtonElement>(null)
  const [kind, setKind] = useState<FeedbackKind>('problem')
  const [message, setMessage] = useState('')
  const location = useLocation()
  const send = useMutation({
    mutationFn: () => api.sendFeedback(feedbackOf(kind, message, location.pathname, location.search, lastFailedRequest())),
    onSuccess: () => setMessage(''),
  })

  return (
    <>
      <button
        ref={button}
        type="button"
        aria-haspopup="dialog"
        aria-expanded={anchor !== null}
        aria-label="Help"
        title="Help"
        onClick={(event) => setAnchor(anchor ? null : event.currentTarget.getBoundingClientRect())}
        className="inline-flex size-7 shrink-0 items-center justify-center rounded-md text-muted transition hover:bg-sunken hover:text-ink"
      >
        <CircleHelp className="size-4" />
      </button>
      {anchor && (
        <Floating anchor={anchor} alignEnd onClose={() => setAnchor(null)} ignore={button} className="w-96 max-w-[calc(100vw-16px)]">
          <div role="dialog" aria-label="Help">
            <HelpLinks onClose={() => setAnchor(null)} />
            {help.data?.mail !== false && (
              <WriteSection supportEmail={help.data?.support_email ?? null}>
                {send.isSuccess ? (
                  <Sent reference={send.data.reference} to={send.data.to} onAgain={() => send.reset()} />
                ) : (
                  <WriteForm
                    kind={kind}
                    onKind={setKind}
                    message={message}
                    onMessage={setMessage}
                    sending={send.isPending}
                    error={send.error?.message ?? null}
                    onSend={() => send.mutate()}
                  />
                )}
              </WriteSection>
            )}
          </div>
        </Floating>
      )}
    </>
  )
}

/**
 * The help pages, in the app, which the menu closes behind; then the deployment's links: the status page, where it
 * names one, which says whether Emudoc is up from outside it, and the source of the code it runs, which the AGPL's
 * section 13 has it offer whoever uses it (SOURCE_URL, deploying.md).
 */
function HelpLinks({ onClose }: { onClose: () => void }) {
  const help = useHelp()
  return (
    <div className="p-1">
      <Link to="/help" onClick={onClose} className={LINK}>
        <BookOpen className="size-4 shrink-0 opacity-70" />
        Help pages
      </Link>
      {help.data?.status_page && (
        <HelpLink href={help.data.status_page} icon={Activity}>
          Service status
        </HelpLink>
      )}
      {help.data && (
        <HelpLink href={help.data.source} icon={Code}>
          Source code
        </HelpLink>
      )}
    </div>
  )
}

const LINK = 'flex h-8 items-center gap-2.5 rounded-[5px] px-2 text-[13px] outline-none hover:bg-sunken focus-visible:bg-sunken'

function HelpLink({ href, icon: Icon, children }: { href: string; icon: LucideIcon; children: ReactNode }) {
  return (
    <a href={href} target="_blank" rel="noreferrer" className={LINK}>
      <Icon className="size-4 shrink-0 opacity-70" />
      {children}
    </a>
  )
}

/** The way to write for help, under the links, headed by whom it goes to: SUPPORT_EMAIL, else the workspace's admins. */
function WriteSection({ supportEmail, children }: { supportEmail: string | null; children: ReactNode }) {
  return (
    <div className="border-t border-line">
      <p className="px-3 pt-3 text-[13px] font-medium">{writeHeading(supportEmail)}</p>
      {children}
    </div>
  )
}

interface WriteFormProps {
  kind: FeedbackKind
  onKind: (kind: FeedbackKind) => void
  message: string
  onMessage: (message: string) => void
  sending: boolean
  error: string | null
  onSend: () => void
}

function WriteForm({ kind, onKind, message, onMessage, sending, error, onSend }: WriteFormProps) {
  const help = useHelp()
  const words = useRef<HTMLTextAreaElement>(null)
  useEffect(() => {
    words.current?.focus()
  }, [])
  const address = help.data?.support_email

  function submit(event: FormEvent) {
    event.preventDefault()
    if (message.trim()) onSend()
  }

  return (
    <form onSubmit={submit} className="space-y-3 p-3">
      <div role="group" aria-label="What about">
        <Segmented value={kind} onChange={onKind} options={FEEDBACK_KINDS} />
      </div>
      <div>
        <label htmlFor="support-message" className="mb-1 block text-xs font-medium text-muted">
          Message
        </label>
        <textarea
          id="support-message"
          ref={words}
          rows={6}
          maxLength={MAX_FEEDBACK_CHARS}
          value={message}
          onChange={(event) => onMessage(event.target.value)}
          placeholder={FEEDBACK_KINDS.find((option) => option.value === kind)?.prompt}
          className={`${FIELD} resize-y`}
        />
        <p className="mt-1 text-xs leading-relaxed text-muted">{attachedWords(lastFailedRequest())}</p>
      </div>
      {error && <Notice tone="bad">{error}</Notice>}
      <div className="flex items-center justify-between gap-3">
        {address ? (
          <a href={`mailto:${address}`} className="min-w-0 truncate text-xs text-muted hover:text-ink hover:underline">
            Or write to {address}
          </a>
        ) : (
          <span />
        )}
        <Button type="submit" variant="primary" disabled={sending || !message.trim()}>
          {sending ? 'Sending…' : 'Send'}
        </Button>
      </div>
    </form>
  )
}

function Sent({ reference, to, onAgain }: { reference: string; to: string | null; onAgain: () => void }) {
  const me = useMe()
  // The keyboard goes on from the way to write again, the form's place.
  const panel = useRef<HTMLDivElement>(null)
  useEffect(() => {
    panel.current?.querySelector('button')?.focus()
  }, [])
  return (
    <div ref={panel} className="space-y-3 p-3">
      <p role="status" className="text-[13px] leading-relaxed">
        Sent to {sentTo(to)}. The answer comes to {me.email}; if you write about it again, give the reference{' '}
        <span className="font-mono wrap-break-word select-all">{reference}</span>.
      </p>
      <Button onClick={onAgain}>
        Write another
      </Button>
    </div>
  )
}
