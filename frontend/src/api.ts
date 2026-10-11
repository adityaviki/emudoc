// Types mirror the backend's response schemas (backend/app/schemas.py).

export type StageName = 'parse' | 'structure' | 'enrich' | 'embed'
export const STAGES: StageName[] = ['parse', 'structure', 'enrich', 'embed']

export interface Stage {
  status: 'pending' | 'running' | 'done' | 'skipped' | 'failed'
  detail: string
}

export interface Project {
  id: number
  name: string
  description: string
  created_at: string
  document_count: number
}

export interface Folder {
  id: number
  project_id: number
  parent_id: number | null
  name: string
  created_at: string
}

export interface Document {
  id: number
  project_id: number
  folder_id: number | null
  filename: string
  mime_type: string
  size_bytes: number
  status: 'queued' | 'processing' | 'ready' | 'failed'
  stages: Partial<Record<StageName, Stage>>
  error: string | null
  page_count: number | null
  title: string | null
  doc_type: string | null
  doc_date: string | null
  summary: string | null
  created_at: string
}

export interface UploadResult {
  documents: Document[]
  rejected: { filename: string; reason: string }[]
}

export interface Section {
  id: number
  title: string
  level: number
  summary: string | null
  page_start: number | null
  page_end: number | null
  block_start: number
  block_end: number
  children: Section[]
}

export interface Block {
  ordinal: number
  kind: 'heading' | 'paragraph' | 'list_item' | 'table'
  level: number | null
  text: string
  page: number | null
  bbox: [number, number, number, number] | null
}

export interface DocumentContent {
  blocks: Block[]
  page_sizes: [number, number][]
}

export interface Passage {
  id: number
  document_id: number
  document_title: string
  section_id: number
  context: string
  text: string
  page_start: number | null
  page_end: number | null
  block_start: number
  block_end: number
}

export interface Fact {
  id: number
  document_id: number
  document_title: string
  name: string
  value_type: string
  value: string
  number: number | null
  date: string | null
  currency: string | null
  quote: string | null
  page: number | null
  block_ordinal: number | null
  passage_id: number | null
  verified: boolean
}

export interface SearchHit {
  passage_id: number
  document_id: number
  document_title: string
  doc_type: string | null
  context: string
  snippet: { text: string; match: boolean }[]
  page_start: number | null
  page_end: number | null
  score: number
  keyword_rank: number | null
  vector_rank: number | null
}

export interface SearchResult {
  hits: SearchHit[]
  semantic: boolean
}

export interface SystemStatus {
  llm: { available: boolean; agent_model: string; index_model: string; error: string | null }
  embeddings: { ok: boolean; model: string; error: string | null }
}

/** A passage the agent was shown, which its answer may cite as [P<id>]. */
export interface Source {
  id: number
  document_id: number
  document_title: string
  context: string
  page_start: number | null
  page_end: number | null
  excerpt: string
}

/** A chat is private to whoever started it until they share it with the project. */
export type ChatVisibility = 'private' | 'project'

export interface Chat {
  id: number
  project_id: number
  title: string
  visibility: ChatVisibility
  created_at: string
  updated_at: string
}

/** One tool call the agent made while answering. */
export interface ChatStep {
  id: string
  name: string
  input: Record<string, unknown>
  summary: string
  is_error: boolean
}

export interface ChatMessage {
  id: number
  role: 'user' | 'assistant'
  content: string
  steps: ChatStep[]
  sources: Source[]
  notice: string | null
  error: string | null
  created_at: string
}

export interface ChatDetail extends Chat {
  messages: ChatMessage[]
}

export type ChatEvent =
  | { type: 'text'; text: string }
  | { type: 'thinking' }
  | { type: 'retry' }
  | { type: 'tool_call'; id: string; name: string; input: Record<string, unknown> }
  | { type: 'tool_result'; id: string; summary: string; is_error: boolean }
  | { type: 'sources'; sources: Source[] }
  | { type: 'notice'; message: string }
  | { type: 'error'; message: string }
  | { type: 'done'; usage: { input_tokens: number; output_tokens: number } }

export interface Organization {
  id: number
  name: string
  slug: string
}

/** The signed-in user, and the organisation and role they act with. */
export interface Me {
  id: number
  email: string
  name: string
  email_verified: boolean
  mfa_enabled: boolean
  /** Providers the account can sign in through, such as "google". */
  identities: string[]
  organization: Organization
  role: string
}

export interface OAuthProvider {
  name: string
  label: string
}

/** A signed-in browser or device. */
export interface UserSession {
  id: number
  user_agent: string
  ip: string | null
  created_at: string
  last_seen_at: string
  /** The session this very browser holds. */
  current: boolean
}

export interface MfaSetup {
  secret: string
  otpauth_url: string
}

// The password rules the server applies (backend/app/schemas.py), so forms can say so first.
export const PASSWORD_MIN_LENGTH = 8
export const PASSWORD_MAX_LENGTH = 200

export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

let onUnauthorized: (() => void) | null = null

/**
 * What happens when the server no longer accepts the session: the app registers a handler
 * that returns to the sign-in page. Sign-in's own routes are left out, since a wrong password
 * or code answers 401 too and those are shown on the form.
 */
export function handleUnauthorized(handler: (() => void) | null) {
  onUnauthorized = handler
}

/**
 * Every request carries this header. The server refuses a change without it, which is what
 * keeps a page on another site from making one with the user's cookie (backend/app/api/deps.py).
 */
async function send(path: string, init?: RequestInit): Promise<Response> {
  const headers = new Headers(init?.headers)
  headers.set('X-Requested-With', 'fetch')
  const response = await fetch(`/api${path}`, { ...init, headers })
  if (response.status === 401 && !path.startsWith('/auth/')) onUnauthorized?.()
  return response
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await send(path, init)
  if (!response.ok) {
    let message = response.statusText
    try {
      const body = await response.json()
      if (typeof body.detail === 'string') message = body.detail
    } catch {
      // Not JSON: keep the status text.
    }
    throw new ApiError(response.status, message)
  }
  return response.status === 204 ? (undefined as T) : response.json()
}

function json(method: string, body: unknown): RequestInit {
  return { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
}

/** Sign-in and the account. Mirrors the routes in backend/app/api/auth.py. */
export const auth = {
  /** The signed-in user, or null when there is no usable session. */
  me: async (): Promise<Me | null> => {
    const response = await send('/auth/me')
    if (response.status === 401) return null
    if (!response.ok) throw new ApiError(response.status, response.statusText)
    return response.json()
  },
  options: () => request<{ providers: OAuthProvider[] }>('/auth/options'),
  signup: (body: { email: string; name: string; password: string }) =>
    request<Me>('/auth/signup', json('POST', body)),
  /** With two-factor on, the session grants nothing until `verifyMfa` succeeds. */
  login: (body: { email: string; password: string }) =>
    request<{ mfa_required: boolean }>('/auth/login', json('POST', body)),
  verifyMfa: (code: string) => request<Me>('/auth/mfa/verify', json('POST', { code })),
  logout: () => request<void>('/auth/logout', { method: 'POST' }),

  verifyEmail: (token: string) => request<void>('/auth/verify', json('POST', { token })),
  resendVerification: () => request<void>('/auth/verify/resend', { method: 'POST' }),
  forgotPassword: (email: string) => request<void>('/auth/password/forgot', json('POST', { email })),
  resetPassword: (body: { token: string; password: string }) =>
    request<void>('/auth/password/reset', json('POST', body)),
  changePassword: (body: { current_password: string; new_password: string }) =>
    request<void>('/auth/password', json('POST', body)),

  sessions: () => request<UserSession[]>('/auth/sessions'),
  revokeSession: (id: number) => request<void>(`/auth/sessions/${id}`, { method: 'DELETE' }),
  revokeOtherSessions: () => request<void>('/auth/sessions', { method: 'DELETE' }),

  setupMfa: () => request<MfaSetup>('/auth/mfa/setup', { method: 'POST' }),
  /** Returns the recovery codes, shown once. */
  enableMfa: (code: string) => request<{ recovery_codes: string[] }>('/auth/mfa/enable', json('POST', { code })),
  disableMfa: (body: { password?: string; code?: string }) =>
    request<void>('/auth/mfa/disable', json('POST', body)),
}

/**
 * Where a sign-in with a provider starts. It is a navigation, not a fetch: the provider sends
 * the browser back to `next`, or to /login with `mfa=1` or an `error` to show.
 */
export const oauthStartUrl = (provider: string, next: string) =>
  `/api/auth/oauth/${provider}/start?${new URLSearchParams({ next })}`

export const api = {
  status: () => request<SystemStatus>('/status'),

  projects: () => request<Project[]>('/projects'),
  project: (id: number) => request<Project>(`/projects/${id}`),
  createProject: (body: { name: string; description: string }) =>
    request<Project>('/projects', json('POST', body)),
  deleteProject: (id: number) => request<void>(`/projects/${id}`, { method: 'DELETE' }),
  reprocessProject: (id: number, fromStage: StageName) =>
    request<{ queued: number }>(`/projects/${id}/reprocess?from_stage=${fromStage}`, {
      method: 'POST',
    }),

  folders: (projectId: number) => request<Folder[]>(`/projects/${projectId}/folders`),
  createFolder: (projectId: number, body: { name: string; parent_id: number | null }) =>
    request<Folder>(`/projects/${projectId}/folders`, json('POST', body)),
  updateFolder: (id: number, body: { name?: string; parent_id?: number | null }) =>
    request<Folder>(`/folders/${id}`, json('PATCH', body)),
  deleteFolder: (id: number) => request<void>(`/folders/${id}`, { method: 'DELETE' }),

  documents: (projectId: number) => request<Document[]>(`/projects/${projectId}/documents`),
  upload: (projectId: number, files: File[], folderId: number | null) => {
    const form = new FormData()
    for (const file of files) form.append('files', file)
    const query = folderId == null ? '' : `?folder_id=${folderId}`
    return request<UploadResult>(`/projects/${projectId}/documents${query}`, { method: 'POST', body: form })
  },
  document: (id: number) => request<Document>(`/documents/${id}`),
  updateDocument: (id: number, body: { filename?: string; folder_id?: number | null }) =>
    request<Document>(`/documents/${id}`, json('PATCH', body)),
  deleteDocument: (id: number) => request<void>(`/documents/${id}`, { method: 'DELETE' }),
  reprocessDocument: (id: number, fromStage: StageName) =>
    request<Document>(`/documents/${id}/reprocess?from_stage=${fromStage}`, { method: 'POST' }),
  outline: (id: number) => request<Section[]>(`/documents/${id}/outline`),
  content: (id: number) => request<DocumentContent>(`/documents/${id}/content`),
  documentFacts: (id: number) => request<Fact[]>(`/documents/${id}/facts`),
  passage: (id: number) => request<Passage>(`/passages/${id}`),

  search: (projectId: number, query: string, folderId: number | null = null) => {
    const params = new URLSearchParams({ q: query, limit: '20' })
    if (folderId != null) params.set('folder_id', String(folderId))
    return request<SearchResult>(`/projects/${projectId}/search?${params}`)
  },
  facts: (projectId: number, filters: { name?: string; q?: string }) => {
    const params = new URLSearchParams({ limit: '500' })
    if (filters.name) params.set('name', filters.name)
    if (filters.q) params.set('q', filters.q)
    return request<{ total: number; facts: Fact[] }>(`/projects/${projectId}/facts?${params}`)
  },
  factNames: (projectId: number) =>
    request<{ name: string; count: number }[]>(`/projects/${projectId}/facts/names`),

  chats: (projectId: number) => request<Chat[]>(`/projects/${projectId}/chats`),
  createChat: (projectId: number) => request<Chat>(`/projects/${projectId}/chats`, { method: 'POST' }),
  chat: (id: number) => request<ChatDetail>(`/chats/${id}`),
  renameChat: (id: number, title: string) => request<Chat>(`/chats/${id}`, json('PATCH', { title })),
  shareChat: (id: number, visibility: ChatVisibility) =>
    request<Chat>(`/chats/${id}`, json('PATCH', { visibility })),
  deleteChat: (id: number) => request<void>(`/chats/${id}`, { method: 'DELETE' }),
}

export const fileUrl = (documentId: number) => `/api/documents/${documentId}/file`
export const pageImageUrl = (documentId: number, page: number) =>
  `/api/documents/${documentId}/pages/${page}.png`

/** The file types the backend indexes (backend/app/parsing/__init__.py), for upload inputs. */
export const ACCEPTED_EXTENSIONS = ['.pdf', '.docx', '.xlsx', '.csv', '.txt', '.md', '.png', '.jpg', '.jpeg', '.webp']
export const ACCEPTED_TYPES_LABEL = 'PDF, Word, Excel, CSV, text, Markdown and images'

export const isIndexing = (document: Document) =>
  document.status === 'queued' || document.status === 'processing'
export const isPdf = (document: Document) => document.mime_type === 'application/pdf'
export const isImage = (document: Document) => document.mime_type.startsWith('image/')
export const isSheet = (document: Document) =>
  document.mime_type === 'text/csv' || document.mime_type.endsWith('spreadsheetml.sheet')

/** Add a question to a chat; yields the agent's events as the server streams them. */
export async function* streamAnswer(
  chatId: number,
  content: string,
  signal: AbortSignal,
): AsyncGenerator<ChatEvent> {
  const response = await send(`/chats/${chatId}/messages`, { ...json('POST', { content }), signal })
  if (!response.ok || !response.body) {
    throw new ApiError(response.status, 'The server could not start an answer.')
  }
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader()
  let buffer = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) return
    buffer += value
    // Server-sent events are separated by a blank line.
    let end: number
    while ((end = buffer.indexOf('\n\n')) >= 0) {
      const line = buffer.slice(0, end)
      buffer = buffer.slice(end + 2)
      if (line.startsWith('data: ')) yield JSON.parse(line.slice(6)) as ChatEvent
    }
  }
}

export function pageLabel(start: number | null, end?: number | null): string {
  if (start == null) return ''
  return end == null || end === start ? `p. ${start}` : `pp. ${start}–${end}`
}

/** A passage's path without the document title it starts with. */
export function sectionPath(context: string, documentTitle: string): string {
  const prefix = `${documentTitle} › `
  const path = context.startsWith(prefix) ? context.slice(prefix.length) : context
  return path === documentTitle ? '' : path
}
