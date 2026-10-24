import { describe, expect, it } from 'vitest'
import type { Webhook, WebhookDelivery } from './api.ts'
import { formatDate, formatDateTime } from './format.ts'
import { EVENT_ORDER, chosenEvents, deliveryWords, describeWebhook, subjectWords, webhookProblem } from './webhooks.ts'

const delivery: WebhookDelivery = {
  id: 9,
  event_id: 'evt_0c1d',
  event_type: 'document.ready',
  subject_id: 41,
  status: 'delivered',
  attempts: 1,
  response_status: 204,
  error: null,
  created_at: '2026-10-24T09:00:00Z',
  attempted_at: '2026-10-24T09:00:01Z',
  retry_at: null,
  delivered_at: '2026-10-24T09:00:01Z',
}

const hook: Webhook = {
  id: 3,
  url: 'https://hooks.example.com/emudoc',
  events: ['document.ready'],
  enabled: true,
  made_by: 'Ada',
  created_at: '2026-10-20T09:00:00Z',
  rolled_at: null,
  last_delivery: null,
}

describe('webhooks', () => {
  it('lists the events chosen once each, in the order the app does', () => {
    expect(EVENT_ORDER).toEqual(['document.ready', 'document.failed', 'check_run.completed'])
    expect(chosenEvents(['check_run.completed', 'document.ready', 'document.ready'])).toEqual([
      'document.ready',
      'check_run.completed',
    ])
  })

  it('says what is wrong with an address in the server’s words, before it is sent', () => {
    expect(webhookProblem('')).toBeNull()
    expect(webhookProblem(' https://hooks.example.com/in?token=1 ')).toBeNull()
    expect(webhookProblem('hooks.example.com/in')).toBe('Give the webhook an address that starts with https://.')
    expect(webhookProblem('ftp://hooks.example.com')).toBe('Give the webhook an address that starts with https://.')
    expect(webhookProblem('https://')).toContain('a whole address')
    expect(webhookProblem('https://ada:secret@hooks.example.com/in')).toContain('Leave the user name and password out')
  })

  it('says who added a webhook and how its newest delivery went', () => {
    expect(describeWebhook(hook)).toBe('added by Ada · nothing sent yet')
    expect(describeWebhook({ ...hook, last_delivery: delivery })).toBe(`added by Ada · last delivered ${formatDate(delivery.delivered_at)}`)
    const failed = { ...delivery, status: 'failed' as const, delivered_at: null }
    expect(describeWebhook({ ...hook, enabled: false, made_by: null, last_delivery: failed })).toBe(
      `added ${formatDate(hook.created_at)} · paused · last delivery failed`,
    )
  })

  it('words a delivery by how its last try went', () => {
    expect(deliveryWords(delivery)).toEqual({ state: 'Delivered', tone: 'good', detail: 'Answered 204 after 1 try.' })
    const waiting = {
      ...delivery,
      status: 'pending' as const,
      attempts: 2,
      response_status: 500,
      error: 'The address answered 500, not 2xx. It is tried again later.',
      retry_at: '2026-10-24T09:03:00Z',
      delivered_at: null,
    }
    expect(deliveryWords(waiting)).toEqual({
      state: 'Trying again',
      tone: 'warn',
      detail: `The address answered 500, not 2xx. It is tried again later. Next try ${formatDateTime(waiting.retry_at)}.`,
    })
    expect(deliveryWords({ ...waiting, attempts: 0, error: null }).state).toBe('Sending')
    const given = { ...waiting, status: 'failed' as const, retry_at: null, error: 'The address answered 500, not 2xx. That was the last try.' }
    expect(deliveryWords(given)).toEqual({ state: 'Not delivered', tone: 'bad', detail: given.error })
  })

  it('names what an event was about as the API does', () => {
    expect(subjectWords(delivery)).toBe('document 41')
    expect(subjectWords({ ...delivery, event_type: 'check_run.completed', subject_id: 7 })).toBe('run 7')
  })
})
