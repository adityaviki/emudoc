// What an answer, a run's report or a draft used, put into words (ChatPane, RunView, DraftView and the question sets' reports show it).

import type { Usage } from './api.ts'

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

/**
 * An answer's usage in a few words, its model first: "google:gemini-2.5-flash-lite · 2 calls ·
 * 1,234 tokens in, 56 out · $0.0012". The cost is left out while the currency is not known yet.
 */
export function describeAnswerUsage(usage: Usage, currency: string | undefined): string {
  const parts = usage.model ? [usage.model] : []
  if (usage.calls) parts.push(count(usage.calls, 'call'))
  if (usage.output_tokens) {
    parts.push(`${(usage.input_tokens ?? 0).toLocaleString()} tokens in, ${usage.output_tokens.toLocaleString()} out`)
  } else if (usage.input_tokens) {
    parts.push(count(usage.input_tokens, 'token'))
  }
  if (usage.cost_micros != null && currency) parts.push(formatCost(usage.cost_micros, currency))
  return parts.join(' · ')
}
