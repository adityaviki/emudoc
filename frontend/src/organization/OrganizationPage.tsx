// The organisation the user works in: its name and region, what its plan lets it keep and use and buying one, its members,
// their roles and removing them, handing it over to another of them, the invitations its owners and admins send
// and the domains whose addresses may join it, and leaving it; the user's other organisations to switch to and those
// open to them; and the page that starts a new one. A switch, a join, a new organisation and
// leaving one start the app again in the organisation the session then works in
// (`startAgainInOrganization` in auth.ts).

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, CreditCard, LogOut, Plus, UserMinus, UserPlus } from 'lucide-react'
import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router'
import { PLAN_LABELS, REGION_NAMES, ROLES, ROLE_LABELS, isPlanLimit, isWithin, organizations } from '../api.ts'
import type { Allowance, Invitation, JoinRole, Me, Member, Role, SoldPlan } from '../api.ts'
import { setSignedIn, startAgainInOrganization, useMe } from '../auth.ts'
import { describeBilling, mayBuy } from '../billing.ts'
import { PasswordField } from '../account/shared.tsx'
import { PlanLimitNotice } from '../components/PlanLimit.tsx'
import { Button, Chip, FIELD, InputField, Loading, Notice, Row, Section, SelectField } from '../components/ui.tsx'
import { formatDate, formatSize } from '../format.ts'

const MEMBERS_KEY = ['organization', 'members'] as const
const INVITATIONS_KEY = ['organization', 'invitations'] as const
const JOIN_ROLES: JoinRole[] = ['member', 'viewer']

const capitalised = (text: string) => text[0].toUpperCase() + text.slice(1)

export function OrganizationPage() {
  const me = useMe()
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-2xl px-6 py-10">
        <div className="mb-6">
          <h1 className="text-2xl font-semibold tracking-tight">{me.organization.name}</h1>
          <p className="mt-1 text-[13px] text-muted">The organisation you work in: its name, its plan, its members, who may join it, and leaving it.</p>
        </div>
        <div className="space-y-5">
          <NameSection me={me} />
          <PlanSection me={me} />
          <MembersSection me={me} />
          {isWithin('admin', me.role) && (
            <>
              <InvitationsSection me={me} />
              <JoiningSection me={me} />
            </>
          )}
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

// What the organisation may keep and use, as the server holds it to them
// (backend/app/entitlements.py): its plan's figures, the seats it bought on a plan sold by the
// seat, and any figure agreed with it in the plan's place; and how its subscription stands.
function PlanSection({ me }: { me: Me }) {
  const { plan, seats, allowance, chat_retention_days: keptFor, billing } = me.organization
  const figure = (value: number | null, words: (value: number) => string = (n) => n.toLocaleString()) =>
    value === null ? 'No limit' : words(value)
  const monthly = (n: number) => `${n.toLocaleString()} a month`
  const agreed = (name: keyof Allowance | 'retention_days') =>
    allowance.agreed.includes(name) && (
      <span title="Agreed with the organisation in place of the plan's">
        <Chip>Agreed</Chip>
      </span>
    )
  const sells = billing.sold.length > 0 && billing.managed
  const standing = describeBilling(billing, formatDate)
  const tones = { good: 'text-good', warn: 'text-warn', bad: 'text-bad' }

  return (
    <Section
      title="Plan"
      description={`What the organisation may keep and use, counted across its projects and members. Pages and questions start afresh on the first of each month, in UTC. ${sells ? 'Its owners buy and change the plan here.' : 'Contact the Emudoc team to change the plan.'}`}
    >
      <dl className="divide-y divide-line border-t border-line">
        <Row label="Plan">{PLAN_LABELS[plan]}</Row>
        {standing && (
          <Row label="Subscription">
            <span className={tones[standing.tone]}>{standing.text}</span>
          </Row>
        )}
        <Row label="Projects">
          {figure(allowance.projects)}
          {agreed('projects')}
        </Row>
        <Row label="Documents">
          {figure(allowance.documents)}
          {agreed('documents')}
        </Row>
        <Row label="Storage">
          {figure(allowance.storage_bytes, formatSize)}
          {agreed('storage_bytes')}
        </Row>
        <Row label="Pages indexed">
          {figure(allowance.pages_per_month, monthly)}
          {agreed('pages_per_month')}
        </Row>
        <Row label="Questions">
          {figure(allowance.questions_per_month, monthly)}
          {agreed('questions_per_month')}
        </Row>
        <Row label="Members">
          {figure(allowance.members, (n) => (seats === null ? n.toLocaleString() : n === 1 ? '1 seat' : `${n.toLocaleString()} seats`))}
          {agreed('members')}
        </Row>
        <Row label="Chats kept">
          {keptFor === null ? 'Until deleted' : `${keptFor.toLocaleString()} days after the last question`}
          {agreed('retention_days')}
        </Row>
      </dl>
      {sells && <BillingActions me={me} />}
    </Section>
  )
}

// Buying a plan on Stripe's Checkout page, managing it on Stripe's customer portal, and the way
// back from Checkout, whose session the server asks Stripe about (backend/app/api/billing.py).
// Both pages are Stripe's: the browser goes to the address the server gives.
function BillingActions({ me }: { me: Me }) {
  const queryClient = useQueryClient()
  const { billing, seats: bought } = me.organization
  const [params, setParams] = useSearchParams()
  const returned = params.get('checkout')
  const members = useQuery({ queryKey: MEMBERS_KEY, queryFn: organizations.members })
  // The seats to buy, the members the organisation has until the owner types a number.
  const [seats, setSeats] = useState<string | null>(null)
  const seatCount = seats ?? String(bought ?? Math.max(1, members.data?.length ?? 1))
  const away = ({ url }: { url: string }) => window.location.assign(url)
  const buy = useMutation({
    mutationFn: (plan: SoldPlan) => organizations.checkout(plan === 'team' ? { plan, seats: Number(seatCount) } : { plan }),
    onSuccess: away,
  })
  const manage = useMutation({ mutationFn: organizations.billingPortal, onSuccess: away })
  const done = useMutation({
    mutationFn: organizations.checkoutDone,
    onSuccess: (account) => setSignedIn(queryClient, account),
  })
  const { mutate: finish } = done

  useEffect(() => {
    if (returned) {
      finish(returned)
      setParams({}, { replace: true })
    }
  }, [returned, finish, setParams])

  const owner = me.role === 'owner'
  // Stripe's page is loading once an address came back: nothing is offered twice meanwhile.
  const leaving = buy.isPending || manage.isPending || buy.isSuccess || manage.isSuccess
  const error = buy.error ?? manage.error

  return (
    <div className="mt-4 space-y-3">
      {done.isPending && <Notice tone="info">Asking Stripe about the payment…</Notice>}
      {done.isSuccess && <Notice tone="info">Thank you: the organisation is on the {PLAN_LABELS[done.data.organization.plan]} plan.</Notice>}
      {done.isError && <Notice tone="bad">{done.error.message}</Notice>}
      {error && <Notice tone="bad">{error.message}</Notice>}
      {!owner ? (
        <p className="text-[13px] text-muted">Only its owners buy a plan or manage its billing.</p>
      ) : (
        <>
          {mayBuy(billing) && (
            <>
              {billing.trial_days !== null && (
                <p className="text-[13px] text-muted">
                  A plan bought now starts with a {billing.trial_days}-day trial, and is charged when the trial ends unless it is cancelled first.
                </p>
              )}
              <div className="flex flex-wrap items-end gap-3">
                {billing.sold.includes('pro') && (
                  <Button variant="primary" disabled={leaving} onClick={() => buy.mutate('pro')}>
                    Buy Pro
                  </Button>
                )}
                {billing.sold.includes('team') && (
                  <>
                    <InputField
                      label="Seats"
                      type="number"
                      min={1}
                      max={10000}
                      className="w-24"
                      value={seatCount}
                      onChange={(event) => setSeats(event.target.value)}
                    />
                    <Button
                      variant={billing.sold.includes('pro') ? 'secondary' : 'primary'}
                      disabled={leaving || !(Number(seatCount) >= 1)}
                      onClick={() => buy.mutate('team')}
                    >
                      Buy Team
                    </Button>
                  </>
                )}
              </div>
            </>
          )}
          {billing.customer && (
            <div>
              <Button icon={<CreditCard className="size-4" />} disabled={leaving} onClick={() => manage.mutate()}>
                Manage billing
              </Button>
              <p className="mt-1.5 text-[13px] text-muted">
                The plan, the seats, the payment method, the billing address and VAT id, cancelling, and the invoices, on Stripe’s page.
              </p>
            </div>
          )}
        </>
      )}
    </div>
  )
}

function MembersSection({ me }: { me: Me }) {
  const members = useQuery({ queryKey: MEMBERS_KEY, queryFn: organizations.members })
  const handOver = me.role === 'owner' ? ' Handing it over makes another member its owner, and you an admin.' : ''

  return (
    <Section
      title="Members"
      description={`Everyone who works in the organisation. Viewers read its projects and ask questions, members also change them, admins also manage the members, and owners the owners too.${handOver}`}
    >
      {members.isPending && <Loading />}
      {members.isError && <Notice tone="bad">{members.error.message}</Notice>}
      {members.data && (
        <ul className="divide-y divide-line">
          {members.data.map((member) => (
            <MemberRow key={member.user_id} me={me} member={member} />
          ))}
        </ul>
      )}
    </Section>
  )
}

// What the server lets the user do to another member: an owner anything, an admin anything to
// anyone but an owner (backend/app/api/organizations.py); their own role nobody changes here.
function MemberRow({ me, member }: { me: Me; member: Member }) {
  const queryClient = useQueryClient()
  // Handing the organisation over asks for the password; removing asks to be sure.
  const [open, setOpen] = useState<'hand over' | 'remove' | null>(null)
  const refresh = () => queryClient.invalidateQueries({ queryKey: MEMBERS_KEY })
  const changeRole = useMutation({
    mutationFn: (role: Role) => organizations.changeRole(member.user_id, role),
    // Shown at once, and in the list's order once it is fetched again.
    onSuccess: (changed) =>
      queryClient.setQueryData<Member[]>(MEMBERS_KEY, (list) => list?.map((m) => (m.user_id === changed.user_id ? changed : m))),
    onSettled: refresh,
  })
  const remove = useMutation({ mutationFn: () => organizations.removeMember(member.user_id), onSuccess: refresh })
  const yours = member.user_id === me.id
  const manageable = !yours && isWithin('admin', me.role) && isWithin(member.role, me.role)
  const name = member.name || member.email

  return (
    <li className="py-2.5 text-[13px]">
      <div className="flex items-center gap-3">
        <div className="min-w-0 flex-1">
          <p className="flex items-center gap-2">
            <span className="truncate font-medium">{name}</span>
            {yours && <Chip tone="accent">You</Chip>}
          </p>
          <p className="truncate text-xs text-muted">
            {member.name && <>{member.email} · </>}
            Member since {formatDate(member.joined_at)}
          </p>
        </div>
        {manageable ? (
          <select
            aria-label={`Role of ${name}`}
            className={`${FIELD} w-auto! py-1 text-[13px]`}
            value={changeRole.isPending ? changeRole.variables : member.role}
            disabled={changeRole.isPending || remove.isPending}
            onChange={(event) => changeRole.mutate(event.target.value as Role)}
          >
            {ROLES.filter((option) => isWithin(option, me.role)).map((option) => (
              <option key={option} value={option}>
                {ROLE_LABELS[option]}
              </option>
            ))}
          </select>
        ) : (
          <Chip>{ROLE_LABELS[member.role]}</Chip>
        )}
        {me.role === 'owner' && member.role !== 'owner' && open === null && (
          <Button variant="ghost" onClick={() => setOpen('hand over')}>
            Hand over…
          </Button>
        )}
        {manageable && open === null && (
          <Button variant="ghost" onClick={() => setOpen('remove')}>
            Remove…
          </Button>
        )}
      </div>
      {changeRole.isError && (
        <div className="mt-2">
          <Notice tone="bad">{changeRole.error.message}</Notice>
        </div>
      )}
      {open === 'hand over' && <HandOverForm me={me} successor={member} onClose={() => setOpen(null)} />}
      {open === 'remove' && (
        <div className="mt-3 max-w-md space-y-3">
          <Notice tone="warn">
            Remove {name} from {me.organization.name}? Their private chats in its projects go, the chats they shared stay.
            Only an invitation lets them back in.
          </Notice>
          {remove.isError && <Notice tone="bad">{remove.error.message}</Notice>}
          <div className="flex gap-2">
            <Button variant="danger" icon={<UserMinus className="size-4" />} onClick={() => remove.mutate()} disabled={remove.isPending}>
              Remove {name}
            </Button>
            <Button variant="ghost" onClick={() => setOpen(null)}>
              Cancel
            </Button>
          </div>
        </div>
      )}
    </li>
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

// Owners and admins only, as the server holds them: who is invited, and inviting more.
function InvitationsSection({ me }: { me: Me }) {
  const queryClient = useQueryClient()
  const invitations = useQuery({ queryKey: INVITATIONS_KEY, queryFn: organizations.invitations })
  const [email, setEmail] = useState('')
  const [role, setRole] = useState<Role>('member')
  const invite = useMutation({
    mutationFn: () => organizations.invite({ email: email.trim(), role }),
    onSuccess: () => {
      setEmail('')
      void queryClient.invalidateQueries({ queryKey: INVITATIONS_KEY })
    },
  })

  function submit(event: FormEvent) {
    event.preventDefault()
    if (email.trim()) invite.mutate()
  }

  return (
    <Section
      title="Invitations"
      description="Invite someone by email: the link they are sent lets them join with the role you choose, and makes their account if they have none. An invitation takes a place among the members your plan allows until it is accepted, revoked or runs out after a week."
    >
      <form onSubmit={submit} className="flex flex-wrap items-end gap-2">
        <InputField
          label="Email"
          type="email"
          required
          autoComplete="off"
          className="min-w-56 flex-1"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
        />
        <SelectField label="Role" value={role} onChange={(event) => setRole(event.target.value as Role)}>
          {ROLES.filter((option) => isWithin(option, me.role)).map((option) => (
            <option key={option} value={option}>
              {ROLE_LABELS[option]}
            </option>
          ))}
        </SelectField>
        <Button type="submit" variant="primary" icon={<UserPlus className="size-4" />} disabled={!email.trim() || invite.isPending}>
          Invite
        </Button>
      </form>
      <div className="mt-3 space-y-3">
        {invite.isError &&
          (isPlanLimit(invite.error) ? (
            <PlanLimitNotice message={invite.error.message} onDismiss={() => invite.reset()} />
          ) : (
            <Notice tone="bad">{invite.error.message}</Notice>
          ))}
        {invite.isSuccess && (
          <p className="text-[13px] text-muted">An invitation is on its way to {invite.data.email}.</p>
        )}
      </div>
      {invitations.isPending && <Loading />}
      {invitations.isError && <Notice tone="bad">{invitations.error.message}</Notice>}
      {invitations.data &&
        (invitations.data.length ? (
          <ul className="mt-3 divide-y divide-line border-t border-line">
            {invitations.data.map((invitation) => (
              <InvitationRow key={invitation.id} me={me} invitation={invitation} />
            ))}
          </ul>
        ) : (
          <p className="mt-3 text-[13px] text-muted">Nobody is invited at the moment.</p>
        ))}
    </Section>
  )
}

function InvitationRow({ me, invitation }: { me: Me; invitation: Invitation }) {
  const queryClient = useQueryClient()
  const refresh = () => queryClient.invalidateQueries({ queryKey: INVITATIONS_KEY })
  const resend = useMutation({ mutationFn: () => organizations.resendInvitation(invitation.id), onSuccess: refresh })
  const revoke = useMutation({ mutationFn: () => organizations.revokeInvitation(invitation.id), onSuccess: refresh })
  const error = resend.error ?? revoke.error

  return (
    <li className="py-2.5 text-[13px]">
      <div className="flex items-center gap-3">
        <div className="min-w-0 flex-1">
          <p className="truncate font-medium">{invitation.email}</p>
          <p className="truncate text-xs text-muted">
            {invitation.invited_by && <>Sent by {invitation.invited_by} · </>}
            {invitation.expired ? (
              <span className="text-warn">Ran out on {formatDate(invitation.expires_at)}</span>
            ) : (
              <>Open until {formatDate(invitation.expires_at)}</>
            )}
          </p>
        </div>
        <Chip>{ROLE_LABELS[invitation.role]}</Chip>
        {/* Only an owner sends an owner's invitation again; anyone here may revoke one. */}
        {isWithin(invitation.role, me.role) && (
          <Button variant="ghost" onClick={() => resend.mutate()} disabled={resend.isPending || revoke.isPending}>
            {resend.isSuccess ? 'Sent' : 'Send again'}
          </Button>
        )}
        <Button variant="ghost" onClick={() => revoke.mutate()} disabled={resend.isPending || revoke.isPending}>
          Revoke
        </Button>
      </div>
      {error && (
        <div className="mt-2">
          {isPlanLimit(error) ? <PlanLimitNotice message={error.message} /> : <Notice tone="bad">{error.message}</Notice>}
        </div>
      )}
    </li>
  )
}

// Owners and admins only. The page offers the domain of the user's own confirmed address, which
// is the one the server lets them add; any domain may be taken off.
function JoiningSection({ me }: { me: Me }) {
  const queryClient = useQueryClient()
  const { join_domains: domains, join_role: role } = me.organization
  const own = me.email.slice(me.email.lastIndexOf('@') + 1).toLowerCase()
  const change = useMutation({
    mutationFn: organizations.setJoining,
    onSuccess: (account) => setSignedIn(queryClient, account),
  })

  return (
    <Section
      title="Joining by email domain"
      description="Everyone whose confirmed address is at one of these domains is offered the organisation, and joins it if they choose, with the role below. A domain is added by someone whose own confirmed address is at it."
    >
      {domains.length ? (
        <ul className="divide-y divide-line">
          {domains.map((domain) => (
            <li key={domain} className="flex items-center gap-3 py-2 text-[13px]">
              <span className="min-w-0 flex-1 truncate font-medium">@{domain}</span>
              <Button
                variant="ghost"
                onClick={() => change.mutate({ domains: domains.filter((other) => other !== domain), role })}
                disabled={change.isPending}
              >
                Remove
              </Button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-[13px] text-muted">Nobody joins without an invitation.</p>
      )}
      <div className="mt-3 flex flex-wrap items-end gap-3">
        <SelectField
          label="They join as"
          value={role}
          disabled={change.isPending}
          onChange={(event) => change.mutate({ domains, role: event.target.value as JoinRole })}
        >
          {JOIN_ROLES.map((option) => (
            <option key={option} value={option}>
              {ROLE_LABELS[option]}
            </option>
          ))}
        </SelectField>
        {me.email_verified && !domains.includes(own) && (
          <Button icon={<Plus className="size-4" />} onClick={() => change.mutate({ domains: [...domains, own], role })} disabled={change.isPending}>
            Let everyone at @{own} join
          </Button>
        )}
      </div>
      {!me.email_verified && (
        <p className="mt-3 text-[13px] text-muted">Confirm your email address to let everyone at its domain join.</p>
      )}
      {change.isError && (
        <div className="mt-3">
          <Notice tone="bad">{change.error.message}</Notice>
        </div>
      )}
    </Section>
  )
}

function OrganizationsSection({ me }: { me: Me }) {
  const switching = useMutation({ mutationFn: organizations.switch, onSettled: startAgainInOrganization })
  const joining = useMutation({ mutationFn: organizations.join, onSuccess: startAgainInOrganization })
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
      {me.joinable.length > 0 && (
        <>
          <p className="mt-4 text-xs font-medium text-muted">Open to everyone at your address's domain</p>
          <ul className="divide-y divide-line">
            {me.joinable.map((organization) => (
              <li key={organization.id} className="flex items-center gap-3 py-2 text-[13px]">
                <span className="min-w-0 flex-1 truncate font-medium">{organization.name}</span>
                <Chip>{ROLE_LABELS[organization.role]}</Chip>
                <Button className="w-20 justify-center" onClick={() => joining.mutate(organization.id)} disabled={joining.isPending}>
                  Join
                </Button>
              </li>
            ))}
          </ul>
          {joining.isError && <Notice tone="bad">{joining.error.message}</Notice>}
        </>
      )}
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
