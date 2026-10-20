// The organisation the header shows, opening a menu of the user's organisations to switch to,
// those the domain of their address lets them join (on the organisation's page, which says why
// a join is refused), the organisation's page, and the way to start another.

import { useMutation } from '@tanstack/react-query'
import { Check, ChevronDown, Plus, Settings, UserPlus } from 'lucide-react'
import { useRef, useState } from 'react'
import { useNavigate } from 'react-router'
import { organizations } from '../api.ts'
import { startAgainInOrganization, useMe } from '../auth.ts'
import { Floating, Menu } from '../components/Menu.tsx'
import type { MenuItem } from '../components/Menu.tsx'

export function OrganizationSwitcher() {
  const me = useMe()
  const navigate = useNavigate()
  const [anchor, setAnchor] = useState<DOMRect | null>(null)
  const button = useRef<HTMLButtonElement>(null)
  // A switch that failed, because the organisation went meanwhile, starts again all the same,
  // in the organisation the session works in, with the list as it is now.
  const switching = useMutation({ mutationFn: organizations.switch, onSettled: startAgainInOrganization })

  const items: MenuItem[] = [
    ...me.organizations.map(
      (organization): MenuItem => ({
        label: organization.name,
        icon: organization.id === me.organization.id ? Check : undefined,
        disabled: switching.isPending,
        onSelect: () => {
          if (organization.id !== me.organization.id) switching.mutate(organization.id)
        },
      }),
    ),
    ...me.joinable.map(
      (organization): MenuItem => ({
        label: `Join ${organization.name}…`,
        icon: UserPlus,
        onSelect: () => navigate('/organization'),
      }),
    ),
    'separator',
    { label: 'Organisation settings', icon: Settings, onSelect: () => navigate('/organization') },
    { label: 'New organisation', icon: Plus, onSelect: () => navigate('/organizations/new') },
  ]

  return (
    <>
      <button
        ref={button}
        type="button"
        aria-haspopup="menu"
        aria-expanded={anchor !== null}
        title="Your organisation"
        onClick={(event) => setAnchor(anchor ? null : event.currentTarget.getBoundingClientRect())}
        className="hidden h-7 min-w-0 items-center gap-1 rounded-md px-2 text-[13px] text-muted transition hover:bg-sunken hover:text-ink sm:flex"
      >
        <span className="truncate">{me.organization.name}</span>
        <ChevronDown className="size-3.5 shrink-0" />
      </button>
      {anchor && (
        <Floating anchor={anchor} onClose={() => setAnchor(null)} ignore={button} className="w-64">
          <p className="px-3 pt-2 text-[11px] font-medium text-muted">Your organisations</p>
          <Menu items={items} onClose={() => setAnchor(null)} />
        </Floating>
      )}
    </>
  )
}
