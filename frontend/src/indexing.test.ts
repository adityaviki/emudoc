import { describe, expect, it } from 'vitest'
import type { Document, Stage, StageName } from './api.ts'
import { formatDateTime } from './format.ts'
import { missingWords, notIndexedWords, retryWords, statusLabel, statusTitle, waitingRetry } from './indexing.ts'

const NOW = new Date('2026-10-23T12:00:00Z')
const inMinutes = (minutes: number) => new Date(NOW.getTime() + minutes * 60_000).toISOString()
const time = (at: string) => new Date(at).toLocaleTimeString(undefined, { timeStyle: 'short' })

const done: Stage = { status: 'done', detail: '', retry_at: null, deferred: false }
const BUSY = 'The AI model is unavailable just now. Retrying in 2 minutes (2 of 4).'

function doc(status: Document['status'], stages: Partial<Record<StageName, Partial<Stage>>>): Document {
  return {
    id: 1,
    project_id: 3,
    folder_id: null,
    filename: 'invoice.pdf',
    mime_type: 'application/pdf',
    size_bytes: 10,
    status,
    stages: Object.fromEntries(
      (['scan', 'parse', 'structure', 'enrich', 'embed'] as const).map((name) => [name, { ...done, ...stages[name] }]),
    ),
    error: null,
    page_count: 1,
    title: null,
    doc_type: null,
    doc_date: null,
    summary: null,
    usage: {},
    created_at: '2026-10-23T11:50:00Z',
  }
}

const failed = (retryAt: string | null, deferred = false): Partial<Stage> => ({ status: 'failed', detail: BUSY, retry_at: retryAt, deferred })

describe('a retry that waits', () => {
  it('is the failed stage that the server said when it runs again, on a queued or ready document alone', () => {
    const at = inMinutes(2)
    expect(waitingRetry(doc('queued', { parse: failed(at), structure: { status: 'pending' } }))).toEqual({
      stage: 'parse',
      at: new Date(at),
      deferred: false,
    })
    expect(waitingRetry(doc('ready', { enrich: failed(at, true), embed: { status: 'pending' } }))).toEqual({
      stage: 'enrich',
      at: new Date(at),
      deferred: true,
    })
    // From before the server said when, given up on, or taken by a worker meanwhile.
    expect(waitingRetry(doc('queued', { parse: failed(null) }))).toBeNull()
    expect(waitingRetry(doc('failed', { parse: { status: 'failed', detail: 'Tried 5 times.' } }))).toBeNull()
    expect(waitingRetry(doc('processing', { parse: failed(inMinutes(-1)) }))).toBeNull()
    expect(waitingRetry(doc('ready', {}))).toBeNull()
  })

  it('says until when, in the browser’s zone, and the day too when it is not today', () => {
    const soon = inMinutes(2)
    expect(retryWords({ stage: 'parse', at: new Date(soon), deferred: false }, NOW)).toBe(`retrying at ${time(soon)}`)
    const later = inMinutes(90)
    expect(retryWords({ stage: 'enrich', at: new Date(later), deferred: true }, NOW)).toBe(`paused until ${time(later)}`)
    const tomorrow = inMinutes(24 * 60)
    expect(retryWords({ stage: 'enrich', at: new Date(tomorrow), deferred: true }, NOW)).toBe(`paused until ${formatDateTime(tomorrow)}`)
    // Due, and not yet taken by a worker.
    expect(retryWords({ stage: 'parse', at: new Date(inMinutes(-1)), deferred: false }, NOW)).toBe('retrying')
    expect(retryWords({ stage: 'enrich', at: NOW, deferred: true }, NOW)).toBe('retrying')
  })

  it('is a queued document’s state, and the line under a ready one’s', () => {
    const at = inMinutes(2)
    const queued = doc('queued', { parse: failed(at), structure: { status: 'pending' }, enrich: { status: 'pending' }, embed: { status: 'pending' } })
    expect(statusLabel(queued, NOW)).toBe(`Retrying at ${time(at)}`)
    expect(statusLabel(doc('queued', { parse: failed(at, true) }), NOW)).toBe(`Paused until ${time(at)}`)
    expect(statusLabel(doc('queued', { parse: failed(null) }), NOW)).toBe('Queued')

    const ready = doc('ready', { enrich: failed(at), embed: { status: 'pending' } })
    expect(statusLabel(ready, NOW)).toBe('Ready')
    expect(missingWords(ready, NOW)).toBe(`Summaries and facts: retrying at ${time(at)}`)
    expect(missingWords(doc('ready', { enrich: { status: 'skipped' } }), NOW)).toBe('Without summaries and facts')
    // A stage left out for good is not one that comes later.
    expect(missingWords(doc('ready', { scan: { status: 'skipped' }, enrich: failed(at), embed: { status: 'pending' } }), NOW)).toBe(
      `Without malware scan. Summaries and facts: retrying at ${time(at)}`,
    )
    expect(missingWords(doc('ready', {}), NOW)).toBeNull()

    // In place of the text of a document not yet indexed.
    expect(notIndexedWords(queued, NOW)).toBe(
      `Indexing stopped for a reason that passes, and is retrying at ${time(at)}. Its text will appear here once it is indexed.`,
    )
    expect(notIndexedWords(doc('queued', { parse: failed(at, true) }), NOW)).toBe(
      `Indexing is paused until ${time(at)}, when the day's spending limit resets. Its text will appear here once it is indexed.`,
    )
    expect(notIndexedWords(doc('queued', { parse: failed(null) }), NOW)).toBe(
      'This document is being indexed. Its text will appear here in a moment.',
    )
  })

  it('is said beside its stage in the tooltip, with the stage’s own words', () => {
    const at = inMinutes(2)
    const title = statusTitle(doc('ready', { enrich: failed(at), embed: { status: 'pending' } }), NOW).split('\n')
    expect(title).toEqual([
      'Ready',
      'Malware scan: done',
      'Parse: done',
      'Outline: done',
      `Summaries and facts: failed, retrying at ${time(at)} (${BUSY})`,
      'Embeddings: pending',
    ])
    expect(statusTitle(doc('processing', { structure: { status: 'running' } }), NOW).split('\n')[0]).toBe('Indexing: outline')
  })
})
