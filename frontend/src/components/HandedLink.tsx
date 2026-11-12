import { Check, Copy } from 'lucide-react'
import { useState } from 'react'
import type { InvitationSent } from '../api.ts'
import { Button, InputField } from './ui.tsx'

/**
 * A link to copy and hand on, as an invitation's or a reset link, shown once to whoever made it: an install may mail
 * nothing (backend/app/signups.py), and then this is the way it arrives. The field selects it whole, for when the
 * clipboard is unavailable.
 */
export function HandedLink({ label, link }: { label: string; link: string }) {
  const [copied, setCopied] = useState(false)

  async function copy() {
    try {
      await navigator.clipboard.writeText(link)
      setCopied(true)
    } catch {
      // The clipboard can be unavailable; the link is in the field to copy by hand.
    }
  }

  return (
    <div className="flex flex-wrap items-end gap-2">
      <InputField
        label={label}
        readOnly
        value={link}
        className="min-w-56 flex-1"
        onFocus={(event) => event.currentTarget.select()}
      />
      <Button icon={copied ? <Check className="size-4" /> : <Copy className="size-4" />} onClick={copy}>
        {copied ? 'Copied' : 'Copy the link'}
      </Button>
    </div>
  )
}

/** Where an invitation just made or sent again went, and its link to hand on, which is all there is without mail. */
export function SentInvitation({ sent }: { sent: InvitationSent }) {
  return (
    <div className="space-y-2 text-[13px]">
      <p className="text-muted">
        {sent.mailed
          ? `An invitation is on its way to ${sent.email}. You can also send them its link yourself:`
          : `This Emudoc sends no mail, so nothing went to ${sent.email}: copy the invitation's link and send it to them yourself.`}
      </p>
      <HandedLink label={`Invitation link for ${sent.email}`} link={sent.link} />
      <p className="text-xs text-muted">
        It works for a week, until it is accepted or sent again, and it is not shown again: send it again for a new one.
      </p>
    </div>
  )
}
