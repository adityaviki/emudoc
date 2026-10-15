// What a document or an answer used, put into words (DocumentTab and ChatPane show it).

import type { Usage, UsageKind } from './api.ts'

export const USAGE_LABELS: Record<UsageKind, string> = {
  answer: 'Answer',
  upload: 'Upload',
  ocr: 'OCR',
  infer_sections: 'Outline',
  enrich: 'Summaries and facts',
  embed: 'Embeddings',
  render: 'Page renders',
}

// The order the kinds happen to a document in.
const DOCUMENT_KINDS: UsageKind[] = ['upload', 'ocr', 'infer_sections', 'enrich', 'embed', 'render']

const count = (n: number, one: string, many = `${one}s`) => `${n.toLocaleString()} ${n === 1 ? one : many}`

/** A cost in millionths of the currency, as money: "$0.0012". */
export function formatCost(micros: number, currency: string): string {
  const amount = micros / 1_000_000
  try {
    return amount.toLocaleString(undefined, {
      style: 'currency',
      currency,
      minimumFractionDigits: 2,
      maximumFractionDigits: 4,
    })
  } catch {
    // A code the browser does not know as a currency: the amount and the code as given.
    return `${amount.toFixed(4)} ${currency}`
  }
}

function formatBytes(bytes: number): string {
  if (bytes < 1_000_000) return `${Math.max(1, Math.round(bytes / 1000)).toLocaleString()} kB`
  return `${(bytes / 1_000_000).toLocaleString(undefined, { maximumFractionDigits: 1 })} MB`
}

/**
 * The usage of one kind in a few words: "2 calls · 1,234 tokens in, 56 out · $0.0012". The
 * cost is left out while the currency is not known yet.
 */
export function describeUsage(usage: Usage, currency: string | undefined, kind?: UsageKind): string {
  const parts: string[] = []
  if (usage.bytes) parts.push(formatBytes(usage.bytes))
  if (usage.pages) {
    const noun = kind === 'ocr' ? ['page read', 'pages read'] : kind === 'render' ? ['page rendered', 'pages rendered'] : ['page']
    parts.push(count(usage.pages, noun[0], noun[1]))
  }
  if (usage.calls) parts.push(count(usage.calls, 'call'))
  if (usage.output_tokens) {
    parts.push(`${(usage.input_tokens ?? 0).toLocaleString()} tokens in, ${usage.output_tokens.toLocaleString()} out`)
  } else if (usage.input_tokens) {
    // An embedding model reads and writes no text back.
    parts.push(count(usage.input_tokens, 'token'))
  }
  if (usage.inputs) parts.push(`${count(usage.inputs, 'text')} embedded`)
  if (usage.cost_micros != null && currency) parts.push(formatCost(usage.cost_micros, currency))
  return parts.join(' · ')
}

/** An answer's usage with its model first: "google:gemini-2.5-flash-lite · 2 calls · …". */
export function describeAnswerUsage(usage: Usage, currency: string | undefined): string {
  return [usage.model, describeUsage(usage, currency, 'answer')].filter(Boolean).join(' · ')
}

/**
 * What bringing in, indexing and viewing the document used: one line adding the model
 * calls, tokens, texts embedded and cost up over the kinds, and the breakdown by kind for a
 * tooltip. Null while nothing was spent on a model or on embeddings.
 */
export function describeDocumentUsage(
  usage: Partial<Record<UsageKind, Usage>>,
  currency: string | undefined,
): { summary: string; detail: string } | null {
  const kinds = DOCUMENT_KINDS.filter((kind) => usage[kind])
  let calls = 0
  let tokens = 0
  let inputs = 0
  let cost: number | null = null
  for (const kind of kinds) {
    const spent = usage[kind]!
    calls += spent.calls ?? 0
    tokens += (spent.input_tokens ?? 0) + (spent.output_tokens ?? 0)
    inputs += spent.inputs ?? 0
    if (spent.cost_micros != null) cost = (cost ?? 0) + spent.cost_micros
  }
  if (!calls && !inputs && cost == null) return null
  const parts: string[] = []
  if (calls) parts.push(count(calls, 'model call'))
  if (tokens) parts.push(count(tokens, 'token'))
  if (inputs) parts.push(`${count(inputs, 'text')} embedded`)
  if (cost != null && currency) parts.push(formatCost(cost, currency))
  const detail = kinds.map((kind) => `${USAGE_LABELS[kind]}: ${describeUsage(usage[kind]!, currency, kind)}`).join('\n')
  return { summary: parts.join(' · '), detail }
}
