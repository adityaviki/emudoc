// The organisation the user works in: its name and region, its settings and security settings (its audit log is
// AuditPage.tsx), the API keys its owners and admins make for its programs, what its plan lets it keep and use (what it holds and used of it is the usage page's, UsagePage.tsx)
// and buying one, its members, their roles and removing them, handing it over to another of them, the invitations its
// owners and admins send and the domains whose addresses may join it, leaving it, and its owners deleting it or keeping it; the user's other organisations to switch to and those open to them; and the page that starts a new
// one. A switch, a join, a new organisation and leaving one start the app again in the organisation the session then
// works in (`startAgainInOrganization` in auth.ts).

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, Copy, CreditCard, History, KeyRound, LogOut, Plus, Trash2, UserMinus, UserPlus } from 'lucide-react'
import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router'
import { DELETION_GRACE_DAYS, PLAN_LABELS, REGION_NAMES, ROLES, ROLE_LABELS, isPlanLimit, isWithin, organizations } from '../api.ts'
import type {
  Allowance,
  ApiKey,
  ApiKeyMade,
  ApiKeyScope,
  Invitation,
  JoinRole,
  Languages,
  Me,
  Member,
  PlanChoice,
  Role,
  SoldPlan,
} from '../api.ts'
import { KEY_DAYS, SCOPES, callWith, daysOf, describeKey, mayRoll } from '../apiKeys.ts'
import { ME_KEY, setSignedIn, startAgainInOrganization, useMe } from '../auth.ts'
import { describeBilling, describeChange, describeExcess, describeHeld, mayBuy, mayChange, thisMonth } from '../billing.ts'
import { PasswordField } from '../account/shared.tsx'
import { NoticeChoices } from '../components/NoticeChoices.tsx'
import { PlanLimitNotice, PlanOffer } from '../components/PlanLimit.tsx'
import { Button, Checkbox, Chip, FIELD, InputField, LinkButton, Loading, Notice, Radio, Row, Section, SelectField } from '../components/ui.tsx'
import { formatDate, formatSize } from '../format.ts'
import { INVITATIONS_SECTION, JOINING_SECTION } from '../onboarding.ts'
import {
  DELETION_SECTION,
  chatDayChoices,
  chosenLanguage,
  deletes,
  deletionWords,
  heldByTwoFactor,
  keptWords,
  languageChoices,
  languageName,
  sessionDayChoices,
  signInWords,
} from '../organizationSettings.ts'

const MEMBERS_KEY = ['organization', 'members'] as const
const LANGUAGES_KEY = ['organization', 'languages'] as const
const INVITATIONS_KEY = ['organization', 'invitations'] as const
const KEYS_KEY = ['organization', 'keys'] as const
const JOIN_ROLES: JoinRole[] = ['member', 'viewer']

const capitalised = (text: string) => text[0].toUpperCase() + text.slice(1)

export function OrganizationPage() {
  const me = useMe()
  const { hash } = useLocation()
  // A link to a section opens the page there, with its first field ready to type in.
  useEffect(() => {
    const section = hash ? document.getElementById(hash.slice(1)) : null
    if (!section) return
    section.scrollIntoView({ block: 'start' })
    section.querySelector<HTMLInputElement>('input')?.focus({ preventScroll: true })
  }, [hash])
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-2xl px-6 py-10">
        <div className="mb-6">
          <h1 className="text-2xl font-semibold tracking-tight">{me.organization.name}</h1>
          <p className="mt-1 text-[13px] text-muted">The organisation you work in: its name and settings, how its members sign in, its API keys, its plan, its members, who may join it, leaving it, and deleting it.</p>
        </div>
        <div className="space-y-5">
          <NameSection me={me} />
          <SettingsSection me={me} />
          <SecuritySection me={me} />
          {isWithin('admin', me.role) && <ApiKeysSection me={me} />}
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
          {(me.role === 'owner' || me.organization.deletion_due_at) && <DeleteSection me={me} />}
        </div>
      </div>
    </div>
  )
}

function NameSection({ me }: { me: Me }) {
  const queryClient = useQueryClient()
  const [name, setName] = useState(me.organization.name)
  const rename = useMutation({
    mutationFn: () => organizations.change({ name }),
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

// The language its documents are searched in, how long it keeps a chat within its plan, and how
// its members are told each kind of notice until they choose (backend/app/api/organizations.py).
// Owners and admins change them as they choose; the others read the first two.
function SettingsSection({ me }: { me: Me }) {
  const organization = me.organization
  const mayChange = isWithin('admin', me.role)
  const languages = useQuery({ queryKey: LANGUAGES_KEY, queryFn: organizations.languages, staleTime: Infinity, enabled: mayChange })

  if (!mayChange) {
    return (
      <Section title="Settings" description="Chosen by its owners and admins for everyone in the organisation.">
        <dl className="divide-y divide-line border-t border-line">
          <Row label="Language">{organization.language === null ? 'As Emudoc searches by default' : languageName(organization.language)}</Row>
          <Row label="Chats kept">{keptWords(organization.chat_retention_days)}</Row>
        </dl>
      </Section>
    )
  }

  return (
    <Section title="Settings" description="Chosen for everyone in the organisation.">
      <div className="max-w-md space-y-4">
        {languages.data && <LanguageChoice me={me} languages={languages.data} />}
        <ChatDays me={me} />
      </div>
      <h3 className="mt-5 mb-2 text-[13px] font-medium">How members are told, until they choose for themselves</h3>
      <NoticeChoices
        key={JSON.stringify(organization.notice_defaults)}
        saved={organization.notice_defaults}
        change={(choice) => organizations.change({ notices: [choice] })}
      />
    </Section>
  )
}

// The select shows the choice being saved, then the saved one, which a failure leaves as it was.
function LanguageChoice({ me, languages }: { me: Me; languages: Languages }) {
  const queryClient = useQueryClient()
  const save = useMutation({
    mutationFn: (chosen: string) => organizations.change({ language: chosen || null }),
    onSuccess: (account) => setSignedIn(queryClient, account),
  })
  const language = save.isPending ? save.variables : chosenLanguage(me.organization, languages)

  return (
    <div className="space-y-2">
      <SelectField
        label="Language of its documents"
        hint="Search matches the words of its documents by their stems in this language, and answers are in it when a question does not show another. Documents indexed before move to it within minutes."
        value={language}
        disabled={save.isPending}
        onChange={(event) => save.mutate(event.target.value)}
      >
        {languageChoices(languages).map((choice) => (
          <option key={choice.value} value={choice.value}>
            {choice.label}
          </option>
        ))}
      </SelectField>
      {save.isError && <Notice tone="bad">{save.error.message}</Notice>}
    </div>
  )
}

// A shorter time deletes chats for every member, so it is saved by a button, after the page has
// said what goes; once saved, the choice is the saved one, and a failure leaves it to try again.
function ChatDays({ me }: { me: Me }) {
  const queryClient = useQueryClient()
  const organization = me.organization
  const [days, setDays] = useState(organization.chat_days === null ? '' : String(organization.chat_days))
  const save = useMutation({
    mutationFn: organizations.change,
    onSuccess: (account) => setSignedIn(queryClient, account),
  })
  const chosen = days === '' ? null : Number(days)
  const warning = deletes(organization, chosen)

  return (
    <div className="space-y-2">
      <div className="flex items-end gap-3">
        <SelectField label="Chats kept" className="min-w-0 flex-1" value={days} disabled={save.isPending} onChange={(event) => setDays(event.target.value)}>
          {chatDayChoices(organization).map((choice) => (
            <option key={choice.value} value={choice.value}>
              {choice.label}
            </option>
          ))}
        </SelectField>
        <Button onClick={() => save.mutate({ chat_days: chosen })} disabled={chosen === organization.chat_days || save.isPending}>
          Save
        </Button>
      </div>
      {warning && chosen !== organization.chat_days && <Notice tone="warn">{warning}</Notice>}
      {save.isError && <Notice tone="bad">{save.error.message}</Notice>}
    </div>
  )
}

// How its members sign in (backend/app/security.py): whether two-factor is required, which only someone who signs in
// with it may turn on, and how often they sign in again; and the way to its audit log. Owners and admins change them;
// the others read them.
function SecuritySection({ me }: { me: Me }) {
  const organization = me.organization
  const signIn = signInWords(organization.session_days ?? organization.max_session_days)
  if (!isWithin('admin', me.role)) {
    return (
      <Section title="Security" description="Chosen by its owners and admins for everyone in the organisation.">
        <dl className="divide-y divide-line border-t border-line">
          <Row label="Two-factor sign-in">{organization.mfa_required ? 'Required' : 'Up to each member'}</Row>
          <Row label="Sign in again">{signIn}</Row>
        </dl>
      </Section>
    )
  }

  return (
    <Section title="Security" description="How everyone in the organisation signs in, and what was done in it.">
      <div className="max-w-md space-y-4">
        <RequireTwoFactor me={me} />
        <SessionDays me={me} />
      </div>
      <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-line pt-4 text-[13px]">
        <span className="text-muted">Who signed in, changed, shared or deleted what, and when.</span>
        <LinkButton to="/organization/audit">
          <History className="size-4" />
          Audit log
        </LinkButton>
      </div>
    </Section>
  )
}

// Turning it on holds every member without two-factor until they set it up, so the page names them first; it cannot
// be turned on by someone who would be held themselves.
function RequireTwoFactor({ me }: { me: Me }) {
  const queryClient = useQueryClient()
  const organization = me.organization
  const [asking, setAsking] = useState(false)
  const members = useQuery({ queryKey: MEMBERS_KEY, queryFn: organizations.members })
  const save = useMutation({
    mutationFn: (on: boolean) => organizations.change({ mfa_required: on }),
    onSuccess: (account) => {
      setAsking(false)
      setSignedIn(queryClient, account)
    },
  })
  const held = members.data ? heldByTwoFactor(members.data) : null
  const checked = save.isPending ? save.variables : organization.mfa_required

  function toggle(on: boolean) {
    if (asking) setAsking(false)
    else if (on && held) setAsking(true)
    else save.mutate(on)
  }

  return (
    <div className="space-y-2">
      <Checkbox
        label={
          <>
            <span className="font-medium">Require two-factor sign-in</span>
            <span className="block text-muted">
              Everyone signs in with a code from an authenticator app as well, those who sign in through Google or
              Microsoft too.
            </span>
          </>
        }
        checked={checked || asking}
        disabled={save.isPending || members.isPending || (!organization.mfa_required && !me.mfa_enabled)}
        onChange={(event) => toggle(event.target.checked)}
      />
      {!organization.mfa_required && !me.mfa_enabled && (
        <p className="pl-6.5 text-[13px] text-muted">
          <Link to="/account" className="font-medium text-accent hover:underline">
            Turn it on for your own account
          </Link>{' '}
          first: once it is required, nobody works in the organisation without it.
        </p>
      )}
      {asking && held && (
        <div className="space-y-2 pl-6.5">
          <Notice tone="warn">{held}</Notice>
          <div className="flex gap-2">
            <Button variant="primary" onClick={() => save.mutate(true)} disabled={save.isPending}>
              Require it
            </Button>
            <Button variant="ghost" onClick={() => setAsking(false)}>
              Cancel
            </Button>
          </div>
        </div>
      )}
      {save.isError && <Notice tone="bad">{save.error.message}</Notice>}
    </div>
  )
}

// A shorter time signs out every member who signed in longer ago at their next request, so it is saved by a button.
function SessionDays({ me }: { me: Me }) {
  const queryClient = useQueryClient()
  const organization = me.organization
  const [days, setDays] = useState(organization.session_days === null ? '' : String(organization.session_days))
  const save = useMutation({
    mutationFn: organizations.change,
    onSuccess: (account) => setSignedIn(queryClient, account),
  })
  const chosen = days === '' ? null : Number(days)

  return (
    <div className="space-y-2">
      <div className="flex items-end gap-3">
        <SelectField
          label="Sign in again"
          className="min-w-0 flex-1"
          value={days}
          disabled={save.isPending}
          onChange={(event) => setDays(event.target.value)}
        >
          {sessionDayChoices(organization).map((choice) => (
            <option key={choice.value} value={choice.value}>
              {choice.label}
            </option>
          ))}
        </SelectField>
        <Button onClick={() => save.mutate({ session_days: chosen })} disabled={chosen === organization.session_days || save.isPending}>
          Save
        </Button>
      </div>
      <p className="text-xs text-muted">A member who signed in longer ago is asked to sign in again at their next step in the organisation.</p>
      {save.isError && <Notice tone="bad">{save.error.message}</Notice>}
    </div>
  )
}

// Owners and admins only. A key acts as the member who makes it, within their role and its scope, and reaches the
// projects alone (backend/app/apikeys.py). It is shown once, as it is made or rolled; the list shows its first
// characters after that. Only its own member rolls a key, since the new one acts as them; any owner or admin revokes.
function ApiKeysSection({ me }: { me: Me }) {
  const queryClient = useQueryClient()
  const keys = useQuery({ queryKey: KEYS_KEY, queryFn: organizations.keys })
  const [name, setName] = useState('')
  const [scope, setScope] = useState<ApiKeyScope>('read')
  const [days, setDays] = useState(KEY_DAYS[0].value)
  const [shown, setShown] = useState<ApiKeyMade | null>(null)
  const make = useMutation({
    mutationFn: () => organizations.makeKey({ name: name.trim(), scope, days: daysOf(days) }),
    onSuccess: (made) => {
      setName('')
      setShown(made)
      void queryClient.invalidateQueries({ queryKey: KEYS_KEY })
    },
  })

  function submit(event: FormEvent) {
    event.preventDefault()
    if (name.trim()) make.mutate()
  }

  return (
    <Section
      id="api-keys"
      title="API keys"
      description="A program calls Emudoc with a key as the member who made it: it reads what you may in the organisation's projects, and with the write scope changes it, but nothing of the accounts or the organisation itself."
    >
      {shown && <NewKey made={shown} onDone={() => setShown(null)} />}
      {me.organization.allowance.api_access ? (
        <form onSubmit={submit} className="space-y-3">
          <div className="flex flex-wrap items-end gap-2">
            <InputField
              label="Name"
              required
              maxLength={100}
              autoComplete="off"
              placeholder="Invoices sync"
              className="min-w-48 flex-1"
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
            <SelectField label="Runs for" value={days} onChange={(event) => setDays(event.target.value)}>
              {KEY_DAYS.map((choice) => (
                <option key={choice.value} value={choice.value}>
                  {choice.label}
                </option>
              ))}
            </SelectField>
          </div>
          <fieldset className="space-y-2">
            <legend className="sr-only">What it may do</legend>
            {(Object.keys(SCOPES) as ApiKeyScope[]).map((option) => (
              <Radio
                key={option}
                name="api-key-scope"
                label={SCOPES[option].label}
                hint={SCOPES[option].hint}
                checked={scope === option}
                onChange={() => setScope(option)}
              />
            ))}
          </fieldset>
          <Button type="submit" variant="primary" icon={<KeyRound className="size-4" />} disabled={!name.trim() || make.isPending}>
            Make a key
          </Button>
        </form>
      ) : (
        <Notice tone="info">
          <p className="text-ink">The organisation's plan does not include the API.</p>
          <PlanOffer limit="api_access" />
        </Notice>
      )}
      {make.isError && (
        <div className="mt-3">
          {isPlanLimit(make.error) ? (
            <PlanLimitNotice refusal={make.error} onDismiss={() => make.reset()} />
          ) : (
            <Notice tone="bad">{make.error.message}</Notice>
          )}
        </div>
      )}
      {keys.isPending && <Loading />}
      {keys.isError && <Notice tone="bad">{keys.error.message}</Notice>}
      {keys.data &&
        (keys.data.length ? (
          <ul className="mt-4 divide-y divide-line border-t border-line">
            {keys.data.map((apiKey) => (
              <ApiKeyRow key={apiKey.id} me={me} apiKey={apiKey} onRolled={setShown} />
            ))}
          </ul>
        ) : (
          me.organization.allowance.api_access && <p className="mt-3 text-[13px] text-muted">The organisation has no API keys yet.</p>
        ))}
    </Section>
  )
}

/** A key just made or rolled, which nothing shows again, with how a program sends it. */
function NewKey({ made, onDone }: { made: ApiKeyMade; onDone: () => void }) {
  const [copied, setCopied] = useState(false)

  async function copy() {
    try {
      await navigator.clipboard.writeText(made.key)
      setCopied(true)
    } catch {
      // The clipboard can be unavailable; the key is on screen to copy by hand.
    }
  }

  return (
    <div className="mb-4 space-y-3 text-[13px]">
      <Notice tone="warn">
        Copy the key “{made.name}” now: it is not shown again. Whoever holds it acts as you in the organisation's
        projects, so keep it where you keep passwords.
      </Notice>
      <code className="block rounded-md border border-line bg-sunken px-3 py-2 font-mono text-xs break-all select-all">{made.key}</code>
      <p className="text-muted">
        A program sends it in a header, as in{' '}
        <code className="font-mono text-xs break-all">{callWith(made.key, window.location.origin)}</code>
      </p>
      <div className="flex gap-2">
        <Button icon={copied ? <Check className="size-4" /> : <Copy className="size-4" />} onClick={copy}>
          {copied ? 'Copied' : 'Copy the key'}
        </Button>
        <Button variant="primary" onClick={onDone}>
          I have saved it
        </Button>
      </div>
    </div>
  )
}

// Rolling and revoking each stop the program that holds the key at once, so each asks first.
function ApiKeyRow({ me, apiKey, onRolled }: { me: Me; apiKey: ApiKey; onRolled: (made: ApiKeyMade) => void }) {
  const queryClient = useQueryClient()
  const [asking, setAsking] = useState<'roll' | 'revoke' | null>(null)
  const done = () => {
    setAsking(null)
    void queryClient.invalidateQueries({ queryKey: KEYS_KEY })
  }
  const roll = useMutation({
    mutationFn: () => organizations.rollKey(apiKey.id),
    onSuccess: (made) => {
      done()
      onRolled(made)
    },
  })
  const revoke = useMutation({ mutationFn: () => organizations.revokeKey(apiKey.id), onSuccess: done })
  const busy = roll.isPending || revoke.isPending
  const error = roll.error ?? revoke.error

  return (
    <li className="py-2.5 text-[13px]">
      {/* The key's line is what tells keys apart, so it wraps rather than being cut, the buttons below it on a phone. */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <div className="min-w-56 flex-1">
          <p className="font-medium break-words">{apiKey.name}</p>
          <p className={`text-xs ${apiKey.expired ? 'text-warn' : 'text-muted'}`}>{describeKey(apiKey)}</p>
        </div>
        <Chip>{SCOPES[apiKey.scope].label}</Chip>
        {mayRoll(apiKey, me) && (
          <Button variant="ghost" onClick={() => setAsking('roll')} disabled={busy}>
            Roll
          </Button>
        )}
        <Button variant="ghost" onClick={() => setAsking('revoke')} disabled={busy}>
          Revoke
        </Button>
      </div>
      {asking && (
        <div className="mt-2 space-y-2">
          <Notice tone="warn">
            {asking === 'roll'
              ? `Roll “${apiKey.name}”? You get a new key at once, and programs using this one stop until they are given it.`
              : `Revoke “${apiKey.name}”? Programs using it stop at once.`}
          </Notice>
          <div className="flex gap-2">
            <Button variant="primary" onClick={() => (asking === 'roll' ? roll.mutate() : revoke.mutate())} disabled={busy}>
              {asking === 'roll' ? 'Roll it' : 'Revoke it'}
            </Button>
            <Button variant="ghost" onClick={() => setAsking(null)}>
              Cancel
            </Button>
          </div>
        </div>
      )}
      {error && (
        <div className="mt-2">
          {isPlanLimit(error) ? <PlanLimitNotice refusal={error} /> : <Notice tone="bad">{error.message}</Notice>}
        </div>
      )}
    </li>
  )
}

// What the organisation may keep and use, as the server holds it to them
// (backend/app/entitlements.py): its plan's figures, the seats it bought on a plan sold by the
// seat, and any figure agreed with it in the plan's place; and how its subscription stands.
function PlanSection({ me }: { me: Me }) {
  const { plan, seats, allowance, chat_days: chosenDays, billing } = me.organization
  const figure = (value: number | null, words: (value: number) => string = (n) => n.toLocaleString()) =>
    value === null ? 'No limit' : words(value)
  const monthly = (n: number) => `${n.toLocaleString()} a month`
  const agreed = (name: keyof Allowance) =>
    allowance.agreed.includes(name) && (
      <span title="Agreed with the organisation in place of the plan's">
        <Chip>Agreed</Chip>
      </span>
    )
  const sells = (billing.sold.length > 0 || billing.packs.length > 0) && billing.managed
  const standing = describeBilling(billing, formatDate)
  const past = describeExcess(me.organization)
  const kept = describeHeld(me.organization, formatDate)
  const tones = { good: 'text-good', warn: 'text-warn', bad: 'text-bad' }
  const extra = (count: number) =>
    count > 0 && <span className="text-muted">and {count.toLocaleString()} more bought for {thisMonth()}</span>

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
          {extra(allowance.pages_bought)}
          {agreed('pages_per_month')}
        </Row>
        <Row label="Questions">
          {figure(allowance.questions_per_month, monthly)}
          {extra(allowance.questions_bought)}
          {agreed('questions_per_month')}
        </Row>
        <Row label="Members">
          {figure(allowance.members, (n) => (seats === null ? n.toLocaleString() : n === 1 ? '1 seat' : `${n.toLocaleString()} seats`))}
          {agreed('members')}
        </Row>
        <Row label="Chats kept">
          {keptWords(allowance.retention_days)}
          {agreed('retention_days')}
          {chosenDays !== null && <span className="block text-muted">The organisation keeps them {chosenDays.toLocaleString()} days, in its settings.</span>}
          {kept && <span className="block text-muted">{kept}</span>}
        </Row>
      </dl>
      <div className="mt-3">
        <Link to="/organization/usage" className="text-[13px] font-medium text-accent hover:underline">
          See what it holds and has used
        </Link>
      </div>
      {past && (
        <div className="mt-4">
          <Notice>{past}</Notice>
        </div>
      )}
      {sells && <BillingActions me={me} />}
    </Section>
  )
}

// Buying a plan or a pack on Stripe's Checkout page, managing them on Stripe's customer portal,
// and the way back from Checkout, whose session the server asks Stripe about
// (backend/app/api/billing.py). Both pages are Stripe's: the browser goes to the address the
// server gives.
function BillingActions({ me }: { me: Me }) {
  const queryClient = useQueryClient()
  const { billing, seats: bought } = me.organization
  const [params, setParams] = useSearchParams()
  const returned = params.get('checkout')
  const fromPortal = params.get('billing') === 'back'
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
  const buyPack = useMutation({ mutationFn: organizations.buyPack, onSuccess: away })
  const done = useMutation({
    mutationFn: organizations.checkoutDone,
    onSuccess: (account) => setSignedIn(queryClient, account),
  })
  const { mutate: finish } = done
  // Back from Stripe's portal, the server asks Stripe how the subscription stands rather than wait for its event.
  const portalDone = useMutation({
    mutationFn: organizations.portalDone,
    onSuccess: (account) => setSignedIn(queryClient, account),
  })
  const { mutate: settle } = portalDone

  useEffect(() => {
    if (returned) {
      finish(returned)
      setParams({}, { replace: true })
    }
  }, [returned, finish, setParams])

  useEffect(() => {
    if (fromPortal) {
      settle()
      setParams({}, { replace: true })
    }
  }, [fromPortal, settle, setParams])

  const owner = me.role === 'owner'
  // Stripe's page is loading once an address came back: nothing is offered twice meanwhile.
  const leaving = [buy, manage, buyPack].some((step) => step.isPending || step.isSuccess)
  const error = buy.error ?? manage.error ?? buyPack.error

  return (
    <div className="mt-4 space-y-3">
      {done.isPending && <Notice tone="info">Asking Stripe about the payment…</Notice>}
      {done.isSuccess && <Notice tone="info">Thank you: the payment went through, and the plan above shows what it bought.</Notice>}
      {done.isError && <Notice tone="bad">{done.error.message}</Notice>}
      {portalDone.isPending && <Notice tone="info">Asking Stripe how the subscription stands…</Notice>}
      {portalDone.isError && <Notice tone="bad">{portalDone.error.message}</Notice>}
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
          {billing.packs.length > 0 && (
            <div>
              <div className="flex flex-wrap items-center gap-3">
                {billing.packs.map((pack) => (
                  <Button key={`${pack.unit}:${pack.quantity}`} disabled={leaving} onClick={() => buyPack.mutate(pack)}>
                    Buy {pack.quantity.toLocaleString()} {pack.unit} for {thisMonth()}
                  </Button>
                ))}
              </div>
              <p className="mt-1.5 text-[13px] text-muted">
                A pack is paid once and adds to this month’s allowance, on top of the plan’s, until the month ends in UTC.
              </p>
            </div>
          )}
          {mayChange(billing) && <ChangePlan me={me} leaving={leaving} />}
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

// Moving the subscription to another plan, fewer or more seats, or cancelling it for Free: the
// server says first what that would leave read-only and keep for less time
// (`plan_change` in backend/app/billing.py), then Stripe's portal opens on that one step, whose
// page shows the price and confirms it, and sends the owner back here.
function ChangePlan({ me, leaving }: { me: Me; leaving: boolean }) {
  const { billing, plan: current, seats: bought } = me.organization
  const [plan, setPlan] = useState<PlanChoice['plan'] | null>(null)
  const [seats, setSeats] = useState(String(bought ?? 1))
  const seatCount = Number(seats)
  const choice: PlanChoice | null =
    plan === null || (plan === 'team' && !(seatCount >= 1)) ? null : plan === 'team' ? { plan, seats: seatCount } : { plan }
  const change = useQuery({
    queryKey: ['organization', 'planChange', choice],
    queryFn: () => organizations.planChange(choice!),
    enabled: choice !== null,
    retry: false,
  })
  const go = useMutation({
    mutationFn: () => organizations.billingPortal(choice!),
    onSuccess: ({ url }) => window.location.assign(url),
  })
  // Each plan on sale, Team again for other seats, and Free, which is cancelling.
  const choices = [...billing.sold.filter((sold) => sold !== current || sold === 'team'), 'free' as const]
  const account = change.data ? describeChange(change.data, me.organization, formatDate) : null

  return (
    <div className="space-y-3 rounded-lg border border-line p-4">
      <div>
        <p className="text-[13px] font-medium">Change or cancel the plan</p>
        <p className="mt-0.5 text-[13px] text-muted">See what it would mean here first; Stripe’s page then shows the price and confirms it.</p>
      </div>
      <div className="space-y-2" role="radiogroup" aria-label="Plan to move to">
        {choices.map((option) => (
          <Radio
            key={option}
            name="plan-change"
            label={option === 'free' ? 'Free: cancel the subscription' : option === current ? `${PLAN_LABELS[option]}, other seats` : PLAN_LABELS[option]}
            checked={plan === option}
            onChange={() => setPlan(option)}
          />
        ))}
      </div>
      {plan === 'team' && (
        <InputField label="Seats" type="number" min={1} max={10000} className="w-24" value={seats} onChange={(event) => setSeats(event.target.value)} />
      )}
      {change.isFetching && <p className="text-[13px] text-muted">Working out what it would mean…</p>}
      {change.isError && <Notice tone="bad">{change.error.message}</Notice>}
      {account && !change.isFetching && (
        <div className="space-y-2">
          {account.figures.length > 0 && (
            <dl className="divide-y divide-line border-y border-line text-[13px]">
              {account.figures.map((figure) => (
                <div key={figure.label} className="flex justify-between gap-4 py-1.5">
                  <dt className="text-muted">{figure.label}</dt>
                  <dd>
                    {figure.now} → <span className="font-medium">{figure.then}</span>
                  </dd>
                </div>
              ))}
            </dl>
          )}
          {account.notes.map((note) => (
            <p key={note} className="text-[13px]">
              {note}
            </p>
          ))}
          {go.error && <Notice tone="bad">{go.error.message}</Notice>}
          <Button variant={plan === 'free' ? 'danger' : 'primary'} disabled={leaving || go.isPending || go.isSuccess} onClick={() => go.mutate()}>
            {plan === 'free' ? 'Cancel on Stripe’s page' : 'Confirm on Stripe’s page'}
          </Button>
        </div>
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
  const remove = useMutation({
    mutationFn: () => organizations.removeMember(member.user_id),
    onSuccess: () => {
      void refresh()
      // What the organisation holds past its plan, which a member fewer may bring it within.
      void queryClient.invalidateQueries({ queryKey: ME_KEY })
    },
  })
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
            {member.mfa_enabled === false && <Chip>No two-factor</Chip>}
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
      id={INVITATIONS_SECTION}
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
            <PlanLimitNotice refusal={invite.error} onDismiss={() => invite.reset()} />
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
  const revoke = useMutation({
    mutationFn: () => organizations.revokeInvitation(invitation.id),
    onSuccess: () => {
      void refresh()
      // An open invitation holds a member's place, so the organisation may be within its plan now.
      void queryClient.invalidateQueries({ queryKey: ME_KEY })
    },
  })
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
          {isPlanLimit(error) ? <PlanLimitNotice refusal={error} /> : <Notice tone="bad">{error.message}</Notice>}
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
      id={JOINING_SECTION}
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

/**
 * An owner deletes the organisation, confirmed as handing it over is: it goes with everything in it once the grace
 * period is over, and until then any owner keeps it here. Anyone else reads when, and asks an owner.
 */
function DeleteSection({ me }: { me: Me }) {
  const queryClient = useQueryClient()
  const [confirming, setConfirming] = useState(false)
  const [confirmation, setConfirmation] = useState('')
  const name = me.organization.name
  const remove = useMutation({
    mutationFn: () => organizations.delete(me.has_password ? { password: confirmation } : { email: confirmation }),
    onSuccess: (account) => {
      setSignedIn(queryClient, account)
      setConfirming(false)
      setConfirmation('')
    },
  })
  const keep = useMutation({ mutationFn: organizations.keep, onSuccess: (account) => setSignedIn(queryClient, account) })
  const due = me.organization.deletion_due_at
  const owner = me.role === 'owner'

  function submit(event: FormEvent) {
    event.preventDefault()
    if (confirmation.trim()) remove.mutate()
  }

  return (
    <Section
      id={DELETION_SECTION}
      title="Delete the organisation"
      description={`Everything in it goes: its projects, documents and chats, its members' places in it, its usage and its audit log. It is deleted ${DELETION_GRACE_DAYS} days after an owner asks, every member is told by email, and until then any owner can keep it. A subscription that still renews is cancelled first, under Manage billing.`}
    >
      {due ? (
        <div className="space-y-3">
          <Notice tone="warn">{deletionWords(me.organization, me.role, formatDate(due))}</Notice>
          {keep.isError && <Notice tone="bad">{keep.error.message}</Notice>}
          {owner && (
            <Button variant="primary" onClick={() => keep.mutate()} disabled={keep.isPending}>
              Keep {name}
            </Button>
          )}
        </div>
      ) : confirming ? (
        <form onSubmit={submit} className="max-w-sm space-y-3">
          <Notice tone="warn">
            Delete {name}? Its members can read and download what it holds for {DELETION_GRACE_DAYS} days, and then it is gone for good.
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
          {remove.isError && <Notice tone="bad">{remove.error.message}</Notice>}
          <div className="flex gap-2">
            <Button type="submit" variant="danger" icon={<Trash2 className="size-4" />} disabled={!confirmation.trim() || remove.isPending}>
              Delete {name}
            </Button>
            <Button variant="ghost" onClick={() => setConfirming(false)}>
              Cancel
            </Button>
          </div>
        </form>
      ) : (
        <Button variant="danger" onClick={() => setConfirming(true)}>
          Delete…
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
