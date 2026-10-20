// The organisation's settings as its page offers them (backend/app/api/organizations.py): the
// languages its documents may be searched in, the times it may keep a chat within its plan, and
// what choosing a shorter one deletes.

import type { Languages, Organization } from './api.ts'

/** The configuration that stems nothing (WORDS_AS_WRITTEN in backend/app/languages.py). */
const WORDS_AS_WRITTEN = 'simple'

/** The times the page offers to keep a chat after its last question, in days. */
export const CHAT_DAYS = [7, 30, 90, 180, 365, 730]

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
