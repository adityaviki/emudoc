// The question mark in the header: the way to the status page, and to write for help from
// wherever the member is, to SUPPORT_EMAIL or the workspace's owners and admins, with the page
// and the last request that failed attached (src/help/feedback.ts, backend/app/api/help.py). What they typed is kept while the panel is
// closed, which a click elsewhere or a switch to another window does, until it is sent. Where the install mails nothing, the
// form gives way to whom to write to from the member's own mail.

import { useMutation, useQuery } from '@tanstack/react-query'
import { Activity, CircleHelp } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { useLocation } from 'react-router'
import { api, lastFailedRequest, organizations } from '../api.ts'
import type { FeedbackKind } from '../api.ts'
import { useMe } from '../auth.ts'
import { Floating } from '../components/Menu.tsx'
import { Button, FIELD, Notice, Segmented, Spinner } from '../components/ui.tsx'
import { useHelp } from '../hooks.ts'
import {
  FEEDBACK_KINDS,
  MAX_FEEDBACK_CHARS,
  attachedWords,
  byHandWords,
  feedbackOf,
  feedbackPage,
  mailtoOf,
  readersByHand,
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
            <StatusPageLink />
            <p className="px-3 pt-3 text-[13px] font-medium">{writeHeading(help.data?.support_email ?? null)}</p>
            {help.data?.mail === false ? (
              <WriteByHand supportEmail={help.data.support_email} />
            ) : send.isSuccess ? (
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
          </div>
        </Floating>
      )}
    </>
  )
}

/** The status page, where the deployment names one: it says whether Emudoc is up from outside it. */
function StatusPageLink() {
  const help = useHelp()
  if (!help.data?.status_page) return null
  return (
    <div className="border-b border-line p-1">
      <a
        href={help.data.status_page}
        target="_blank"
        rel="noreferrer"
        className="flex h-8 items-center gap-2.5 rounded-[5px] px-2 text-[13px] outline-none hover:bg-sunken focus-visible:bg-sunken"
      >
        <Activity className="size-4 shrink-0 opacity-70" />
        Service status
      </a>
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

// The members list names the owners and admins, which every member may read; the panel's first
// link takes the focus as it opens.
function WriteByHand({ supportEmail }: { supportEmail: string | null }) {
  const me = useMe()
  const location = useLocation()
  const members = useQuery({ queryKey: ['organization', 'members'], queryFn: organizations.members, enabled: !supportEmail })
  if (!supportEmail && members.isPending) {
    return (
      <div className="flex justify-center p-3">
        <Spinner />
      </div>
    )
  }
  if (members.isError) {
    return (
      <div className="p-3">
        <Notice tone="bad">{members.error.message}</Notice>
      </div>
    )
  }
  const readers = readersByHand(supportEmail, members.data ?? [], me.id)
  const page = feedbackPage(location.pathname, location.search)
  const failed = lastFailedRequest()
  return (
    <div className="space-y-2 p-3 text-[13px] leading-relaxed">
      <p>{byHandWords(supportEmail, readers.length)}</p>
      {readers.length > 0 && (
        <ul className="space-y-1">
          {readers.map((reader) => (
            <li key={reader.email} className="truncate">
              <a href={mailtoOf(reader.email, window.location.origin, page, failed)} className="text-accent hover:underline">
                {reader.name === reader.email ? reader.email : `${reader.name}, ${reader.email}`}
              </a>
            </li>
          ))}
        </ul>
      )}
    </div>
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
