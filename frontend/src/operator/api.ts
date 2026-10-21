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

export interface OrganizationDetail {
  organization: Organization
  usage: UsagePage
  entitlements: Entitlement[]
  members: Member[]
  stripe_customer_id: string | null
  stripe_subscription_id: string | null
  since: string
  until: string
  cost: Cost
  cost_days: { day: string; cost_micros: number }[]
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

export interface Jobs {
  taken_at: string
  queue: Queue[]
  running: Job[]
  failed: Job[]
  failed_total: number
  history_days: number
}

export interface Concern {
  key: string
  title: string
  message: string
  raised_at: string | null
}

export interface Services {
  models: { variable: string; name: string; role: string; fallback: boolean; disabled: boolean }[]
  model_problem: string | null
  embedding_model: string
  embedding_address: string
  scanner: boolean
  storage_origin: string | null
  concerns: Concern[]
  unchecked: string[]
  database: { version: string; role: string; vector: string | null; iterative_scans: boolean; timeouts: Record<string, string> }
}

async function request<T>(path: string): Promise<T> {
  const response = await fetch(`/api/operator${path}`, { headers: { 'X-Requested-With': 'fetch' } })
  if (!response.ok) {
    let detail = response.statusText
    try {
      const body = await response.json()
      if (typeof body.detail === 'string') detail = body.detail
    } catch {
      // Not JSON: keep the status's words.
    }
    throw new ApiError(response.status, detail)
  }
  return response.json()
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
}
