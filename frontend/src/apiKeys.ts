// The organisation's API keys as its page words them (backend/app/apikeys.py, backend/app/api/apikeys.py): how long a
// new key may run, what each scope lets it do, a key's line in the list, and how a program calls the API with one.

import type { ApiKey, ApiKeyScope, Me } from './api.ts'
import { formatDate } from './format.ts'
import type { Choice } from './organizationSettings.ts'

/** The times the page offers a new key to run, in days, the first chosen; '' runs until it is revoked. */
export const KEY_DAYS: Choice[] = [
  { value: '90', label: '90 days' },
  { value: '30', label: '30 days' },
  { value: '365', label: 'A year' },
  { value: '', label: 'Until it is revoked' },
]

/** What each scope lets a key do, as the server holds it to: the methods that change nothing, or any its member's role allows. */
export const SCOPES: Record<ApiKeyScope, { label: string; hint: string }> = {
  read: { label: 'Read', hint: 'Lists, reads, searches and downloads, and follows answers. Changes nothing.' },
  write: { label: 'Read and write', hint: 'Also uploads, organises, deletes and asks, as far as your role allows.' },
}

/** The days a choice of KEY_DAYS asks for, null for none. */
export const daysOf = (value: string): number | null => (value === '' ? null : Number(value))

/** A key's line under its name: the start of it, whom it acts as, when it was last used and when it runs out. */
export function describeKey(key: ApiKey): string {
  const parts = [`${key.hint}…`, `acts as ${key.made_by}`]
  parts.push(key.last_used_at ? `last used ${formatDate(key.last_used_at)}` : 'never used')
  if (key.expires_at) parts.push(`${key.expired ? 'ran out on' : 'runs until'} ${formatDate(key.expires_at)}`)
  return parts.join(' · ')
}

/** Whether the member may roll the key: only the one it acts as, since whoever holds the new secret acts as them. */
export const mayRoll = (key: ApiKey, me: Me) => key.user_id === me.id

/** How a program calls the API with the key, from this deployment's address: the projects it may read. */
export function callWith(key: string, origin: string): string {
  return `curl -H "Authorization: Bearer ${key}" ${origin}/api/projects`
}
