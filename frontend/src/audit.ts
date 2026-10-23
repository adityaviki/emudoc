// The audit log as its page words it (backend/app/audit.py, backend/app/api/audit.py): each action, filed under the
// kind of thing it was done to as the filter offers them; a row's detail in a few words; and the filter the page's
// choices ask for, its days in the browser's own zone.

import { PLAN_LABELS, ROLE_LABELS } from './api.ts'
import type { AuditAction, AuditEvent, AuditFilter, NoticeKind, Plan, Role } from './api.ts'
import { fieldWords } from './factTable.ts'
import { formatDate, formatDateTime } from './format.ts'
import { NOTICE_KINDS } from './notifications.ts'
import { keptWords, languageName, signInWords } from './organizationSettings.ts'

/** The kinds the filter groups the actions under, in its order, as it names them. */
export const KINDS = {
  sign_ins: 'Signing in',
  account: 'Accounts',
  members: 'Members and access',
  organization: 'The organisation',
  billing: 'Plan and billing',
  files: 'Projects and documents',
  chats: 'Chats',
} as const

export type AuditKind = keyof typeof KINDS

/** What each action is called and the kind it is filed under: every action of AUDIT_ACTIONS, which the type holds to. */
export const ACTIONS: Record<AuditAction, [label: string, kind: AuditKind]> = {
  sign_up: ['Signed up', 'sign_ins'],
  sign_in: ['Signed in', 'sign_ins'],
  sign_in_failed: ['Sign-in failed', 'sign_ins'],
  sign_in_paused: ['Sign-ins paused', 'sign_ins'],
  sign_out: ['Signed out', 'sign_ins'],
  session_revoked: ['Signed out a session', 'sign_ins'],
  other_sessions_revoked: ['Signed out everywhere else', 'sign_ins'],
  password_changed: ['Changed their password', 'account'],
  password_reset_requested: ['Asked to reset their password', 'account'],
  password_reset: ['Reset their password', 'account'],
  profile_changed: ['Changed their profile', 'account'],
  email_change_requested: ['Asked to change their address', 'account'],
  email_changed: ['Changed their address', 'account'],
  mfa_enabled: ['Turned on two-factor sign-in', 'account'],
  mfa_disabled: ['Turned off two-factor sign-in', 'account'],
  account_exported: ['Downloaded their data', 'account'],
  terms_accepted: ['Accepted the terms', 'account'],
  account_deletion_requested: ['Asked to delete their account', 'account'],
  account_deletion_cancelled: ['Kept their account', 'account'],
  account_purged: ['Account deleted', 'account'],
  support_access_granted: ['Let support see their account', 'account'],
  support_access_revoked: ["Withdrew support's access", 'account'],
  support_session_started: ['Support opened an account', 'sign_ins'],
  support_session_ended: ['Support closed an account', 'sign_ins'],
  role_changed: ['Changed a role', 'members'],
  ownership_transferred: ['Handed over the organisation', 'members'],
  invitation_sent: ['Sent an invitation', 'members'],
  invitation_revoked: ['Revoked an invitation', 'members'],
  invitation_accepted: ['Accepted an invitation', 'members'],
  joining_changed: ['Changed who may join', 'members'],
  member_joined: ['Joined by their address', 'members'],
  member_left: ['Left the organisation', 'members'],
  member_removed: ['Removed a member', 'members'],
  project_access_changed: ['Changed who sees a project', 'members'],
  organization_created: ['Started the organisation', 'organization'],
  organization_renamed: ['Renamed the organisation', 'organization'],
  organization_deletion_requested: ['Asked to delete the organisation', 'organization'],
  organization_deletion_cancelled: ['Kept the organisation', 'organization'],
  settings_changed: ['Changed a setting', 'organization'],
  flag_changed: ['Feature flag changed', 'organization'],
  audit_exported: ['Downloaded the audit log', 'organization'],
  api_key_created: ['Made an API key', 'organization'],
  api_key_rolled: ['Rolled an API key', 'organization'],
  api_key_revoked: ['Revoked an API key', 'organization'],
  sample_added: ['Sample project added', 'organization'],
  database_restored: ['Database restored', 'organization'],
  plan_changed: ['Plan changed', 'billing'],
  seats_changed: ['Seats changed', 'billing'],
  entitlements_changed: ['Agreed limit changed', 'billing'],
  checkout_started: ['Started a checkout', 'billing'],
  subscription_changed: ['Subscription changed', 'billing'],
  subscription_cancelled: ['Subscription cancelled', 'billing'],
  cancellation_withdrawn: ['Cancellation withdrawn', 'billing'],
  payment_failed: ['Payment failed', 'billing'],
  pack_bought: ['Bought a pack', 'billing'],
  project_exported: ['Exported a project', 'files'],
  template_created: ['Made an extraction template', 'files'],
  template_changed: ['Changed an extraction template', 'files'],
  template_deleted: ['Deleted an extraction template', 'files'],
  fact_checked: ['Checked a fact', 'files'],
  fact_corrected: ['Corrected a fact', 'files'],
  fact_check_removed: ["Took back a fact's check", 'files'],
  facts_exported: ['Downloaded a facts table', 'files'],
  project_deleted: ['Deleted a project', 'files'],
  project_restored: ['Restored a project', 'files'],
  project_purged: ['Deleted a project for good', 'files'],
  folder_deleted: ['Deleted a folder', 'files'],
  folder_restored: ['Restored a folder', 'files'],
  folder_purged: ['Deleted a folder for good', 'files'],
  document_uploaded: ['Uploaded a document', 'files'],
  document_downloaded: ['Downloaded a document', 'files'],
  document_deleted: ['Deleted a document', 'files'],
  document_restored: ['Restored a document', 'files'],
  document_purged: ['Deleted a document for good', 'files'],
  document_quarantined: ['Document quarantined', 'files'],
  document_requeued: ['Document queued again', 'files'],
  chat_shared: ['Shared a chat', 'chats'],
  chat_unshared: ['Made a chat private', 'chats'],
  chat_deleted: ['Deleted a chat', 'chats'],
  chat_restored: ['Restored a chat', 'chats'],
  chat_purged: ['Deleted a chat for good', 'chats'],
}

/** What a row's action is called: by ACTIONS, or for one a page loaded before a release does not know, by its name. */
export const actionLabel = (action: AuditAction) => ACTIONS[action]?.[0] ?? action.replaceAll('_', ' ')

/** The actions filed under a kind, in the order above. */
export const actionsOf = (kind: AuditKind) => (Object.keys(ACTIONS) as AuditAction[]).filter((action) => ACTIONS[action][1] === kind)

/** What the page's choices are: a kind or an action ('' for any, `kind:files` for a kind), a member's id and a project's ('' for any), and the first and last day ('YYYY-MM-DD', '' for none). */
export interface AuditChoices {
  what: string
  who: string
  project: string
  from: string
  to: string
}

export const NO_CHOICES: AuditChoices = { what: '', who: '', project: '', from: '', to: '' }

/** The filter the choices ask for: the days run from the first's midnight in the browser's zone until the midnight after the last. */
export function filterOf({ what, who, project, from, to }: AuditChoices): AuditFilter {
  const kind = what.startsWith('kind:') ? (what.slice(5) as AuditKind) : null
  return {
    actions: kind ? actionsOf(kind) : what ? [what as AuditAction] : [],
    userId: who ? Number(who) : null,
    projectId: project ? Number(project) : null,
    since: from ? midnight(from) : null,
    until: to ? midnight(to, 1) : null,
  }
}

/** The midnight that starts the day, or one `later` days after, in the browser's zone, as an ISO time. */
function midnight(day: string, later = 0): string {
  const [year, month, date] = day.split('-').map(Number)
  return new Date(year, month - 1, date + later).toISOString()
}

/** Who did it, as a row says: the member as their account is now, the app or the operator, or a member whose account is gone. */
export function whoDid(event: AuditEvent): string {
  if (event.user_id === null) return 'Emudoc'
  return event.user_name || event.user_email || 'A former member'
}

/**
 * A row's detail in a few words: the name of the file, folder or project it was about, the member it was done to (by
 * `names`, the members' names by id), and what changed. Never more than the row holds, which is names and settings,
 * never what a document or a question says.
 */
export function describeDetail(event: AuditEvent, names: ReadonlyMap<number, string> = new Map()): string {
  const detail = event.detail
  const parts: string[] = []
  const name = detail.filename ?? detail.name
  // A project's own rows name it in their detail, as the row's project does already.
  if (typeof name === 'string' && name !== event.project_name) parts.push(name)
  if (event.target_type === 'user' && event.target_id !== null) parts.push(names.get(event.target_id) ?? 'a former member')
  if (typeof detail.entitlement === 'string') parts.push(detail.entitlement.replaceAll('_', ' '))
  // A fact checked or corrected: its field, never what the document says.
  if (typeof detail.field === 'string') parts.push(fieldWords(detail.field))
  if (event.action === 'settings_changed') parts.push(describeSetting(detail))
  else if (event.action === 'flag_changed') parts.push(describeFlag(detail))
  else if (TEMPLATE_ACTIONS.has(event.action)) parts.push(describeTemplate(event.action, detail))
  else if (event.action === 'facts_exported') parts.push(describeFactsExport(detail))
  else if ('to' in detail) parts.push(`${word(detail.from)} → ${word(detail.to)}`)
  else if ('from' in detail) parts.push(`was ${word(detail.from)}`)
  if (typeof detail.role === 'string' && !('to' in detail)) parts.push(word(detail.role))
  if (typeof detail.plan === 'string') parts.push(word(detail.plan))
  const unit = detail.unit ?? detail.pack
  if (typeof detail.quantity === 'number') parts.push(`${detail.quantity.toLocaleString()} ${unit ?? ''}`.trim())
  if (typeof detail.seats === 'number') parts.push(`${detail.seats} seat${detail.seats === 1 ? '' : 's'}`)
  if (typeof detail.domains === 'string') parts.push(detail.domains || 'no domains')
  // An account's or the organisation's deletion asked for, and the day it was set for.
  if (typeof detail.due_at === 'string') parts.push(`for ${formatDate(detail.due_at)}`)
  if (typeof detail.method === 'string') parts.push(detail.method === 'password' ? 'with a password' : `through ${word(detail.method)}`)
  if (detail.second_factor === 'recovery_code') parts.push('with a recovery code')
  if (detail.reason === 'password' || detail.reason === 'code') parts.push(`wrong ${detail.reason}`)
  if (detail.scope === 'read' || detail.scope === 'write') parts.push(detail.scope === 'write' ? 'reads and writes' : 'reads only')
  if (event.action === 'api_key_created') parts.push(typeof detail.days === 'number' ? `for ${detail.days} days` : 'until revoked')
  // A member's leave for support, and a view of their account, until when.
  if (event.action === 'support_access_granted' && typeof detail.until === 'string') parts.push(`until ${formatDateTime(detail.until)}`)
  if (event.action === 'support_session_started' && typeof detail.ends_at === 'string') parts.push(`until ${formatDateTime(detail.ends_at)}`)
  // What a member did through an API key is theirs, with the key named as it was.
  const key = detail.api_key
  if (key && typeof key === 'object' && 'name' in key) parts.push(`with the API key ${key.name}`)
  // What one of the deployment's operators did from the operator's area, by the id the deployment's records keep.
  if (typeof detail.operator === 'number') parts.push("by Emudoc's operators")
  // What was done while Emudoc's support viewed the member's account with their leave.
  if (typeof detail.support === 'number') parts.push("by Emudoc's support, viewing their account")
  return parts.join(' · ')
}

function describeSetting(detail: AuditEvent['detail']): string {
  const { setting, from, to } = detail
  if (setting === 'language') return `Language: ${language(from)} → ${language(to)}`
  if (setting === 'chat_days') return `Chats kept: ${days(from)} → ${days(to)}`
  if (setting === 'session_days') return `Sign in again: ${typeof to === 'number' ? signInWords(to).toLowerCase() : 'as Emudoc asks'}`
  if (setting === 'mfa_required') return to ? 'Two-factor sign-in required' : 'Two-factor sign-in no longer required'
  if (setting === 'notices') {
    const kind = NOTICE_KINDS[detail.kind as NoticeKind]?.label ?? String(detail.kind)
    return `${kind}, ${detail.way === 'email' ? 'by email' : 'in the app'}: ${to ? 'on' : 'off'}`
  }
  return `${String(setting)}: ${word(from)} → ${word(to)}`
}

/** A feature flag turned on or off for the organisation whatever its rollout, or back to it, and whether it is on now. */
function describeFlag(detail: AuditEvent['detail']): string {
  const flag = String(detail.flag).replaceAll('_', ' ')
  const now = detail.on ? 'on' : 'off'
  return detail.to === null ? `${flag}: as Emudoc rolls it out, ${now}` : `${flag}: ${now}`
}

const TEMPLATE_ACTIONS = new Set<AuditAction>(['template_created', 'template_changed', 'template_deleted'])

/** A project's extraction template made, changed or deleted: the kind of document it is for, and its fields or what became of them, by their names. */
function describeTemplate(action: AuditAction, detail: AuditEvent['detail']): string {
  const kind = typeof detail.from === 'string' ? `${detail.from} → ${String(detail.to)}` : String(detail.doc_type)
  const parts = [`for ${kind} documents`]
  if (action === 'template_created' && Array.isArray(detail.fields)) {
    parts.push(`${detail.fields.length} field${detail.fields.length === 1 ? '' : 's'}`)
  }
  for (const change of ['added', 'removed', 'changed'] as const) {
    const names = detail[change]
    if (Array.isArray(names) && names.length > 0) parts.push(`${change} ${names.join(', ')}`)
  }
  return parts.join(', ')
}

/** A facts table downloaded: its kind, the file, and how its rows were sorted and kept, by the fields' names, never a condition's value or the words. */
function describeFactsExport(detail: AuditEvent['detail']): string {
  const parts = [`${String(detail.kind)} facts as ${detail.format === 'xlsx' ? 'an Excel workbook' : 'a CSV file'}`]
  if (typeof detail.sort === 'string') parts.push(`sorted by ${fieldWords(detail.sort)}${detail.order === 'desc' ? ', descending' : ''}`)
  if (Array.isArray(detail.conditions) && detail.conditions.length > 0) {
    const fields = [...new Set(detail.conditions.map((condition) => fieldWords(String(condition).split(':')[0])))]
    parts.push(`kept by ${fields.join(' and ')}`)
  }
  if (detail.words === true) parts.push('rows holding the words looked for')
  if (detail.review === true) parts.push('those to review')
  return parts.join(', ')
}

const language = (value: unknown) => (typeof value === 'string' ? languageName(value) : 'the default')
const days = (value: unknown) => (typeof value === 'number' ? keptWords(value).toLowerCase() : 'as long as the plan')

/** A value of a detail as a row says it: a role or a plan by its name, nothing as none. */
function word(value: unknown): string {
  if (value === null || value === undefined || value === '') return 'none'
  if (typeof value === 'boolean') return value ? 'on' : 'off'
  if (typeof value === 'number') return value.toLocaleString()
  const text = String(value)
  return ROLE_LABELS[text as Role] ?? PLAN_LABELS[text as Plan] ?? text[0].toUpperCase() + text.slice(1)
}
