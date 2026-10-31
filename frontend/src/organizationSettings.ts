// The organisation's settings as its page offers them (backend/app/api/organizations.py): the
// languages its documents may be searched in, the times it may keep a chat within its plan, and
// what choosing a shorter one deletes; its security settings (backend/app/security.py), how
// often its members sign in again and whom requiring two-factor would hold; and what the pages
// say of it once an owner set it to be deleted (backend/app/retention.py).

import type { Languages, Member, Organization, Role } from './api.ts'

/** The configuration that stems nothing (WORDS_AS_WRITTEN in backend/app/languages.py). */
export const WORDS_AS_WRITTEN = 'simple'

/** The languages the organisation's documents may be searched in, which the organisation page and a document's details offer. */
export const LANGUAGES_KEY = ['organization', 'languages'] as const

/** The times the page offers to keep a chat after its last question, in days. */
export const CHAT_DAYS = [7, 30, 90, 180, 365, 730]

/** The times the page offers a session to work in the organisation after signing in, in days. */
export const SESSION_DAYS = [1, 7, 14, 30, 60, 90]

/** A text search configuration's name as the page says it: "german" is German. */
export function languageName(name: string): string {
  if (name === WORDS_AS_WRITTEN) return 'Any language, words as written'
  const spaced = name.replaceAll('_', ' ')
  return spaced[0].toUpperCase() + spaced.slice(1)
}

/** One choice of a select: its value, '' for the deployment's or the plan's, and its words. */
export interface Choice {
  value: string
  label: string
}

/** The languages offered, the deployment's first as the choice of none. */
export function languageChoices({ languages, default: deployment }: Languages): Choice[] {
  return [
    { value: '', label: `${languageName(deployment)}, as Emudoc searches by default` },
    ...languages.filter((name) => name !== deployment).map((name) => ({ value: name, label: languageName(name) })),
  ]
}

/** Which of `languageChoices` the organisation has: naming the deployment's is having none. */
export function chosenLanguage(organization: Organization, { languages, default: deployment }: Languages): string {
  const chosen = organization.language
  return chosen === null || chosen === deployment || !languages.includes(chosen) ? '' : chosen
}

/** How long a chat is kept, as a row says it. */
export function keptWords(days: number | null): string {
  return days === null ? 'Until deleted' : `${days.toLocaleString()} day${days === 1 ? '' : 's'} after the last question`
}

/** The times the organisation may keep a chat: as long as its plan does, then those shorter, with its own choice among them. */
export function chatDayChoices(organization: Organization): Choice[] {
  const plan = organization.allowance.retention_days
  const days = new Set(CHAT_DAYS.filter((n) => plan === null || n < plan))
  if (organization.chat_days !== null) days.add(organization.chat_days)
  return [
    { value: '', label: `As long as the plan keeps them: ${keptWords(plan).toLowerCase()}` },
    ...[...days].sort((a, b) => a - b).map((n) => ({ value: String(n), label: keptWords(n) })),
  ]
}

/** What choosing to keep a chat `days` days deletes, said before it is saved; null when it keeps everything kept now, a hold after a lower plan's included. */
export function deletes(organization: Organization, days: number | null): string | null {
  const plan = organization.chat_retention_days
  const held = organization.retention_held?.chat_days
  const now = held !== undefined && (held === null || (plan !== null && held > plan)) ? held : plan
  if (days === null || (now !== null && days >= now)) return null
  return `The chats whose last question is more than ${days.toLocaleString()} days old are deleted within six hours, for every member, and cannot be taken back.`
}

/** How often a member signs in again, as a row says it. */
export function signInWords(days: number): string {
  return days === 1 ? 'Every day' : `Every ${days.toLocaleString()} days`
}

/** The times a session may work in the organisation: as long as the deployment keeps one, then those shorter, with its own choice among them. */
export function sessionDayChoices(organization: Organization): Choice[] {
  const most = organization.max_session_days
  const days = new Set(SESSION_DAYS.filter((n) => n < most))
  if (organization.session_days !== null) days.add(organization.session_days)
  return [
    { value: '', label: `As often as Emudoc asks: ${signInWords(most).toLowerCase()}` },
    ...[...days].sort((a, b) => a - b).map((n) => ({ value: String(n), label: signInWords(n) })),
  ]
}

/** Who would be held by requiring two-factor sign-in, said before it is turned on: null when every member signs in with it. */
export function heldByTwoFactor(members: Member[]): string | null {
  const without = members.filter((member) => member.mfa_enabled === false).map((member) => member.name || member.email)
  if (without.length === 0) return null
  const names = without.length <= 3 ? without.join(', ') : `${without.slice(0, 3).join(', ')} and ${without.length - 3} more`
  const they = without.length === 1 ? 'They set it up' : 'Each sets it up'
  return `${without.length === 1 ? 'One member signs' : `${without.length} members sign`} in without it: ${names}. ${they} before working in the organisation again.`
}

/** The organisation page's section that deletes the organisation or keeps it, by its anchor. */
export const DELETION_SECTION = 'deletion'

/**
 * What the banner and the organisation page say of an organisation set to be deleted on `day`,
 * as the reader may act on it: an owner keeps it, anyone else asks one to; null for one that is not.
 */
export function deletionWords(organization: Organization, role: Role, day: string): string | null {
  if (!organization.deletion_due_at) return null
  const then = `${organization.name} is set to be deleted on ${day}, with everything in it. Until then nothing can be added to its projects or changed in them, and what it holds can still be read and downloaded`
  return role === 'owner' ? `${then}; an owner can keep it.` : `${then}; ask an owner to keep it if it should stay.`
}
