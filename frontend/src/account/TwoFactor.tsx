// Setting up two-factor sign-in: the authenticator's secret as a QR code and a key, the code
// that turns it on, then the recovery codes, shown once. The account page uses both steps, and so
// does the page an organisation that requires two-factor shows a member without it
// (MfaPending.tsx).

import { useMutation } from '@tanstack/react-query'
import { Check, Copy } from 'lucide-react'
import { useState } from 'react'
import type { FormEvent } from 'react'
import { auth } from '../api.ts'
import type { MfaSetup } from '../api.ts'
import { Button, InputField, Notice } from '../components/ui.tsx'
import { QrCode } from './QrCode.tsx'

export function SetupStep({ setup, onCancel, onEnabled }: { setup: MfaSetup; onCancel: () => void; onEnabled: (codes: string[]) => void }) {
  const [code, setCode] = useState('')
  const enable = useMutation({
    mutationFn: () => auth.enableMfa(code.trim()),
    onSuccess: (result) => onEnabled(result.recovery_codes),
  })

  function submit(event: FormEvent) {
    event.preventDefault()
    if (code.trim()) enable.mutate()
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-5 sm:flex-row">
      <QrCode value={setup.otpauth_url} label="Scan this with your authenticator app" />
      <div className="min-w-0 flex-1 space-y-3 text-[13px]">
        <p className="leading-relaxed text-muted">
          Scan the code with an authenticator app such as Google Authenticator, Microsoft Authenticator, 1Password or
          Aegis, or{' '}
          <a href={setup.otpauth_url} className="font-medium text-accent hover:underline">
            open it in an app on this device
          </a>
          . If you cannot scan it, enter this key by hand:
        </p>
        <code className="block rounded-md border border-line bg-sunken px-3 py-2 font-mono text-xs tracking-wider break-all select-all">
          {setup.secret.match(/.{1,4}/g)?.join(' ')}
        </code>
        <InputField
          label="Then enter the code the app shows"
          autoComplete="one-time-code"
          inputMode="numeric"
          required
          className="max-w-xs font-mono"
          value={code}
          onChange={(event) => setCode(event.target.value)}
        />
        {enable.isError && <Notice tone="bad">{enable.error.message}</Notice>}
        <div className="flex gap-2">
          <Button type="submit" variant="primary" disabled={!code.trim() || enable.isPending}>
            Turn on
          </Button>
          <Button variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
        </div>
      </div>
    </form>
  )
}

/** Shown once, right after two-factor sign-in is turned on. */
export function RecoveryCodes({ codes, onDone }: { codes: string[]; onDone: () => void }) {
  const [copied, setCopied] = useState(false)

  async function copy() {
    try {
      await navigator.clipboard.writeText(codes.join('\n'))
      setCopied(true)
    } catch {
      // The clipboard can be unavailable; the codes are on screen to copy by hand.
    }
  }

  return (
    <div className="space-y-3 text-[13px]">
      <Notice tone="warn">
        Two-factor sign-in is on. Save these recovery codes somewhere safe: each one signs you in once if you lose
        the app. They are not shown again.
      </Notice>
      <div className="grid grid-cols-2 gap-x-6 gap-y-1 rounded-md border border-line bg-sunken px-4 py-3 font-mono text-xs sm:grid-cols-4">
        {codes.map((code) => (
          <span key={code} className="select-all">
            {code}
          </span>
        ))}
      </div>
      <div className="flex gap-2">
        <Button icon={copied ? <Check className="size-4" /> : <Copy className="size-4" />} onClick={copy}>
          {copied ? 'Copied' : 'Copy the codes'}
        </Button>
        <Button variant="primary" onClick={onDone}>
          I have saved them
        </Button>
      </div>
    </div>
  )
}
