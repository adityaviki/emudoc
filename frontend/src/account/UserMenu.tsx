import { Building2, FileText, Lock, LogOut, ShieldCheck } from 'lucide-react'
import { useRef, useState } from 'react'
import { useNavigate } from 'react-router'
import { useMe, useSignOut } from '../auth.ts'
import { Floating, Menu } from '../components/Menu.tsx'
import type { MenuItem } from '../components/Menu.tsx'
import { useVersion } from '../hooks.ts'

/** The signed-in user's initials in the header, opening the way to the account page, the
 * terms, the privacy policy and the subprocessor list, and out, above the version that runs. */
export function UserMenu() {
  const me = useMe()
  const signOut = useSignOut()
  const navigate = useNavigate()
  const [anchor, setAnchor] = useState<DOMRect | null>(null)
  const button = useRef<HTMLButtonElement>(null)
  const close = () => setAnchor(null)

  const items: MenuItem[] = [
    { label: 'Account and security', icon: ShieldCheck, onSelect: () => navigate('/account') },
    { label: 'Terms of service', icon: FileText, onSelect: () => navigate('/terms') },
    { label: 'Privacy policy', icon: Lock, onSelect: () => navigate('/privacy') },
    { label: 'Subprocessors', icon: Building2, onSelect: () => navigate('/subprocessors') },
    'separator',
    { label: 'Sign out', icon: LogOut, onSelect: signOut },
  ]

  return (
    <>
      <button
        ref={button}
        type="button"
        aria-haspopup="menu"
        aria-expanded={anchor !== null}
        title={me.name || me.email}
        onClick={(event) => setAnchor(anchor ? null : event.currentTarget.getBoundingClientRect())}
        className="flex size-7 shrink-0 items-center justify-center rounded-full bg-accent-soft text-[11px] font-semibold text-accent transition hover:ring-2 hover:ring-accent/30"
      >
        {initials(me.name, me.email)}
      </button>
      {anchor && (
        <Floating anchor={anchor} alignEnd onClose={close} ignore={button} className="w-60">
          <div className="border-b border-line px-3 py-2">
            <p className="truncate text-[13px] font-medium">{me.name || me.email}</p>
            {me.name && <p className="truncate text-xs text-muted">{me.email}</p>}
          </div>
          <Menu items={items} onClose={close} />
          <RunningVersion />
        </Floating>
      )}
    </>
  )
}

/** The release the API runs, for a member telling support what they saw, or an operator checking a release. */
function RunningVersion() {
  const version = useVersion()
  if (!version.data) return null
  // A commit is shortened as GitHub shortens it, with the whole of it on hover.
  const shown = /^[0-9a-f]{40}$/.test(version.data) ? version.data.slice(0, 7) : version.data
  return (
    <p title={version.data} className="border-t border-line px-3 py-1.5 text-[11px] text-muted">
      Emudoc {shown}
    </p>
  )
}

function initials(name: string, email: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean)
  if (words.length >= 2) return (words[0][0] + words[words.length - 1][0]).toUpperCase()
  return (words[0] ?? email).slice(0, 1).toUpperCase()
}
