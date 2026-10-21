// How the operator's area words its figures, as the maintenance commands print them
// (backend/app/maintenance.py): costs in the deployment's currency, spans of time, counts.

import type { Cost, Job } from './api.ts'

/** An amount of millionths of the currency: to the cent, or to four places below a cent, as a cheap model's answer costs a tenth of one. */
export function amount(micros: number, currency: string): string {
  const places = micros > 0 && micros < 10_000 ? 4 : 2
  return `${(micros / 1_000_000).toLocaleString('en', { minimumFractionDigits: places, maximumFractionDigits: places })} ${currency}`
}

/** A cost as the `costs` command shows it: unpriced when tokens were recorded without a price, nothing when there were none to price. */
export function known(micros: number | null, tokens: number, currency: string): string {
  if (micros !== null) return amount(micros, currency)
  return tokens ? 'unpriced' : amount(0, currency)
}

export const tokensOf = (cost: Pick<Cost, 'input_tokens' | 'output_tokens'>) => cost.input_tokens + cost.output_tokens

/** The costliest day against the daily limit, as "1.20 EUR, 120 % of 1.00 EUR", or the day alone without a limit. */
export function topDay(cost: Cost, currency: string): string {
  if (cost.top_day_micros === null) return '—'
  const day = amount(cost.top_day_micros, currency)
  if (!cost.limit_micros) return day
  return `${day}, ${Math.floor((cost.top_day_micros * 100) / cost.limit_micros)} % of ${amount(cost.limit_micros, currency)}`
}

export const count = (value: number, one: string, many = `${one}s`) => `${value.toLocaleString('en')} ${value === 1 ? one : many}`

/** A span of seconds in the largest unit that reads well: 45 s, 12 min, 3 h 5 min, 2 days. */
export function span(seconds: number): string {
  const whole = Math.max(0, Math.round(seconds))
  if (whole < 60) return `${whole} s`
  const minutes = Math.floor(whole / 60)
  if (minutes < 60) return `${minutes} min`
  const hours = Math.floor(minutes / 60)
  if (hours < 48) return minutes % 60 ? `${hours} h ${minutes % 60} min` : `${hours} h`
  return count(Math.floor(hours / 24), 'day')
}

const seconds = (from: string, to: string) => (new Date(to).getTime() - new Date(from).getTime()) / 1000

/** A running job's lease as the `jobs` command words it: the time left, or how long ago it ran out, which is a worker that stopped. */
export function lease(job: Job, takenAt: string): string {
  if (!job.lease_until) return ''
  const left = seconds(takenAt, job.lease_until)
  return left >= 0 ? `${span(left)} left` : `ran out ${span(-left)} ago`
}

/** How long ago a moment was, from when the figures were taken. */
export const ago = (moment: string, takenAt: string) => `${span(seconds(moment, takenAt))} ago`
