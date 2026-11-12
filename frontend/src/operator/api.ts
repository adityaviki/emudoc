// The operator's area's requests and answers. Mirrors the routes in backend/app/api/operator.py
// and the Operator* models in backend/app/schemas.py; amounts are millionths of the currency,
// as the server keeps them.

import { ApiError } from '../api.ts'
import type { Organization, Plan, Role, UsagePage } from '../api.ts'

export interface Operator {
  user_id: number
  name: string
  email: string
  environment: 'development' | 'production'
  version: string
  currency: string
  /** The member's account the operator's session views as support (backend/app/support.py), null for none. */
  viewing: Viewing | null
}

export interface Viewing {
  org_id: number
  organization: string
  user_id: number
  name: string
  email: string
  ends_at: string
}

export interface OrganizationRow {
  id: number
  name: string
  slug: string
  plan: Plan
  seats: number | null
  members: number
  created_at: string
  subscription_status: string | null
  deletion_due_at: string | null
  /** Its known model cost over the list's `cost_days`, null while none is known. */
  cost_micros: number | null
}

export interface OrganizationList {
  organizations: OrganizationRow[]
  next_before: number | null
  cost_days: number
}

/** One figure the organisation is held to, worded as the `plan` command prints it, its plan's own beside it. */
export interface Entitlement {
  name: string
  value: string
  plan_value: string
  agreed: boolean
}

export interface Member {
  user_id: number
  name: string
  email: string
  role: Role
  mfa_enabled: boolean
  joined_at: string
  /** Until when the member lets support see their account here, null while they do not. */
  support_until: string | null
}

/** What work cost over a span of days, against the daily limit it is held to (null for none). */
export interface Cost {
  cost_micros: number | null
  calls: number
  input_tokens: number
  output_tokens: number
  answers: number
  pages: number
  top_day_micros: number | null
  limit_micros: number | null
  days_at_limit: number | null
}

/** A plan an organisation may be put on, with its own figures worded as the entitlements are. */
export interface PlanOffered {
  name: Plan
  by_the_seat: boolean
  figures: Record<string, string>
}

export interface OrganizationDetail {
  organization: Organization
  usage: UsagePage
  entitlements: Entitlement[]
  /** The figures agreed with it in place of its plan's, worded as the entitlements are; its seats are the organisation's. */
  overrides: Record<string, string>
  plans: PlanOffered[]
  members: Member[]
  stripe_customer_id: string | null
  stripe_subscription_id: string | null
  since: string
  until: string
  cost: Cost
  cost_days: { day: string; cost_micros: number }[]
  /** Every feature flag as it stands for the organisation, in the order of FLAGS. */
  flags: FlagState[]
}

/** A feature flag as it stands for one organisation (backend/app/flags.py): its rollout's share of organisations in percent, an operator's choice for this one (null for the rollout's) and whether it is on. */
export interface FlagState {
  name: string
  title: string
  share: number
  chosen: boolean | null
  on: boolean
}

/** A feature flag across the deployment: how many organisations it is on for of all of them, and those an operator chose for. */
export interface FlagRollout {
  name: string
  title: string
  share: number
  on: number
  organizations: number
  chosen: { org_id: number; slug: string; name: string; on: boolean }[]
}

/** What an organisation is to be held to, as a whole: each agreed figure in the words the area shows, the rest the plan's own. */
export interface PlanChange {
  plan: Plan
  seats: number | null
  agreed: Record<string, string>
}

export interface User {
  id: number
  name: string
  email: string
  created_at: string
  email_verified: boolean
  mfa_enabled: boolean
  operator: boolean
  deletion_due_at: string | null
  last_seen_at: string | null
  memberships: { org_id: number; slug: string; name: string; role: Role }[]
}

export interface UserList {
  users: User[]
  more: boolean
}

export interface OrganizationCost extends Cost {
  org_id: number
  slug: string
  name: string
  plan: Plan
}

export interface ModelCost {
  kind: string
  model: string
  cost_micros: number | null
  calls: number
  input_tokens: number
  output_tokens: number
  unpriced_tokens: number
}

export interface Costs {
  since: string
  until: string
  currency: string
  organizations: OrganizationCost[]
  platform: Cost
  idle: number
  models: ModelCost[]
}

export interface Queue {
  kind: string
  due: number
  waiting: number
  running: number
  failed: number
  oldest_due_seconds: number | null
}

export interface Job {
  id: number
  kind: string
  status: string
  attempts: number
  max_attempts: number
  run_at: string
  lease_until: string | null
  finished_at: string | null
  worker_id: string | null
  about: string
  last_error: string | null
  reported: boolean
}

/** A worker seen within the last day: running, stopped by itself, or neither, which is a process that died or lost the database. */
export interface Worker {
  id: string
  version: string
  started_at: string
  last_seen_at: string
  stopped_at: string | null
  running: boolean
}

export interface Jobs {
  taken_at: string
  queue: Queue[]
  running: Job[]
  failed: Job[]
  failed_total: number
  history_days: number
  /** The workers seen lately, the latest first; none running leaves the queue's work waiting. */
  workers: Worker[]
}

/** What running failed jobs again did: how many documents it queued, and a line for each job. */
export interface Requeued {
  queued: number
  lines: string[]
}

/** A model provider the settings name a model of, or that the area holds switched off, and what holds it off. */
export interface Provider {
  name: string
  variables: string[]
  off: boolean
  by_setting: boolean
  switched_at: string | null
  switched_by: string | null
}

export interface Concern {
  key: string
  title: string
  message: string
  raised_at: string | null
}

export interface Services {
  models: { variable: string; name: string; role: string; fallback: boolean; disabled: boolean }[]
  providers: Provider[]
  model_problem: string | null
  embedding_model: string
  embedding_address: string
  scanner: boolean
  storage_origin: string | null
  concerns: Concern[]
  unchecked: string[]
  database: { version: string; role: string; vector: string | null; iterative_scans: boolean; timeouts: Record<string, string> }
}

// Every request carries the app's header, which the server asks of a change (`refuse_cross_site`), and a change its body as JSON.
async function request<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const response = await fetch(`/api/operator${path}`, {
    method,
    headers: { 'X-Requested-With': 'fetch', ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!response.ok) {
    let detail = response.statusText
    try {
      const body = await response.json()
      if (typeof body.detail === 'string') detail = body.detail
      // A body the server could not read: what was wrong with each part.
      else if (Array.isArray(body.detail)) detail = body.detail.map((error: { msg: string }) => error.msg).join('; ')
    } catch {
      // Not JSON: keep the status's words.
    }
    throw new ApiError(response.status, detail)
  }
  return response.status === 204 ? (undefined as T) : response.json()
}

const query = (values: Record<string, string | number | null | undefined>) => {
  const search = new URLSearchParams()
  for (const [name, value] of Object.entries(values)) if (value !== null && value !== undefined && value !== '') search.set(name, String(value))
  const text = search.toString()
  return text ? `?${text}` : ''
}

export const operatorApi = {
  me: () => request<Operator>('/me'),
  organizations: (q: string, before: number | null) => request<OrganizationList>(`/organizations${query({ q, before })}`),
  organization: (id: number) => request<OrganizationDetail>(`/organizations/${id}`),
  users: (q: string) => request<UserList>(`/users${query({ q })}`),
  costs: (days: number) => request<Costs>(`/costs${query({ days })}`),
  jobs: () => request<Jobs>('/jobs'),
  services: () => request<Services>('/services'),
  changePlan: (id: number, change: PlanChange) => request<OrganizationDetail>(`/organizations/${id}/plan`, 'PUT', change),
  /** Turn a feature flag on or off for the organisation whatever its rollout, or back to its rollout for null. */
  chooseFlag: (id: number, name: string, on: boolean | null) => request<OrganizationDetail>(`/organizations/${id}/flags/${encodeURIComponent(name)}`, 'PUT', { on }),
  flags: () => request<FlagRollout[]>('/flags'),
  /** Run failed jobs again by id, or every one for null. */
  requeue: (jobIds: number[] | null) => request<Requeued>('/jobs/requeue', 'POST', { job_ids: jobIds }),
  switchProvider: (name: string, off: boolean) => request<Provider[]>(`/providers/${encodeURIComponent(name)}`, 'PUT', { off }),
  /** View the member's account in the organisation as support, from the operator's own session, while the member lets it. */
  viewAs: (orgId: number, userId: number) => request<Viewing>(`/organizations/${orgId}/members/${userId}/support`, 'POST'),
  endView: () => request<void>('/support', 'DELETE'),
  /** Mail an address the link that makes an account, which works while sign-up is closed. */
}
