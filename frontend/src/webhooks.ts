// The organisation's webhooks as its page words them (backend/app/webhooks.py, backend/app/api/webhooks.py): what
// each event is, what is wrong with an address before it is sent, a webhook's line in the list, and a delivery's
// line in its log.

import type { Webhook, WebhookDelivery, WebhookEvent } from './api.ts'
import { formatDate, formatDateTime } from './format.ts'

/** What each event tells a program, in the order the app lists them, as the contract names them. */
export const WEBHOOK_EVENTS: Record<WebhookEvent, { label: string; hint: string }> = {
  'document.ready': {
    label: 'A document is indexed',
    hint: 'Once it can be searched and asked about, and again each time it is indexed again.',
  },
  'document.failed': {
    label: 'A document could not be indexed',
    hint: 'Once it could not be read, or the malware scan quarantined it.',
  },
}

export const EVENT_ORDER = Object.keys(WEBHOOK_EVENTS) as WebhookEvent[]

/** The events chosen, each once, in the app's order. */
export const chosenEvents = (events: Iterable<WebhookEvent>): WebhookEvent[] => {
  const chosen = new Set(events)
  return EVENT_ORDER.filter((event) => chosen.has(event))
}

/** What keeps an address from being a webhook's, in the server's words, before it is sent; null when nothing does
 * that the page can tell (the server holds a deployment's own network and plain HTTP to its own rules). */
export function webhookProblem(url: string): string | null {
  const address = url.trim()
  if (!address) return null
  if (!/^https?:\/\//i.test(address)) return 'Give the webhook an address that starts with https://.'
  let parsed: URL
  try {
    parsed = new URL(address)
  } catch {
    return 'Give the webhook a whole address, with the name of the server that takes the events.'
  }
  if (!parsed.hostname) return 'Give the webhook a whole address, with the name of the server that takes the events.'
  if (parsed.username || parsed.password) {
    return 'Leave the user name and password out of the webhook’s address: the signature on every event proves it is Emudoc’s.'
  }
  return null
}

/** A webhook's line under its address: who added it, and how its newest delivery went. */
export function describeWebhook(hook: Webhook): string {
  const parts = [hook.made_by ? `added by ${hook.made_by}` : `added ${formatDate(hook.created_at)}`]
  if (!hook.enabled) parts.push('paused')
  const last = hook.last_delivery
  if (!last) parts.push('nothing sent yet')
  else if (last.status === 'delivered' && last.delivered_at) parts.push(`last delivered ${formatDate(last.delivered_at)}`)
  else if (last.status === 'failed') parts.push('last delivery failed')
  else parts.push(last.attempts ? 'last delivery being tried again' : 'sending')
  return parts.join(' · ')
}

export type DeliveryTone = 'good' | 'warn' | 'bad'

/** A delivery's state in a word or two, with its tone, and the sentence of how its last try went. */
export function deliveryWords(delivery: WebhookDelivery): { state: string; tone: DeliveryTone; detail: string } {
  const tries = `${delivery.attempts} ${delivery.attempts === 1 ? 'try' : 'tries'}`
  if (delivery.status === 'delivered') {
    const answered = delivery.response_status ? `answered ${delivery.response_status}` : 'taken'
    return { state: 'Delivered', tone: 'good', detail: `${capitalised(answered)} after ${tries}.` }
  }
  if (delivery.status === 'failed') return { state: 'Not delivered', tone: 'bad', detail: delivery.error ?? '' }
  if (!delivery.attempts) return { state: 'Sending', tone: 'warn', detail: 'Waiting for its first try.' }
  const next = delivery.retry_at ? ` Next try ${formatDateTime(delivery.retry_at)}.` : ''
  return { state: 'Trying again', tone: 'warn', detail: `${delivery.error ?? ''}${next}`.trim() }
}

/** What the event was about: the document, by its id, as the API names it. */
export function subjectWords(delivery: WebhookDelivery): string {
  return `document ${delivery.subject_id}`
}

const capitalised = (text: string) => text[0].toUpperCase() + text.slice(1)
