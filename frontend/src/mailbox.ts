// A project's address as the library's "Email in" words it (backend/app/inbound.py,
// backend/app/api/mailbox.py): the senders' list as it is typed and kept, what became of each
// message, and what the view asks before it gives the project a new address or takes it away.

import type { InboundMessage } from './api.ts'
import type { Question } from './components/Confirm.tsx'
import { formatDateTime } from './format.ts'

export const mailboxKey = (projectId: number) => ['mailbox', projectId] as const

// A host name of labels joined by dots, the last of letters or an IDN's punycode, as the server
// takes a domain (DOMAIN in backend/app/invitations.py).
const DOMAIN = /^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+(?:[a-z]{2,63}|xn--[a-z0-9-]{1,59})$/

/** An entry of the senders' list as the server keeps it: lower-cased, an address or a bare domain. */
export function senderEntry(typed: string): string {
  const value = typed.trim().toLowerCase()
  return value.slice(1).includes('@') ? value : value.replace(/^@/, '')
}

/**
 * Why a typed entry cannot be on the list, or null when it can: an address, or a domain. The
 * server refuses a mail service's domain itself (gmail.com), saying so; a list holds an entry
 * once.
 */
export function senderProblem(typed: string, senders: string[]): string | null {
  const entry = senderEntry(typed)
  if (!entry) return 'Type an address such as ap@acme.com, or a domain such as acme.com.'
  if (entry.includes('@')) {
    const at = entry.lastIndexOf('@')
    if (at === 0 || /\s/.test(entry.slice(0, at)) || !DOMAIN.test(entry.slice(at + 1))) {
      return `${typed.trim()} is not an email address. Type one such as ap@acme.com.`
    }
  } else if (!DOMAIN.test(entry)) {
    return `${typed.trim()} is neither an address nor a domain. Type one such as ap@acme.com or acme.com.`
  }
  if (senders.includes(entry)) return `${entry} is on the list already.`
  return null
}

/** Whom the address takes mail from, in a sentence. */
export function sendersWords(senders: string[]): string {
  const members = 'the members who may change the project, from their own confirmed addresses'
  if (!senders.length) return `It takes mail from ${members}, and nobody else yet.`
  return `It takes mail from ${members}, and from ${senders.length === 1 ? 'the sender' : `the ${senders.length} senders`} below, an address or everyone at a domain.`
}

/** What became of a message, in a few words: "2 documents kept", "Turned away". */
export function outcomeWords(message: InboundMessage): string {
  if (message.outcome === 'refused') return 'Turned away'
  if (message.outcome === 'empty') return 'Nothing kept'
  return message.kept === 1 ? '1 document kept' : `${message.kept} documents kept`
}

/** Who sent a message and when: "Bob Smith (bob@acme.com), 24 Oct 2026, 10:02". */
export function sentWords(message: InboundMessage): string {
  const who = message.member ? `${message.member} (${message.sender})` : message.sender || 'An unreadable sender'
  return `${who}, ${formatDateTime(message.received_at)}`
}

export const NEW_ADDRESS: Question = {
  title: 'Give the project a new address?',
  body: 'Mail to the address it has now will reach nothing. Whoever sends to it needs the new one.',
  action: 'New address',
}

export const TAKE_AWAY: Question = {
  title: 'Take the project’s address away?',
  body: 'Mail to it will reach nothing, and the list of senders goes with it. What became of the messages it received stays here until it is forgotten.',
  action: 'Take it away',
}
