// A document's or an answer's share links (backend/app/share_links.py): the dialog the document's tab or the answer
// opens, which makes one (the days it runs and a password, if any), shows its address once to copy, and lists those
// that have not expired; and a link's row, which its maker or an owner or admin revokes, here and in the
// organisation's list (organization/ShareLinksSection.tsx).

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, Copy, X } from 'lucide-react'
import { useEffect, useId, useRef, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import { DEFAULT_SHARE_DAYS, PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH, PLAN_LABELS, api, isPlanLimit } from '../api.ts'
import type { ShareDays, ShareLink, ShareLinkMade } from '../api.ts'
import { PasswordField } from '../account/shared.tsx'
import { useMe } from '../auth.ts'
import { PlanLimitNotice, PlanOffer } from '../components/PlanLimit.tsx'
import { Button, IconButton, InputField, Notice, SelectField } from '../components/ui.tsx'
import { formatDate } from '../format.ts'
import { LINKS_OFF, ORGANIZATION_LINKS_KEY, SHARE_DAY_CHOICES, describeLink, linksHeld, mayRevoke, shareLinksKey } from '../shareLinks.ts'
import type { ShareTarget } from '../shareLinks.ts'

// What the dialog says the link shows, by what it is made for.
const SHOWS = {
  document: 'Anyone who holds a link reads the document and downloads its file',
  answer: 'Anyone who holds a link reads the answer, its question and the passages it cites, and nothing else of the documents,',
}

/**
 * The document's or the answer's links, in a modal over the page for as long as it is mounted, as `Confirm` is: the
 * browser's own dialog, Escape closing it and the focus going back to the button that opened it.
 */
export function ShareDialog({ target, onClose }: { target: ShareTarget; onClose: () => void }) {
  const me = useMe()
  const queryClient = useQueryClient()
  const dialog = useRef<HTMLDialogElement>(null)
  const heading = useRef<HTMLHeadingElement>(null)
  const id = useId()
  const held = linksHeld(me)
  const key = shareLinksKey(target.kind, target.id)
  const links = useQuery({
    queryKey: key,
    queryFn: () => (target.kind === 'document' ? api.shareLinks(target.id) : api.answerShareLinks(target.chatId, target.id)),
  })
  const [days, setDays] = useState<ShareDays>(DEFAULT_SHARE_DAYS)
  const [password, setPassword] = useState('')
  const [made, setMade] = useState<ShareLinkMade | null>(null)
  const changed = () => {
    void queryClient.invalidateQueries({ queryKey: key })
    void queryClient.invalidateQueries({ queryKey: ORGANIZATION_LINKS_KEY })
  }
  const make = useMutation({
    mutationFn: () => {
      const body = password ? { days, password } : { days }
      return target.kind === 'document' ? api.makeShareLink(target.id, body) : api.makeAnswerShareLink(target.chatId, target.id, body)
    },
    onSuccess: (link) => {
      setMade(link)
      setPassword('')
      changed()
    },
  })

  useEffect(() => {
    const shown = dialog.current
    // Strict mode runs an effect twice, and a dialog already open stays as it is.
    if (!shown || shown.open) return
    shown.showModal()
    ;(shown.querySelector<HTMLElement>('[data-autofocus]') ?? heading.current)?.focus()
  }, [])

  function submit(event: FormEvent) {
    event.preventDefault()
    make.mutate()
  }

  let making: ReactNode
  if (held === 'plan') {
    making = (
      <Notice tone="info">
        <p className="text-ink">The {PLAN_LABELS[me.organization.plan]} plan does not include share links.</p>
        <PlanOffer limit="share_links" />
      </Notice>
    )
  } else if (held === 'off') {
    making = <Notice tone="warn">{LINKS_OFF}</Notice>
  } else if (made) {
    making = (
      <MadeLink
        made={made}
        onDone={() => {
          setMade(null)
          heading.current?.focus()
        }}
      />
    )
  } else {
    making = (
      <form onSubmit={submit} className="space-y-3">
        <SelectField label="Runs for" value={days} onChange={(event) => setDays(Number(event.target.value) as ShareDays)} data-autofocus>
          {SHARE_DAY_CHOICES.map((choice) => (
            <option key={choice.value} value={choice.value}>
              {choice.label}
            </option>
          ))}
        </SelectField>
        <PasswordField
          label="Password, if it should have one"
          hint={`At least ${PASSWORD_MIN_LENGTH} characters. Send it apart from the link, another way.`}
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          minLength={PASSWORD_MIN_LENGTH}
          maxLength={PASSWORD_MAX_LENGTH}
          autoComplete="new-password"
        />
        <Button variant="primary" type="submit" disabled={make.isPending}>
          Make a link
        </Button>
        {make.isError &&
          (isPlanLimit(make.error) ? (
            <PlanLimitNotice refusal={{ message: make.error.message, limit: make.error.limit }} />
          ) : (
            <Notice tone="bad">{make.error.message}</Notice>
          ))}
      </form>
    )
  }

  return (
    <dialog
      ref={dialog}
      aria-labelledby={`${id}-title`}
      aria-describedby={`${id}-body`}
      onClose={onClose}
      className="m-auto w-lg max-w-[calc(100vw-32px)] rounded-xl border border-line bg-surface p-5 text-ink shadow-xl shadow-ink/8 backdrop:bg-scrim"
    >
      <div className="flex items-start justify-between gap-3">
        <h2 ref={heading} tabIndex={-1} id={`${id}-title`} className="font-semibold break-words">
          {target.kind === 'document' ? `Share “${target.name}”` : 'Share this answer'}
        </h2>
        <IconButton label="Close" onClick={() => dialog.current?.close()}>
          <X className="size-4" />
        </IconButton>
      </div>
      <p id={`${id}-body`} className="mt-1.5 text-[13px] leading-relaxed text-muted">
        {SHOWS[target.kind]} with no account, until the link runs out or is revoked. The organisation's audit log records
        each opening.
      </p>
      <div className="mt-4">{making}</div>
      <h3 className="mt-5 text-[13px] font-medium">Links running</h3>
      {links.isError ? (
        <Notice tone="bad">{links.error.message}</Notice>
      ) : links.data && links.data.length > 0 ? (
        <ul className="mt-1 divide-y divide-line border-t border-line">
          {links.data.map((link) => (
            <LinkRow
              key={link.id}
              link={link}
              onRevoked={() => {
                changed()
                heading.current?.focus()
              }}
            />
          ))}
        </ul>
      ) : (
        links.data && <p className="mt-1 text-[13px] text-muted">No link to this {target.kind} is running.</p>
      )}
    </dialog>
  )
}

/** A link just made: its address, once, to copy, which nothing shows again. */
function MadeLink({ made, onDone }: { made: ShareLinkMade; onDone: () => void }) {
  const [copied, setCopied] = useState(false)
  const field = useId()
  // The form it replaces held the focus; the address takes it, selected to copy.
  useEffect(() => {
    window.document.getElementById(field)?.focus()
  }, [field])

  async function copy() {
    try {
      await navigator.clipboard.writeText(made.url)
      setCopied(true)
    } catch {
      // The clipboard can be unavailable; the link is on screen to copy by hand.
    }
  }

  return (
    <div className="space-y-3 text-[13px]">
      <Notice tone="warn">
        Copy the link now: it is not shown again. Whoever holds it reads the {made.kind} until {formatDate(made.expires_at)}.
        {made.password && ' Send its password apart from it.'}
      </Notice>
      <InputField id={field} label="The link" value={made.url} readOnly onFocus={(event) => event.target.select()} />
      <div className="flex gap-2">
        <Button icon={copied ? <Check className="size-4" /> : <Copy className="size-4" />} onClick={copy}>
          {copied ? 'Copied' : 'Copy the link'}
        </Button>
        <Button variant="primary" onClick={onDone}>
          Done
        </Button>
      </div>
    </div>
  )
}

/**
 * A link's row: who made it and until when it runs, and its Revoke for its maker or an owner or admin, which asks first
 * under the row, since the link stops at once for whoever holds it. `about` names what it shows, in the organisation's list.
 */
export function LinkRow({ link, about, onRevoked }: { link: ShareLink; about?: string; onRevoked: () => void }) {
  const me = useMe()
  const [asking, setAsking] = useState(false)
  const revoke = useMutation({ mutationFn: () => api.revokeShareLink(link.id), onSuccess: onRevoked })

  return (
    <li className="py-2.5 text-[13px]">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <div className="min-w-56 flex-1">
          {about && <p className="font-medium break-words">{about}</p>}
          <p className={about ? 'text-xs text-muted' : ''}>{describeLink(link)}</p>
        </div>
        {mayRevoke(link, me) && (
          <Button variant="ghost" onClick={() => setAsking(true)} disabled={revoke.isPending}>
            Revoke
          </Button>
        )}
      </div>
      {asking && (
        <div className="mt-2 space-y-2">
          <Notice tone="warn">The link stops at once for whoever holds it.</Notice>
          <div className="flex gap-2">
            <Button variant="danger" onClick={() => revoke.mutate()} disabled={revoke.isPending}>
              Revoke the link
            </Button>
            <Button variant="ghost" onClick={() => setAsking(false)}>
              Cancel
            </Button>
          </div>
          {revoke.isError && <Notice tone="bad">{revoke.error.message}</Notice>}
        </div>
      )}
    </li>
  )
}
