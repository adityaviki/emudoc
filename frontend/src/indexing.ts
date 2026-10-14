// How a document's indexing state is put into words (components/IndexingStatus.tsx draws it).

import { STAGES } from './api.ts'
import type { Document, Stage, StageName } from './api.ts'

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

export const stageOf = (document: Document, name: StageName): Stage => document.stages[name] ?? { status: 'pending', detail: '' }

/**
 * Whether the file may be opened: its malware scan found nothing, or there was no scanner to
 * run it. The API refuses the file otherwise, so the links to it wait for this.
 */
export const scanPassed = (document: Document): boolean => ['done', 'skipped'].includes(stageOf(document, 'scan').status)

/** The optional stages a ready document went without. */
export function missingStages(document: Document): StageName[] {
  return STAGES.filter((name) => ['failed', 'skipped'].includes(stageOf(document, name).status))
}

/** The document's state in a few words: "Ready", "Indexing: outline", "Failed". */
export function statusLabel(document: Document): string {
  const running = STAGES.find((name) => stageOf(document, name).status === 'running')
  if (document.status === 'processing' && running) return `Indexing: ${STAGE_LABELS[running].toLowerCase()}`
  return STATUS_LABELS[document.status]
}

/** The state and every stage, one per line, for a tooltip. */
export function statusTitle(document: Document): string {
  const stages = STAGES.map((name) => {
    const stage = stageOf(document, name)
    return `${STAGE_LABELS[name]}: ${stage.status}${stage.detail ? ` (${stage.detail})` : ''}`
  })
  return [statusLabel(document), ...stages].join('\n')
}
