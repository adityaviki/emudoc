// How a document's indexing state is put into words (components/IndexingStatus.tsx draws it).

import { STAGES } from './api.ts'
import type { Document, Stage, StageName } from './api.ts'
import { formatDateTime } from './format.ts'

export const STAGE_LABELS: Record<StageName, string> = {
  scan: 'Malware scan',
  parse: 'Parse',
  structure: 'Outline',
  enrich: 'Summaries and facts',
  embed: 'Embeddings',
}

export const STATUS_LABELS: Record<Document['status'], string> = {
  queued: 'Queued',
  processing: 'Indexing',
  ready: 'Ready',
  failed: 'Failed',
  quarantined: 'Quarantined',
}

export const stageOf = (document: Document, name: StageName): Stage =>
  document.stages[name] ?? { status: 'pending', detail: '', retry_at: null, deferred: false }

/**
 * Whether the file may be opened: its malware scan found nothing, or there was no scanner to
 * run it. The API refuses the file otherwise, so the links to it wait for this.
 */
export const scanPassed = (document: Document): boolean => ['done', 'skipped'].includes(stageOf(document, 'scan').status)

/** The optional stages a ready document went without. */
export function missingStages(document: Document): StageName[] {
  return STAGES.filter((name) => ['failed', 'skipped'].includes(stageOf(document, name).status))
}

/** A failed stage that waits to run again by itself, and until when (`_retry_index` in backend/app/worker.py). */
export interface Retry {
  stage: StageName
  at: Date
  /** For the day's spending limit to reset, rather than after a backoff. */
  deferred: boolean
}

/**
 * The retry a queued or ready document waits for: the stage that failed for a reason that
 * passes, which the worker runs again at `at`. Null while nothing waits, and for a wait the
 * server did not time (from before it said when), which reads as it did: queued, or ready
 * without the stage.
 */
export function waitingRetry(document: Document): Retry | null {
  if (document.status !== 'queued' && document.status !== 'ready') return null
  for (const name of STAGES) {
    const stage = stageOf(document, name)
    if (stage.status === 'failed' && stage.retry_at) return { stage: name, at: new Date(stage.retry_at), deferred: stage.deferred }
  }
  return null
}

/** A moment in the browser's own zone: its time on the same day, else its day and time. */
function momentWords(at: Date, now: Date): string {
  return at.toDateString() === now.toDateString()
    ? at.toLocaleTimeString(undefined, { timeStyle: 'short' })
    : formatDateTime(at.toISOString())
}

/**
 * The wait in a few words: "retrying at 14:32", or "paused until 00:00" for the day's spending
 * limit; once the moment has come and until a worker takes the document, "retrying".
 */
export function retryWords(retry: Retry, now: Date): string {
  if (retry.at <= now) return 'retrying'
  return retry.deferred ? `paused until ${momentWords(retry.at, now)}` : `retrying at ${momentWords(retry.at, now)}`
}

const capitalised = (words: string) => words.charAt(0).toUpperCase() + words.slice(1)

/**
 * The document's state in a few words: "Ready", "Indexing: outline", "Failed", and for a
 * document whose required stage waits for a retry, "Retrying at 14:32" in place of "Queued".
 */
export function statusLabel(document: Document, now = new Date()): string {
  const running = STAGES.find((name) => stageOf(document, name).status === 'running')
  if (document.status === 'processing' && running) return `Indexing: ${STAGE_LABELS[running].toLowerCase()}`
  const retry = document.status === 'queued' ? waitingRetry(document) : null
  if (retry) return capitalised(retryWords(retry, now))
  return STATUS_LABELS[document.status]
}

/**
 * The line under a ready document's state: the stages it went without, and when the one that
 * waits runs again ("Without malware scan. Summaries and facts: retrying at 14:32"); null with
 * every stage done.
 */
export function missingWords(document: Document, now = new Date()): string | null {
  const retry = waitingRetry(document)
  const without = missingStages(document).filter((name) => name !== retry?.stage)
  const words = [
    without.length ? `Without ${without.map((name) => STAGE_LABELS[name].toLowerCase()).join(', ')}` : '',
    retry ? `${STAGE_LABELS[retry.stage]}: ${retryWords(retry, now)}` : '',
  ]
  return words.filter(Boolean).join('. ') || null
}

/** What a document's tab says in place of its text until it is indexed. */
export function notIndexedWords(document: Document, now = new Date()): string {
  const retry = waitingRetry(document)
  const after = 'Its text will appear here once it is indexed.'
  if (!retry) return 'This document is being indexed. Its text will appear here in a moment.'
  if (retry.deferred && retry.at > now) return `Indexing is ${retryWords(retry, now)}, when the day's spending limit resets. ${after}`
  return `Indexing stopped for a reason that passes, and is ${retryWords(retry, now)}. ${after}`
}

/** The state and every stage, one per line, for a tooltip; a stage that waits says until when. */
export function statusTitle(document: Document, now = new Date()): string {
  const retry = waitingRetry(document)
  const stages = STAGES.map((name) => {
    const stage = stageOf(document, name)
    const status = retry?.stage === name ? `${stage.status}, ${retryWords(retry, now)}` : stage.status
    return `${STAGE_LABELS[name]}: ${status}${stage.detail ? ` (${stage.detail})` : ''}`
  })
  return [statusLabel(document, now), ...stages].join('\n')
}
