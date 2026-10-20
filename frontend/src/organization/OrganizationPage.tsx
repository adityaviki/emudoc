// The organisation the user works in: its name, plan and region, its members, handing it over
// to another of them and leaving it; the user's other organisations to switch to; and the page
// that starts a new one. A switch, a new organisation and leaving one start the app again in
// the organisation the session then works in (`startAgainInOrganization` in auth.ts).

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, LogOut, Plus } from 'lucide-react'
import { useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useNavigate } from 'react-router'
import { PLAN_LABELS, REGION_NAMES, ROLE_LABELS, organizations } from '../api.ts'
import type { Me, Member } from '../api.ts'
import { setSignedIn, startAgainInOrganization, useMe } from '../auth.ts'
import { PasswordField } from '../account/shared.tsx'
import { Button, Chip, InputField, Loading, Notice, Row, Section } from '../components/ui.tsx'
import { formatDate } from '../format.ts'

const MEMBERS_KEY = ['organization', 'members'] as const

const capitalised = (text: string) => text[0].toUpperCase() + text.slice(1)

export function OrganizationPage() {
  const me = useMe()
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-2xl px-6 py-10">
        <div className="mb-6">
          <h1 className="text-2xl font-semibold tracking-tight">{me.organization.name}</h1>
          <p className="mt-1 text-[13px] text-muted">The organisation you work in: its name, its members, and leaving it.</p>
        </div>
        <div className="space-y-5">
          <NameSection me={me} />
          <MembersSection me={me} />
          <OrganizationsSection me={me} />
          <LeaveSection me={me} />
        </div>
      </div>
    </div>
  )
}

function NameSection({ me }: { me: Me }) {
  const queryClient = useQueryClient()
  const [name, setName] = useState(me.organization.name)
  const rename = useMutation({
    mutationFn: () => organizations.rename(name),
    onSuccess: (account) => setSignedIn(queryClient, account),
  })
  const mayRename = me.role === 'owner' || me.role === 'admin'
  const changed = name.trim() !== me.organization.name

  function submit(event: FormEvent) {
    event.preventDefault()
    if (changed && name.trim()) rename.mutate()
  }

  return (
    <Section title="Organisation">
      {mayRename ? (
        <form onSubmit={submit} className="max-w-sm space-y-3">
          <InputField label="Name" maxLength={200} required value={name} onChange={(event) => setName(event.target.value)} />
          {rename.isError && <Notice tone="bad">{rename.error.message}</Notice>}
          <div className="flex items-center gap-3">
            <Button type="submit" variant="primary" disabled={!changed || !name.trim() || rename.isPending}>
              Rename
            </Button>
            {rename.isSuccess && !changed && <span className="text-[13px] text-muted">Renamed.</span>}
          </div>
        </form>
      ) : (
        <p className="text-[13px] text-muted">Only its owners and admins can rename it.</p>
      )}
      <dl className="mt-4 divide-y divide-line border-t border-line">
        <Row label="Your role">
          <Chip>{ROLE_LABELS[me.role]}</Chip>
        </Row>
        <Row label="Plan">
          <span>{PLAN_LABELS[me.organization.plan]}</span>
        </Row>
        {me.organization.region && (
          <Row label="Data kept in">
            <span>{capitalised(REGION_NAMES[me.organization.region])}</span>
          </Row>
        )}
        <Row label="Identifier">
          <code className="font-mono text-xs">{me.organization.slug}</code>
        </Row>
      </dl>
    </Section>
  )
}

function MembersSection({ me }: { me: Me }) {
  const members = useQuery({ queryKey: MEMBERS_KEY, queryFn: organizations.members })
  // The member an owner is handing the organisation to, while the form asks for the password.
  const [successor, setSuccessor] = useState<Member | null>(null)

  return (
    <Section
      title="Members"
      description={
        me.role === 'owner'
          ? 'Everyone who works in the organisation. Handing it over makes another member its owner, and you an admin.'
          : 'Everyone who works in the organisation.'
      }
    >
      {members.isPending && <Loading />}
      {members.isError && <Notice tone="bad">{members.error.message}</Notice>}
      {members.data && (
        <ul className="divide-y divide-line">
          {members.data.map((member) => (
            <li key={member.user_id} className="py-2.5 text-[13px]">
              <div className="flex items-center gap-3">
                <div className="min-w-0 flex-1">
                  <p className="flex items-center gap-2">
                    <span className="truncate font-medium">{member.name || member.email}</span>
                    {member.user_id === me.id && <Chip tone="accent">You</Chip>}
                  </p>
                  <p className="truncate text-xs text-muted">
                    {member.name && <>{member.email} · </>}
                    Member since {formatDate(member.joined_at)}
                  </p>
                </div>
                <Chip>{ROLE_LABELS[member.role]}</Chip>
                {me.role === 'owner' && member.role !== 'owner' && successor?.user_id !== member.user_id && (
                  <Button variant="ghost" onClick={() => setSuccessor(member)}>
                    Hand over…
                  </Button>
                )}
              </div>
              {successor?.user_id === member.user_id && (
                <HandOverForm me={me} successor={member} onClose={() => setSuccessor(null)} />
              )}
            </li>
          ))}
        </ul>
      )}
    </Section>
  )
}

// Confirmed as deleting the account is: by the password, or the address typed out for an account
// that signs in only through a provider.
function HandOverForm({ me, successor, onClose }: { me: Me; successor: Member; onClose: () => void }) {
  const queryClient = useQueryClient()
  const [confirmation, setConfirmation] = useState('')
  const handOver = useMutation({
    mutationFn: () =>
      organizations.handOver(
        me.has_password
          ? { user_id: successor.user_id, password: confirmation }
          : { user_id: successor.user_id, email: confirmation },
      ),
    onSuccess: (account) => {
      setSignedIn(queryClient, account)
      void queryClient.invalidateQueries({ queryKey: MEMBERS_KEY })
      onClose()
    },
  })

  function submit(event: FormEvent) {
    event.preventDefault()
    if (confirmation.trim()) handOver.mutate()
  }

  return (
    <form onSubmit={submit} className="mt-3 max-w-sm space-y-3">
      <Notice tone="warn">
        {successor.name || successor.email} becomes an owner of {me.organization.name}, and you an admin: only an owner
        can make you one again.
      </Notice>
      {me.has_password ? (
        <PasswordField
          label="Your password"
          autoComplete="current-password"
          required
          value={confirmation}
          onChange={(event) => setConfirmation(event.target.value)}
        />
      ) : (
        <InputField
          label={`Type ${me.email} to confirm`}
          autoComplete="off"
          required
          value={confirmation}
          onChange={(event) => setConfirmation(event.target.value)}
        />
      )}
      {handOver.isError && <Notice tone="bad">{handOver.error.message}</Notice>}
      <div className="flex gap-2">
        <Button type="submit" variant="primary" disabled={!confirmation.trim() || handOver.isPending}>
          Hand over
        </Button>
        <Button variant="ghost" onClick={onClose}>
          Cancel
        </Button>
      </div>
    </form>
  )
}

function OrganizationsSection({ me }: { me: Me }) {
  const switching = useMutation({ mutationFn: organizations.switch, onSettled: startAgainInOrganization })
  return (
    <Section title="Your organisations" description="Switching changes the organisation every tab of this browser works in.">
      <ul className="divide-y divide-line">
        {me.organizations.map((organization) => (
          <li key={organization.id} className="flex items-center gap-3 py-2 text-[13px]">
            <span className="min-w-0 flex-1 truncate font-medium">{organization.name}</span>
            <Chip>{ROLE_LABELS[organization.role]}</Chip>
            {organization.id === me.organization.id ? (
              <span className="flex w-20 items-center justify-end gap-1 text-muted">
                <Check className="size-3.5" /> Current
              </span>
            ) : (
              <Button className="w-20 justify-center" onClick={() => switching.mutate(organization.id)} disabled={switching.isPending}>
                Switch
              </Button>
            )}
          </li>
        ))}
      </ul>
      <div className="mt-3">
        <Link to="/organizations/new" className="inline-flex items-center gap-1.5 text-[13px] font-medium text-accent hover:underline">
          <Plus className="size-4" />
          New organisation
        </Link>
      </div>
    </Section>
  )
}

function LeaveSection({ me }: { me: Me }) {
  const [confirming, setConfirming] = useState(false)
  const leave = useMutation({ mutationFn: organizations.leave, onSuccess: startAgainInOrganization })
  return (
    <Section
      title="Leave the organisation"
      description="You stop being a member: its projects and documents stay with it, and so do the chats you shared with a project. Your private chats in its projects go. Only an invitation lets you back in."
    >
      {confirming ? (
        <div className="space-y-3">
          <Notice tone="warn">Leave {me.organization.name}? You will work in another of your organisations.</Notice>
          {leave.isError && <Notice tone="bad">{leave.error.message}</Notice>}
          <div className="flex gap-2">
            <Button variant="danger" icon={<LogOut className="size-4" />} onClick={() => leave.mutate()} disabled={leave.isPending}>
              Leave {me.organization.name}
            </Button>
            <Button variant="ghost" onClick={() => setConfirming(false)}>
              Cancel
            </Button>
          </div>
        </div>
      ) : (
        <Button variant="danger" onClick={() => setConfirming(true)}>
          Leave…
        </Button>
      )}
    </Section>
  )
}

/** Starts an organisation the user owns, and works in it from then on. */
export function NewOrganizationPage() {
  const navigate = useNavigate()
  const [name, setName] = useState('')
  const start = useMutation({ mutationFn: () => organizations.start(name), onSuccess: startAgainInOrganization })

  function submit(event: FormEvent) {
    event.preventDefault()
    if (name.trim()) start.mutate()
  }

  // Back to the page the user came from, or the front page when this one was opened directly.
  function back() {
    if (window.history.state?.idx) navigate(-1)
    else navigate('/')
  }

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-2xl px-6 py-10">
        <div className="mb-6">
          <h1 className="text-2xl font-semibold tracking-tight">New organisation</h1>
          <p className="mt-1 text-[13px] text-muted">
            An organisation of its own, with its own projects, members and plan. It starts empty on the Free plan, with you
            as its owner, and you work in it from then on; your other organisations stay as they are.
          </p>
        </div>
        <form onSubmit={submit} className="rounded-xl border border-line bg-surface p-5 shadow-xs">
          <div className="max-w-sm space-y-3">
            <InputField
              label="Name"
              autoFocus
              maxLength={200}
              required
              placeholder="e.g. Acme Legal"
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
            {start.isError && <Notice tone="bad">{start.error.message}</Notice>}
          </div>
          <div className="mt-4 flex justify-end gap-2">
            <Button variant="ghost" onClick={back}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" disabled={!name.trim() || start.isPending}>
              Create organisation
            </Button>
          </div>
        </form>
      </div>
    </div>
  )
}
