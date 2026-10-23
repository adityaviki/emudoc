// Types mirror the backend's response schemas (backend/app/schemas.py).

// The pipeline's stages in order (backend/app/models.py): the malware scan comes first.
export type StageName = 'scan' | 'parse' | 'structure' | 'enrich' | 'embed'
export const STAGES: StageName[] = ['scan', 'parse', 'structure', 'enrich', 'embed']

export interface Stage {
  status: 'pending' | 'running' | 'done' | 'skipped' | 'failed'
  detail: string
  /**
   * While a failed stage waits to run again by itself (backend/app/worker.py): when; null once
   * it runs or "Index again" overtakes it.
   */
  retry_at: string | null
  /** Whether that wait is for the day's spending limit to reset rather than a backoff after the failure. */
  deferred: boolean
}

export type UsageKind = 'answer' | 'upload' | 'ocr' | 'infer_sections' | 'enrich' | 'embed' | 'render'

/**
 * What one kind of work used for a document or an answer, summed over every run for it. A
 * unit the work did not count is 0 from the API and absent from the `done` event; the cost
 * is in millionths of the currency the status names, and null without a price for the model.
 */
export interface Usage {
  model: string | null
  calls?: number
  input_tokens?: number
  output_tokens?: number
  pages?: number
  bytes?: number
  inputs?: number
  cost_micros: number | null
}

/**
 * Who sees a project besides the organisation's owners and admins, who see every one: everyone
 * in the organisation, or the members chosen for it (backend/app/access.py).
 */
export type ProjectVisibility = 'organization' | 'private'

export interface Project {
  id: number
  name: string
  description: string
  created_at: string
  visibility: ProjectVisibility
  /** The sample project a new organisation starts with (backend/app/sample): Emudoc's own documents, which count against nothing on the plan and take no uploads. */
  sample: boolean
  /** The questions an empty chat offers to ask; none offers the page's own. */
  suggestions: string[]
  document_count: number
  /** The documents partly indexed (`unfinishedStages` in indexing.ts), which the library offers to index the rest of. */
  partly_indexed_count: number
  /** Those of them without summaries and facts, whose rest waits for the AI model too. */
  unenriched_count: number
}

/** A project in the organisation's trash (backend/app/trash.py). */
export interface TrashedProject {
  id: number
  name: string
  sample: boolean
  document_count: number
  deleted_at: string
  /** Who deleted it; null once their account is gone. */
  deleted_by: string | null
  /** When it is deleted for good, unless it is restored first. */
  purged_at: string
}

/** What a project's trash holds: its folders and documents, and the asker's own chats. */
export type TrashKind = 'folder' | 'document' | 'chat'

export interface Trashed {
  kind: TrashKind
  id: number
  /** The folder's or the file's name, or the chat's title (empty before its first question). */
  name: string
  /** The path of the folder it was in; null at the project's root, and for a chat. */
  place: string | null
  /** For a folder, the documents that went with it. */
  documents: number
  mime_type: string | null
  deleted_at: string
  deleted_by: string | null
  purged_at: string
}

/** The newest of a project's trash, how much it holds in all, and how many days it keeps it. */
export interface ProjectTrash {
  items: Trashed[]
  total: number
  days: number
}

/** What a fact's value is, and so a template's field (FACT_TYPES in backend/app/models.py). */
export type FactType = 'text' | 'number' | 'date' | 'money'

/** A field of an extraction template (TemplateFieldOut in backend/app/schemas.py): the name its facts have, its type, what it is, whether every document of the kind should state it, and values as documents write them. */
export interface TemplateField {
  name: string
  type: FactType
  description: string
  required: boolean
  examples: string[]
}

/** An extraction template in force for a project (TemplateOut): its own, with an id, or a built-in one, which applies while the project has none of its kind. */
export interface ExtractionTemplate {
  id: number | null
  doc_type: string
  title: string
  description: string
  fields: TemplateField[]
  built_in: boolean
  /** A project's own template of a kind a built-in one is for, which it replaces. */
  replaces_built_in: boolean
  /** The project's documents whose facts were last read for it. */
  documents: number
  /** Those of its kind read for no template, such as the ones read before it was made, which reading its documents again takes too. */
  matching: number
  /** Its documents waiting for their summaries and facts to be read, again or retried. */
  reading: number
  /** Those whose last reading of its kind changed a field or the template they are read for. */
  changed: number
  updated_at: string | null
}

/** Which documents to read again for the templates as they are now (ReadAgainIn): those of a template's kind, and those read for a kind its template was for before. */
export interface ReadAgainInput {
  kind?: string
  was?: string
}

/** A field whose facts a document's last reading changed (FieldChangeOut): the values read before and no longer, and those read now and not before, each cut at a few of the counts; `checked` when a value gone was a member's check. */
export interface FieldChange {
  name: string
  before: string[]
  after: string[]
  before_count: number
  after_count: number
  checked: boolean
}

/** What a document's last reading changed (DocumentChangesOut): the template it was read for before and now, and its fields. */
export interface DocumentChanges {
  document_id: number
  title: string
  filename: string
  read_at: string
  template_before: string | null
  template_after: string | null
  fields: FieldChange[]
}

/** The last readings of a project's documents of one kind (FactChangesOut): how many wait to be read, were read and changed, and a page of those that changed. */
export interface FactChanges {
  kind: string
  template: ExtractionTemplate | null
  reading: number
  read: number
  changed: number
  /** How many changed a value a member checked or corrected. */
  unheld: number
  documents: DocumentChanges[]
  next_after: string | null
}

/** A template as a member writes it, whole (TemplateIn): the server makes the kind lowercase words and the fields' names snake_case. */
export interface TemplateInput {
  doc_type: string
  title: string
  description: string
  fields: TemplateField[]
}

/** A project's export the member asked for (ProjectExportOut in backend/app/schemas.py): being made, ready to download until it expires, or failed. */
export interface ProjectExport {
  id: number
  status: 'queued' | 'ready' | 'failed'
  /** How many originals and bytes the file holds; 0 until it is ready. */
  documents: number
  size_bytes: number
  /** Why it could not be made. */
  error: string | null
  created_at: string
  /** Until when it can be downloaded; null until it is ready. */
  expires_at: string | null
}

/** The file of a ready export, for a download link: the member's own alone, until it expires. */
export const exportFileUrl = (projectId: number, exportId: number) => `/api/projects/${projectId}/exports/${exportId}/file`

/** Who sees a project: the members chosen for a private one, by user id; none for an open one. */
export interface ProjectAccess {
  visibility: ProjectVisibility
  user_ids: number[]
}

export interface Folder {
  id: number
  project_id: number
  parent_id: number | null
  name: string
  created_at: string
  /** The documents directly in the folder, not in its subfolders. */
  document_count: number
}

export interface Document {
  id: number
  project_id: number
  folder_id: number | null
  filename: string
  mime_type: string
  size_bytes: number
  // `quarantined`: the malware scan flagged the file; `error` says so, and the file is neither
  // indexed nor served.
  status: 'queued' | 'processing' | 'ready' | 'failed' | 'quarantined'
  stages: Partial<Record<StageName, Stage>>
  error: string | null
  page_count: number | null
  title: string | null
  doc_type: string | null
  doc_date: string | null
  summary: string | null
  /** The kind of the extraction template its facts were read for (backend/app/extraction.py), if any. */
  template: string | null
  usage: Partial<Record<UsageKind, Usage>>
  created_at: string
}

/** A page of the documents directly in one folder, in the library's order (by name). */
export interface DocumentPage {
  documents: Document[]
  /** What to pass as `after` for the next page; null on the last one. */
  next_after: string | null
}

export interface UploadResult {
  documents: Document[]
  /** The files left out, each with why; `limit` names the plan's figure when it was the plan. */
  rejected: { filename: string; reason: string; limit: PlanLimit | null }[]
  /** The upload the documents kept are on the server, which the library follows as they are indexed; null when it kept none. */
  upload_id: number | null
}

/** A document of an upload that could not be indexed, and why, in the words its tab shows. */
export interface UploadFailure {
  id: number
  filename: string
  reason: string
  /** Set apart by the malware scan rather than failed. */
  quarantined: boolean
}

/**
 * How far the documents of some uploads are indexed, those in the trash left out, each counted
 * once: ready, indexing, waiting for a retry or failed (UploadStatusOut in backend/app/schemas.py).
 */
export interface UploadStatus {
  documents: number
  ready: number
  /** Of the ready, those without summaries and facts or their vectors. */
  partly_indexed: number
  indexing: number
  retrying: number
  /** When the soonest retry is due. */
  retry_at: string | null
  /** Failed, or set apart by the malware scan. */
  failed: number
  /** The first of them by name; `failed` counts them all. */
  failures: UploadFailure[]
}

/** A file to upload, with the folders it is in below the one it goes into ("Invoices/2024"), '' for that one itself. */
export interface UploadItem {
  file: File
  folder: string
}

/** What one upload request may carry (UploadLimitsOut in backend/app/schemas.py). */
export interface UploadLimits {
  files: number
  request_bytes: number
  file_bytes: number
}

/** How far a request's body has gone, in bytes, multipart's own lines included. */
export type Progress = (loaded: number, total: number) => void

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
  /** Whether the model's quote was found in the text; a person's word is `check`. */
  verified: boolean
  check: FactCheck | null
}

/** A member's check of a fact (FactCheckOut in backend/app/schemas.py): who and when, and with a correction what the model read in its place. */
export interface FactCheck {
  id: number
  /** The member's name as it is now; null once their account is gone. */
  checked_by: string | null
  checked_at: string
  /** The model's value, when the member wrote another. */
  corrected_from: string | null
  /** What the member said the field holds: their value, or the model's they checked. */
  value: string
}

/** A fact checked as it reads (no `value`), or corrected to the value the document states: words, digits for a number or an amount, a date as 2026-03-01. */
export interface FactCheckInput {
  value?: string
  currency?: string
}

/** A row of a kind's facts table (FactRowOut in backend/app/schemas.py): a document, and the facts of each of its template's fields it has, in the document's order. */
export interface FactRow {
  document_id: number
  title: string
  filename: string
  cells: Record<string, Fact[]>
  /** The checks of a field no fact reads any more, by the field's name: made before the document was read again, which read it differently. */
  earlier: Record<string, FactCheck[]>
}

/** A page of a kind's facts table (FactTableOut): the template whose fields are its columns, its rows, how many rows its conditions keep and how many of those hold a fact nobody checked, and where its next page starts. */
export interface FactTable {
  template: ExtractionTemplate
  rows: FactRow[]
  total: number
  to_review: number
  next_after: string | null
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

/**
 * Whether the models and the embeddings endpoint can be used. The names are the operator's,
 * given in development only; `error` is a sentence for whoever reads the status, what to
 * change in development and what members cannot do for now in a deployment.
 */
export interface SystemStatus {
  llm: {
    available: boolean
    agent_model: string | null
    index_model: string | null
    /** What is asked when the model before it cannot answer; null without one. */
    agent_fallback_model: string | null
    index_fallback_model: string | null
    error: string | null
  }
  embeddings: { ok: boolean; model: string | null; error: string | null }
  /** Whether a worker takes the background work, without which uploads wait to be indexed and questions to be answered. */
  workers: { running: boolean; error: string | null }
  /** The currency the costs on usage are in, an ISO 4217 code. */
  currency: string
  uploads: UploadLimits
}

/** Whether the API can serve, and which version it runs (backend/app/version.py). */
export interface Health {
  status: 'ok'
  /** The commit the image was built from, its full SHA, or "development" for a checkout. */
  version: string
}

/** A frame of the page's own scripts that an error was raised in, minified as the browser ran it. */
export interface PageFrame {
  function: string | null
  /** The built script's path, under /assets. */
  file: string
  line: number
  column: number
}

/**
 * An error the page rendered into, as its error boundary reports it (src/errors.ts): its kind,
 * the API's status when it was the API's refusal, its frames, the innermost first, and the
 * page's path without its query. Never its message, which may quote what the page showed.
 */
export interface PageError {
  name: string
  status: number | null
  frames: PageFrame[]
  path: string
}

/** A help page (backend/app/help), filled in for the deployment: Markdown, its title the first heading. */
export interface HelpArticle {
  slug: string
  title: string
  /** The line the contents show of it. */
  summary: string
  text: string
}

/** The help pages, and where members write for help (backend/app/api/help.py): none only in development. */
export interface Help {
  articles: HelpArticle[]
  support_email: string | null
  /** The status page outside the deployment, which says whether it is up; none where it names none. */
  status_page: string | null
}

/** A day of what's new, or days told as one, and what changed for members then, the newest first, each entry a line of Markdown (backend/app/changelog.py). */
export interface ChangesDay {
  /** ISO dates, the same for a single day. */
  first: string
  last: string
  entries: string[]
}

/** A page of what's new, the newest days first, and the day to ask before for the next, null once the oldest is shown. */
export interface Changelog {
  days: ChangesDay[]
  next_before: string | null
}

/** What a member writes to support about, which the mail's subject says. */
export type FeedbackKind = 'problem' | 'question' | 'idea'

/** A request the API refused or failed, by the reference its answer carried, under which the log keeps it. */
export interface FailedRequest {
  reference: string
  status: number
}

/**
 * A message to support from the help menu: the member's words, and where they were, as the page's
 * path with the ids its query names, never its words (src/help/feedback.ts).
 */
export interface Feedback {
  kind: FeedbackKind
  message: string
  page: string
  failed: FailedRequest | null
}

/** A message posted to support: the reference it went under, for the member to quote, and the address an answer comes from. */
export interface FeedbackSent {
  reference: string
  to: string
}

/** A company the deployment sends data to, as the server reads it from its settings. */
export interface Subprocessor {
  name: string
  purpose: string
  /** What it receives. */
  data: string
  /** Where it handles what it receives, when the settings show it, as "The European Union (eu-central-1)". */
  location: string | null
}

/** The /subprocessors page: the region the documents are kept in, and who receives what. */
export interface Subprocessors {
  region: Region | null
  subprocessors: Subprocessor[]
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
  /** Who started the chat, and their name: a shared chat is theirs alone to ask in, rename and delete. */
  user_id: number
  started_by: string
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

/**
 * How far an answer is. While it is `streaming` the row is empty and its events, streamed by
 * `streamEvents`, are the answer so far; a question is always `done`.
 */
export type MessageStatus = 'streaming' | 'done' | 'stopped' | 'failed'

export interface ChatMessage {
  id: number
  role: 'user' | 'assistant'
  content: string
  steps: ChatStep[]
  sources: Source[]
  notice: string | null
  error: string | null
  status: MessageStatus
  /** What the answer used, under `answer`, once its run has recorded it; empty for a question. */
  usage: Partial<Record<UsageKind, Usage>>
  created_at: string
}

export interface ChatDetail extends Chat {
  messages: ChatMessage[]
}

/** A question as saved, and the empty answer whose events the client follows. */
export interface AskResult {
  question: ChatMessage
  answer: ChatMessage
}

/**
 * An answer tried again, emptied and streaming, and the id of the last event its earlier tries
 * left: followed from there, the new try's events alone arrive, `restart` first. Null when there
 * are none, and the new try's are followed from the start.
 */
export interface RetryResult {
  answer: ChatMessage
  last_event_id: number | null
}

export type ChatEvent =
  | { type: 'text'; text: string }
  | { type: 'thinking' }
  | { type: 'retry' }
  | { type: 'restart' }
  | { type: 'tool_call'; id: string; name: string; input: Record<string, unknown> }
  | { type: 'tool_result'; id: string; summary: string; is_error: boolean }
  | { type: 'sources'; sources: Source[] }
  | { type: 'notice'; message: string }
  | { type: 'error'; message: string }
  | { type: 'done'; usage: Usage }
  // Sent by the events route itself, last, once the answer is settled.
  | { type: 'end'; status: MessageStatus }

/** The plans an organisation can be on (backend/app/entitlements.py), least to most. */
export type Plan = 'free' | 'pro' | 'team' | 'enterprise'
export const PLANS: Plan[] = ['free', 'pro', 'team', 'enterprise']
export const PLAN_LABELS: Record<Plan, string> = { free: 'Free', pro: 'Pro', team: 'Team', enterprise: 'Enterprise' }

/** The figures of a plan a refusal or an excess is about (`PlanLimit` in backend/app/schemas.py). */
export type PlanLimit =
  | 'projects'
  | 'documents'
  | 'storage_bytes'
  | 'pages_per_month'
  | 'questions_per_month'
  | 'members'
  | 'daily_spend_micros'
  | 'api_access'

/** What the organisation's plan turned down: the sentence, and the figure it is held to, null when the server named none. */
export interface PlanRefusal {
  message: string
  limit: PlanLimit | null
}

/** A figure the organisation holds past, once its plan, seats or terms were lowered under it, and how it reads: "3 projects of 1". */
export interface Excess {
  limit: PlanLimit
  words: string
}

/** The regions a deployment can keep its documents in (DATA_REGION, backend/app/residency.py). */
export type Region = 'eu' | 'us'
export const REGION_NAMES: Record<Region, string> = { eu: 'the European Union', us: 'the United States' }

export interface Organization {
  id: number
  name: string
  slug: string
  /** Which plan's limits the organisation is held to; a refusal by them is a 402. */
  plan: Plan
  /** The members it bought, on a plan sold by the seat (Team); null otherwise. */
  seats: number | null
  /** What it may keep and use: its plan's, its seats and what was agreed with it in their place (backend/app/entitlements.py). */
  allowance: Allowance
  /** How it buys its plan through Stripe (backend/app/billing.py). */
  billing: Billing
  /** The region its data is kept in, null when the deployment promised none as it was created. */
  region: Region | null
  /** How many days a chat is kept after its last question, by the plan or the organisation's own choice, whichever is shorter, and an audit log entry, null for good. */
  chat_retention_days: number | null
  audit_retention_days: number | null
  /** What it keeps as before after a move to a plan that keeps less, and until when, with its own choice applied; null when nothing is held (backend/app/retention.py). */
  retention_held: RetentionHeld | null
  /** Its settings, as its owners and admins chose them: the language its documents are searched in, null for the deployment's (backend/app/languages.py); the days it keeps a chat, null for as long as the plan does; how its members are told each kind of notice until they choose. */
  language: string | null
  chat_days: number | null
  notice_defaults: NoticeChoice[]
  /** Its security settings (backend/app/security.py): whether everyone who works in it signs in with two-factor, and the days a session may work in it after signing in, null for as long as the deployment keeps one, which is the most it may choose. */
  mfa_required: boolean
  session_days: number | null
  max_session_days: number
  /** The domains whose confirmed addresses may join it, and the role they join with (backend/app/invitations.py). */
  join_domains: string[]
  join_role: JoinRole
  /** When it is deleted with everything in it, as an owner asked; until then it is held as it stands and an owner may keep it (backend/app/retention.py). Null when nobody asked. */
  deletion_due_at: string | null
  /** The feature flags on for it (backend/app/flags.py): a page shows a feature being rolled out where its flag is here. */
  flags_on: Flag[]
}

/** The names of the feature flags (FLAGS in backend/app/flags.py), so that a page that reads a misspelt one fails the build. */
export type Flag = 'extraction_templates'

/** What the organisation page changes about the organisation (OrganizationChangeIn in backend/app/schemas.py). */
export interface OrganizationChange {
  name?: string
  language?: string | null
  chat_days?: number | null
  notices?: { kind: NoticeKind; app?: boolean; email?: boolean }[]
  /** Turned on only by someone who signs in with two-factor themselves. */
  mfa_required?: boolean
  session_days?: number | null
}

/** The text search configurations the database has, which an organisation may search its documents in, and the deployment's. */
export interface Languages {
  languages: string[]
  default: string
}

/** The plans an owner may buy for the organisation; Free is every organisation's, and Enterprise is agreed. */
export type SoldPlan = 'pro' | 'team'

/** How many days chats are kept after their last question and audit log entries, null for good. */
export interface Retention {
  chat_days: number | null
  audit_days: number | null
}

/** What an organisation keeps as before after a move to a plan that keeps less, and until when: null for a move not made yet. */
export interface RetentionHeld extends Retention {
  until: string | null
}

/** A plan an owner may move the organisation's subscription to, with its seats on Team; Free is cancelling it. */
export interface PlanChoice {
  plan: 'free' | SoldPlan
  seats?: number
}

/** What moving the subscription to a plan would do (`PlanChangeOut` in backend/app/schemas.py), for its owner to read before Stripe's page. */
export interface PlanChange {
  plan: Plan
  seats: number | null
  /** When a cancellation would move it, the end of the period paid for; null for a change of plan, made once Stripe's page confirms it. */
  at: string | null
  /** What it would allow, and what the organisation would hold past that (`over`), which would leave it read-only. */
  allowance: Allowance
  retention: Retention
  /** What it keeps now that would be kept as before for `held_days` after the move, null when the plan keeps everything as long. */
  held: RetentionHeld | null
  held_days: number
}

/** How an organisation buys its plan, as Stripe last said of its subscription. */
export interface Billing {
  /** The plans the deployment sells, empty while billing is off. */
  sold: SoldPlan[]
  /** Whether its subscription sets its plan: not when its terms were agreed with the operator. */
  managed: boolean
  /** Stripe's word for its subscription (trialing, active, past_due, canceled, unpaid, …), null before it had one. */
  status: string | null
  trial_ends_at: string | null
  renews_at: string | null
  ends_at: string | null
  /** Since when a payment has failed, and when the plan stops waiting for it. */
  payment_failed_at: string | null
  grace_ends_at: string | null
  /** Whether it is a customer at Stripe, whose billing an owner can manage. */
  customer: boolean
  /** The days of trial a plan bought now starts with, null for none. */
  trial_days: number | null
  /** The packs it may buy for this month: pages or questions, and how many. */
  packs: Pack[]
}

/** A pack of pages or questions for the month, paid once. */
export interface Pack {
  unit: 'pages' | 'questions'
  quantity: number
}

/** What an organisation may keep and use, null for no limit. */
export interface Allowance {
  projects: number | null
  documents: number | null
  storage_bytes: number | null
  pages_per_month: number | null
  questions_per_month: number | null
  members: number | null
  /** How many days the plan, or the terms agreed in its place, keeps a chat after its last question, null for good. */
  retention_days: number | null
  /** Whether its members may make API keys and call the API with them (backend/app/apikeys.py). */
  api_access: boolean
  /** Which of them were agreed with the organisation in place of its plan's. */
  agreed: string[]
  /** The pages and questions bought in packs for this month, on top of the plan's. */
  pages_bought: number
  questions_bought: number
  /** What it holds past them, which makes it read-only until it is within them again: everything is read, asked about and deleted, nothing added or changed. */
  over: Excess[]
}

/** One figure the plan sets, beside what the organisation holds or has used of it, as the server's checks count it. */
export interface Figure {
  used: number
  /** What it may, null for no limit. */
  allowed: number | null
  /** The pages or questions bought in packs for the month, on top of `allowed`. */
  bought: number
}

/** The pages the organisation's uploads brought in and the questions it asked over one UTC day, or one month from its first day. */
export interface Metered {
  start: string
  pages: number
  questions: number
}

export interface MeteredMonth extends Metered {
  pages_bought: number
  questions_bought: number
}

/** The figures a plan sets that the usage page shows: what is held now, then the month's. */
export type Figured = 'projects' | 'documents' | 'storage_bytes' | 'members' | 'pages_per_month' | 'questions_per_month'

/**
 * What the organisation holds and has used of what its plan allows, and its pages and questions
 * day by day and month by month, in UTC as the plan counts them (backend/app/api/usage.py).
 */
export type UsagePage = Record<Figured, Figure> & {
  /** The first day of the month the monthly figures count, and of the next, when they start afresh. */
  month: string
  next_month: string
  /** The characters of text a document without pages (a Word file, a sheet, a text file) counts a page for. */
  chars_per_page: number
  /** The sample project's documents, which `projects` and `documents` leave out; 0 once it is deleted. */
  sample_documents: number
  /** Every day of the last 30 up to today, and every month of the last 12 up to this one, the oldest first. */
  days: Metered[]
  months: MeteredMonth[]
}

/** The roles an organisation may let everyone at its domains join with. */
export type JoinRole = 'member' | 'viewer'

/** An organisation the user may join, since it lets everyone at their confirmed address's domain. */
export interface Joinable {
  id: number
  name: string
  role: JoinRole
}

/** An invitation still open, or expired and not sent again, as its organisation's owners and admins see it. */
export interface Invitation {
  id: number
  email: string
  role: Role
  /** Who sent it last, by name or address; null once their account is gone. */
  invited_by: string | null
  created_at: string
  expires_at: string
  expired: boolean
}

/** What an invitation's link is for, as its page shows it. */
export interface InvitationLink {
  organization: string
  role: Role
  /** The address it was sent to, which the account that accepts it must have. */
  email: string
  /** Who sent it, by name or address, '' once their account is gone. */
  invited_by: string
  expires_at: string
  expired: boolean
  /** Whether an account has the address, which then signs in to accept rather than signing up. */
  has_account: boolean
}

/** One of the organisations a user is a member of, which they can switch to. */
export interface Membership {
  id: number
  name: string
  role: Role
}

/** From most to least permitted: each role may do everything the roles after it may (ROLES in backend/app/models.py). */
export type Role = 'owner' | 'admin' | 'member' | 'viewer'
export const ROLES: Role[] = ['owner', 'admin', 'member', 'viewer']
export const ROLE_LABELS: Record<Role, string> = { owner: 'Owner', admin: 'Admin', member: 'Member', viewer: 'Viewer' }

/**
 * Whether `role` grants nothing `granted` does not: the roles someone may invite to, and whose
 * members' roles they may change (backend/app/api/invitations.py, backend/app/api/organizations.py).
 */
export const isWithin = (role: Role, granted: Role) => ROLES.indexOf(role) >= ROLES.indexOf(granted)

/** Whether the role may change what the organisation holds: every role but a viewer's (`get_editor` in backend/app/api/deps.py). */
export const mayChange = (role: Role) => isWithin('member', role)

/** Whether the role sees every project, the private ones whoever was chosen for them (`sees_every_project` in backend/app/access.py). */
export const seesEveryProject = (role: Role) => isWithin('admin', role)

/** A member of the organisation, as the other members see them. */
export interface Member {
  user_id: number
  name: string
  email: string
  role: Role
  joined_at: string
  /** Whether they sign in with two-factor, shown to the owners and admins alone; null for anyone else. */
  mfa_enabled: boolean | null
}

/** The kinds of notice (NOTICE_KINDS in backend/app/models.py, backend/app/notices.py). */
export type NoticeKind = 'upload_indexed' | 'quota' | 'project_export'

/** How one kind of notice is told: in the notification centre, by email, both or neither. */
export interface NoticeChoice {
  kind: NoticeKind
  app: boolean
  email: boolean
}

/** A notice in the user's notification centre. */
export interface NotificationItem {
  id: number
  kind: NoticeKind
  title: string
  body: string
  /** The page it leads to, a path in the organisation `org_id`, which may be another than the one the page shows. */
  link: string
  org_id: number
  /** The organisation's name as it is now. */
  organization: string
  created_at: string
  read_at: string | null
}

/** A page of the user's notices, the newest first, and how many of all of them are unread. */
export interface NotificationPage {
  notifications: NotificationItem[]
  unread: number
  /** What to pass as `before` for the next, older page; null on the last one. */
  next_before: number | null
}

/** The signed-in user, and the organisation and role they act with. */
export interface Me {
  id: number
  email: string
  name: string
  /** The IANA zone the mail Emudoc sends the user is dated in, '' for UTC until they choose one. */
  time_zone: string
  /** How the user is told each kind of notice, the news they may do without, in the server's order of the kinds. */
  notices: NoticeChoice[]
  email_verified: boolean
  mfa_enabled: boolean
  /** Whether the organisation requires two-factor sign-in and the account signs in without it; until it is set up, the app offers only that, a switch, leaving, and signing out. */
  mfa_due: boolean
  /** Providers the account can sign in through, such as "google". */
  identities: string[]
  organization: Organization
  role: Role
  /** Every organisation the user is a member of, the current one among them, by name. */
  organizations: Membership[]
  /** The organisations the domain of the user's confirmed address lets them join, by name. */
  joinable: Joinable[]
  /** Whether the account has a password, which then confirms its deletion. */
  has_password: boolean
  /** When the account will be deleted, if its user asked; until then the app offers only to keep it. */
  deletion_due_at: string | null
  /** The edition of the terms of service and privacy policy the user accepted, and when. */
  terms_version: string | null
  terms_accepted_at: string | null
  /** Whether the current edition waits to be accepted; until then the app offers only that, a copy of the data, and leaving. */
  terms_due: boolean
  /** Whether the user hid the projects page's list of first steps, ended the workspace's tour, and hid the chat's suggestion to invite their team, for every organisation. */
  checklist_hidden: boolean
  tour_seen: boolean
  invite_hidden: boolean
  /** How many of what's new's entries the user has not read, the newest ones, which the help menu marks. */
  changes_unread: number
  /** Until when the user lets Emudoc's support see their account in this organisation (backend/app/support.py), null while they do not. */
  support_until: string | null
  /** Set while one of Emudoc's operators views the account as support rather than the user signed in: read only, this organisation alone, until `ends_at`. */
  support_view: { ends_at: string } | null
}

/** The first steps the projects page lists (backend/app/onboarding.py), in its order. */
export type OnboardingStepKey = 'upload' | 'ask' | 'invite'

/** One of the organisation's first steps, and whether it took it. */
export interface OnboardingStep {
  key: OnboardingStepKey
  done: boolean
}

/** The first steps the member's role is offered: none for a viewer. */
export interface Onboarding {
  steps: OnboardingStep[]
}

/** The /terms and /privacy pages (backend/app/legal/), filled in from the deployment's settings. */
export interface Legal {
  /** The current edition, which the sign-up form and the acceptance page send back. */
  version: string
  /** Its date in words, "15 October 2026". */
  published: string
  /** The two documents, as Markdown. */
  terms: string
  privacy: string
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

/** What the audit log records: `AUDIT_ACTIONS` in backend/app/models.py. */
export type AuditAction =
  | 'sign_up'
  | 'sign_in'
  | 'sign_in_failed'
  | 'sign_in_paused'
  | 'sign_out'
  | 'session_revoked'
  | 'other_sessions_revoked'
  | 'password_changed'
  | 'password_reset_requested'
  | 'password_reset'
  | 'profile_changed'
  | 'email_change_requested'
  | 'email_changed'
  | 'mfa_enabled'
  | 'mfa_disabled'
  | 'account_exported'
  | 'terms_accepted'
  | 'account_deletion_requested'
  | 'account_deletion_cancelled'
  | 'account_purged'
  | 'support_access_granted'
  | 'support_access_revoked'
  | 'support_session_started'
  | 'support_session_ended'
  | 'role_changed'
  | 'plan_changed'
  | 'seats_changed'
  | 'entitlements_changed'
  | 'checkout_started'
  | 'subscription_changed'
  | 'subscription_cancelled'
  | 'cancellation_withdrawn'
  | 'payment_failed'
  | 'pack_bought'
  | 'organization_created'
  | 'organization_renamed'
  | 'organization_deletion_requested'
  | 'organization_deletion_cancelled'
  | 'settings_changed'
  | 'flag_changed'
  | 'audit_exported'
  | 'api_key_created'
  | 'api_key_rolled'
  | 'api_key_revoked'
  | 'ownership_transferred'
  | 'invitation_sent'
  | 'invitation_revoked'
  | 'invitation_accepted'
  | 'joining_changed'
  | 'member_joined'
  | 'member_left'
  | 'member_removed'
  | 'project_access_changed'
  | 'project_exported'
  | 'template_created'
  | 'template_changed'
  | 'template_deleted'
  | 'fact_checked'
  | 'fact_corrected'
  | 'fact_check_removed'
  | 'project_deleted'
  | 'project_restored'
  | 'project_purged'
  | 'sample_added'
  | 'folder_deleted'
  | 'folder_restored'
  | 'folder_purged'
  | 'document_uploaded'
  | 'document_downloaded'
  | 'document_deleted'
  | 'document_restored'
  | 'document_purged'
  | 'document_quarantined'
  | 'document_requeued'
  | 'chat_shared'
  | 'chat_unshared'
  | 'chat_deleted'
  | 'chat_restored'
  | 'chat_purged'
  | 'database_restored'

/** One row of the organisation's audit log, which its owners and admins read. */
export interface AuditEvent {
  id: number
  occurred_at: string
  action: AuditAction
  /** The member who acted, or whose account a sign-in was for; null for the operator or the app, and once the account is deleted. */
  user_id: number | null
  user_name: string | null
  user_email: string | null
  ip: string | null
  project_id: number | null
  /** The project's name as it is now, null once it is gone. */
  project_name: string | null
  /** What it was done to, which may be gone since. */
  target_type: string | null
  target_id: number | null
  /** Names and settings as they were, such as `{ filename: 'invoice.pdf' }`, the users a change let in or left out by id, and the API key a member acted through. */
  detail: Record<string, string | number | boolean | number[] | string[] | AuditKey | null>
}

/** The API key a row's member acted through, as it was then. */
export interface AuditKey {
  id: number
  name: string
}

/** What an API key may do (API_KEY_SCOPES in backend/app/models.py): read, or read and change, within its member's role. */
export type ApiKeyScope = 'read' | 'write'

/** One of the organisation's API keys, as its owners and admins see it (ApiKeyOut in backend/app/schemas.py). */
export interface ApiKey {
  id: number
  name: string
  scope: ApiKeyScope
  /** Its first characters, to tell it from the others; the key itself is shown once. */
  hint: string
  /** The member it acts as, by name or address as they are now. */
  user_id: number
  made_by: string
  created_at: string
  rolled_at: string | null
  last_used_at: string | null
  /** Null for a key that runs until it is revoked. */
  expires_at: string | null
  expired: boolean
}

/** A key just made or rolled, with the key itself, which nothing shows again. */
export interface ApiKeyMade extends ApiKey {
  key: string
}

/** Which rows of the audit log to read: of these actions (any, when none), by this member, about this project, from `since` until before `until` (ISO times). */
export interface AuditFilter {
  actions: AuditAction[]
  userId: number | null
  projectId: number | null
  since: string | null
  until: string | null
}

/** The query that asks for the rows a filter picks (`audit_filter` in backend/app/api/audit.py). */
export function auditQuery(filter: AuditFilter): URLSearchParams {
  const params = new URLSearchParams()
  for (const action of filter.actions) params.append('action', action)
  if (filter.userId !== null) params.set('user_id', String(filter.userId))
  if (filter.projectId !== null) params.set('project_id', String(filter.projectId))
  if (filter.since) params.set('since', filter.since)
  if (filter.until) params.set('until', filter.until)
  return params
}

/** The rows a filter picks as a CSV file to save, the newest first; a link, not a fetch, and the download is in the log. */
export const auditExportUrl = (filter: AuditFilter) => {
  const query = auditQuery(filter).toString()
  return `/api/audit/export${query ? `?${query}` : ''}`
}

/** A page of the audit log, the newest row first. */
export interface AuditPage {
  events: AuditEvent[]
  /** What to pass as `before` for the next, older page; null on the last one. */
  next_before: number | null
}

// The password rules the server applies (backend/app/schemas.py), so forms can say so first.
export const PASSWORD_MIN_LENGTH = 8
export const PASSWORD_MAX_LENGTH = 200
// How long a deleted account, or organisation, can still be kept (DELETION_GRACE in backend/app/retention.py),
// so the page can say so before the server sets the day.
export const DELETION_GRACE_DAYS = 14

export class ApiError extends Error {
  status: number
  /** On a 402, the figure of the plan it is held to, from which the page chooses what to offer. */
  limit: PlanLimit | null

  constructor(status: number, message: string, limit: PlanLimit | null = null) {
    super(message)
    this.status = status
    this.limit = limit
  }
}

// What a request that reached no server says: the browser knows it is offline, or the network
// or something on its way dropped the request or its answer.
const OFFLINE = 'Your browser is offline. Try again once the connection is back.'
const UNREACHABLE = 'Emudoc did not answer. Check your connection, then try again.'

/**
 * A request that reached no server, or whose answer never came back. fetch rejects with a
 * TypeError in the browser's own words ("Failed to fetch", "Load failed"); this says it in the
 * app's. A class of its own, not an `ApiError`, so that the queries' rule (both main.tsx)
 * retries it as it does a server's failure, and following an answer takes it for a dropped
 * connection.
 */
export class Unreachable extends Error {
  /** Whether the browser said it was offline, rather than the server or the way to it failing. */
  offline: boolean

  constructor(offline: boolean) {
    super(offline ? OFFLINE : UNREACHABLE)
    this.offline = offline
  }
}

/**
 * Whether the server refused because the organisation's plan does not allow it (402). The
 * message names the limit and says to upgrade, and `limit` which figure it is; the app shows
 * it as the way forward, not as a failure.
 */
export const isPlanLimit = (error: unknown): error is ApiError => error instanceof ApiError && error.status === 402

let onUnauthorized: (() => void) | null = null
let onSwitched: (() => void) | null = null
let onForbidden: (() => void) | null = null
let onPlanLimit: (() => void) | null = null
let shownOrganization: number | null = null
let lastFailure: FailedRequest | null = null

/**
 * What happens when the server no longer accepts the session: the app registers a handler
 * that returns to the sign-in page. Sign-in's own routes are left out, since a wrong password
 * or code answers 401 too and those are shown on the form.
 */
export function handleUnauthorized(handler: (() => void) | null) {
  onUnauthorized = handler
}

/**
 * What happens when the server refused a request because the page shows an organisation the
 * session no longer works in, since another tab switched: the app registers a handler that
 * starts it again in the one the session works in.
 */
export function handleSwitched(handler: (() => void) | null) {
  onSwitched = handler
}

/**
 * What happens when the server refused a request as not the account's to make (403): the
 * account may have changed since the page asked who is signed in, a role lowered by an admin,
 * terms to accept or a deletion asked for elsewhere, so the app registers a handler that asks
 * again, and the page stops offering what the server refuses. Sign-in's own routes are left
 * out, as for a 401.
 */
export function handleForbidden(handler: (() => void) | null) {
  onForbidden = handler
}

/**
 * What happens when the organisation's plan refused a request (402): what the page knows of the
 * plan may be old, the organisation lowered under what it holds and read-only since, so the
 * app registers a handler that asks again, and the page says so.
 */
export function handlePlanLimit(handler: (() => void) | null) {
  onPlanLimit = handler
}

/**
 * The last request of this page's that the API refused or failed, by the id its answer carried
 * (X-Request-ID, backend/app/logs.py): what a message to support attaches, for support to find
 * in the log what the member met. Null until one did.
 */
export function lastFailedRequest(): FailedRequest | null {
  return lastFailure
}

/** The organisation the page shows, which every request names from now on; null for none. */
export function showOrganization(id: number | null) {
  shownOrganization = id
}

// The header that names the organisation the page shows, and on a refusal the one the session
// works in (ORGANIZATION_HEADER in backend/app/api/deps.py).
const ORGANIZATION_HEADER = 'X-Organization'

/**
 * Every request carries these headers. The server refuses a change without the first, which is
 * what keeps a page on another site from making one with the user's cookie, and anything a tab
 * asks in an organisation other than the one the session works in now (backend/app/api/deps.py).
 */
async function send(path: string, init?: RequestInit, onProgress?: Progress): Promise<Response> {
  const headers = new Headers(init?.headers)
  headers.set('X-Requested-With', 'fetch')
  if (shownOrganization !== null) headers.set(ORGANIZATION_HEADER, String(shownOrganization))
  let response: Response
  try {
    const sent = { ...init, headers }
    response = await (onProgress ? sendReporting(`/api${path}`, sent, onProgress) : fetch(`/api${path}`, sent))
  } catch (error) {
    // An abort (a stop, a query no longer wanted) is the page's own doing and stays as it is.
    if (error instanceof TypeError) throw new Unreachable(globalThis.navigator?.onLine === false)
    throw error
  }
  const reference = response.headers.get('X-Request-ID')
  if (response.status >= 400 && reference) lastFailure = { reference, status: response.status }
  if (response.status === 401 && !path.startsWith('/auth/')) onUnauthorized?.()
  if (response.status === 403 && !path.startsWith('/auth/')) onForbidden?.()
  if (response.status === 402) onPlanLimit?.()
  if (response.status === 409 && response.headers.has(ORGANIZATION_HEADER)) onSwitched?.()
  return response
}

/**
 * A request that reports how far its body has gone, which fetch does not (a streamed body needs
 * HTTP/2 and Chromium): sent through XMLHttpRequest, and answered as fetch would answer, with a
 * Response, a TypeError for one that reached no server and an AbortError for an abort.
 */
function sendReporting(url: string, init: RequestInit & { headers: Headers }, onProgress: Progress): Promise<Response> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    const aborted = () => reject(new DOMException('The request was stopped.', 'AbortError'))
    xhr.open(init.method ?? 'GET', url)
    init.headers.forEach((value, name) => xhr.setRequestHeader(name, value))
    xhr.responseType = 'blob'
    xhr.upload.onprogress = (event) => onProgress(event.loaded, event.total)
    xhr.onload = () => {
      const headers = new Headers()
      for (const line of xhr.getAllResponseHeaders().split('\r\n')) {
        const colon = line.indexOf(':')
        if (colon > 0) headers.append(line.slice(0, colon), line.slice(colon + 1).trim())
      }
      const body = xhr.status === 204 || xhr.status === 304 ? null : (xhr.response as Blob)
      resolve(new Response(body, { status: xhr.status, statusText: xhr.statusText, headers }))
    }
    xhr.onerror = () => reject(new TypeError('The request reached no server.'))
    xhr.onabort = aborted
    if (init.signal?.aborted) return aborted()
    init.signal?.addEventListener('abort', () => xhr.abort(), { once: true })
    xhr.send(init.body as XMLHttpRequestBodyInit | null)
  })
}

/**
 * The error a refused response stands for: the sentence it carries as its `detail`, or the
 * fallback when it has none, and the plan's figure a 402 names.
 */
async function refusal(response: Response, fallback: string): Promise<ApiError> {
  try {
    const body = await response.json()
    const limit = typeof body.limit === 'string' ? (body.limit as PlanLimit) : null
    return new ApiError(response.status, typeof body.detail === 'string' ? body.detail : fallback, limit)
  } catch {
    // Not JSON: keep the fallback.
    return new ApiError(response.status, fallback)
  }
}

async function request<T>(path: string, init?: RequestInit, onProgress?: Progress): Promise<T> {
  const response = await send(path, init, onProgress)
  if (!response.ok) throw await refusal(response, response.statusText)
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
  /**
   * Mails `email` the link that makes the account. Answers alike whether or not the address has an
   * account; one that has is told so by mail instead.
   */
  signup: (email: string) => request<void>('/auth/signup', json('POST', { email })),
  /** The address a sign-up link was sent to; the link stays unused. */
  signupLink: (token: string) => request<{ email: string }>('/auth/signup/link', json('POST', { token })),
  /**
   * Makes the account a sign-up link was sent for and signs it in. `terms_version` is the edition the
   * page showed and its user accepted; `time_zone` the browser's.
   */
  finishSignup: (body: {
    token: string
    name: string
    password: string
    terms_version: string
    time_zone: string
  }) => request<Me>('/auth/signup/finish', json('POST', body)),
  /** What an invitation's link is for; an expired one too, a gone one is a 400. */
  invitation: (token: string) => request<InvitationLink>('/auth/invitation', json('POST', { token })),
  /** Joins the organisation an invitation to the signed-in account's address is to, and works in it. */
  acceptInvitation: (token: string) => request<Me>('/auth/invitation/accept', json('POST', { token })),
  /** Makes an account for an invitation's address, a member of that organisation alone, and signs it in. */
  signUpInvited: (body: { token: string; name: string; password: string; terms_version: string; time_zone: string }) =>
    request<Me>('/auth/invitation/signup', json('POST', body)),
  /** Accept the edition the page showed; a 409 says it changed meanwhile. */
  acceptTerms: (version: string) => request<Me>('/auth/terms', json('POST', { version })),
  /** With two-factor on, the session grants nothing until `verifyMfa` succeeds. */
  login: (body: { email: string; password: string }) =>
    request<{ mfa_required: boolean }>('/auth/login', json('POST', body)),
  verifyMfa: (code: string) => request<Me>('/auth/mfa/verify', json('POST', { code })),
  logout: () => request<void>('/auth/logout', { method: 'POST' }),

  /** A field left out stays as it is, and so does a way of a kind of notice; turning one on by email writes again to an address that bounced. */
  updateProfile: (body: {
    name?: string
    time_zone?: string
    notices?: { kind: NoticeKind; app?: boolean; email?: boolean }[]
  }) => request<Me>('/auth/profile', json('PATCH', body)),
  /** Hide the list of first steps or show it again, or say the tour was seen; a field left out stays as it is. */
  chooseOnboarding: (body: { checklist_hidden?: boolean; tour_seen?: boolean; invite_hidden?: boolean }) =>
    request<Me>('/auth/onboarding', json('PATCH', body)),
  /** Say the user has read what's new as it stands, for every organisation. */
  readChanges: () => request<Me>('/auth/changelog/read', { method: 'POST' }),
  /** Let Emudoc's support see the account in the organisation it works in for `days` from now, in place of a leave given before. */
  letSupportIn: (days: number) => request<Me>('/auth/support-access', json('PUT', { days })),
  /** Withdraw that leave, which also ends a view of the account by support. */
  keepSupportOut: () => request<Me>('/auth/support-access', { method: 'DELETE' }),
  /** Ends the view of a member's account the operator's session holds (backend/app/api/operator.py), from the banner the view shows. */
  endSupportView: () => request<void>('/operator/support', { method: 'DELETE' }),
  /**
   * Stops the email of the kind of notice a notice's link was made for, to its user, signed in
   * or not: the token in the link says whose and which. Mirrors `unsubscribe` in
   * backend/app/api/mail.py.
   */
  unsubscribe: (token: string) =>
    request<void>(`/mail/unsubscribe?${new URLSearchParams({ token })}`, { method: 'POST' }),
  /**
   * Mails the link that makes `email` the account's, confirmed by the password, or by the current
   * address for an account without one. Answers alike whether or not the address has an account.
   */
  requestEmailChange: (body: { email: string; password?: string; current_email?: string }) =>
    request<void>('/auth/email', json('POST', body)),
  confirmEmailChange: (token: string) => request<void>('/auth/email/confirm', json('POST', { token })),

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

  /** Confirmed by the password, or by the email address for an account without one. */
  deleteAccount: (body: { password?: string; email?: string }) =>
    request<Me>('/auth/account/deletion', json('POST', body)),
  keepAccount: () => request<Me>('/auth/account/deletion', { method: 'DELETE' }),
}

/**
 * Everything kept about the signed-in user, as a JSON file to save (`AccountExport` in
 * backend/app/schemas.py). A link, not a fetch: the browser downloads it.
 */
export const ACCOUNT_EXPORT_URL = '/api/auth/account/export'

/**
 * Where a sign-in with a provider starts. It is a navigation, not a fetch: the provider sends
 * the browser back to `next`, or to /login with `mfa=1` or an `error` to show. The browser's
 * time zone goes to an account the sign-in creates, and so does the invitation whose page it
 * started from, which such an account accepts (then the browser comes back to the front page).
 */
export function oauthStartUrl(provider: string, next: string, timeZone: string, invitation = '') {
  const params = new URLSearchParams({ next })
  if (timeZone) params.set('time_zone', timeZone)
  if (invitation) params.set('invitation', invitation)
  return `/api/auth/oauth/${provider}/start?${params}`
}

/**
 * The user's organisations: starting, switching and joining one by the domain of the address,
 * which the session keeps for every tab, and the current one's name, members and their roles,
 * owner, invitations, API keys, who may join it and leaving it. Mirrors backend/app/api/organizations.py,
 * backend/app/api/invitations.py and backend/app/api/apikeys.py; each change of the organisation answers with the
 * account as `auth.me` does.
 */
export const organizations = {
  start: (name: string) => request<Me>('/organizations', json('POST', { name })),
  switch: (id: number) => request<Me>(`/organizations/${id}/switch`, { method: 'POST' }),
  /** Owners and admins only: its name or its settings; what is left out stays, a null setting goes back to the deployment's or the plan's. */
  change: (body: OrganizationChange) => request<Me>('/organization', json('PATCH', body)),
  /** The languages its documents may be searched in, and the deployment's. */
  languages: () => request<Languages>('/organization/languages'),
  members: () => request<Member[]>('/organization/members'),
  /** Owners and admins only, for another member whose role is within their own, to a role within it. */
  changeRole: (userId: number, role: Role) => request<Member>(`/organization/members/${userId}`, json('PATCH', { role })),
  /** As `changeRole`; the member's private chats in its projects go, as when they leave. */
  removeMember: (userId: number) => request<void>(`/organization/members/${userId}`, { method: 'DELETE' }),
  /** Owners only, confirmed by the password, or by the email address for an account without one. */
  handOver: (body: { user_id: number; password?: string; email?: string }) =>
    request<Me>('/organization/owner', json('POST', body)),
  leave: () => request<Me>('/organization/leave', { method: 'POST' }),
  /** Owners only, confirmed as `handOver` is: deleted with everything in it once the grace period is over, every member told; a 409 while its subscription renews. */
  delete: (body: { password?: string; email?: string }) => request<Me>('/organization/deletion', json('POST', body)),
  /** Owners only: kept, as though nobody had asked. */
  keep: () => request<Me>('/organization/deletion', { method: 'DELETE' }),
  /** Owners and admins only, as is everything about invitations and joining. */
  invitations: () => request<Invitation[]>('/organization/invitations'),
  /** Mails the address its link; a 402 when the plan has no room, a 409 for a member or one invited already. */
  invite: (body: { email: string; role: Role }) => request<Invitation>('/organization/invitations', json('POST', body)),
  /** Mails a new link, which stops the one before. */
  resendInvitation: (id: number) => request<Invitation>(`/organization/invitations/${id}/resend`, { method: 'POST' }),
  revokeInvitation: (id: number) => request<void>(`/organization/invitations/${id}`, { method: 'DELETE' }),
  /** Owners and admins only, as is everything about API keys; none of these takes a key. */
  keys: () => request<ApiKey[]>('/organization/keys'),
  /** A key that acts as the member who makes it; a 402 when the plan does not include the API. */
  makeKey: (body: { name: string; scope: ApiKeyScope; days: number | null }) =>
    request<ApiKeyMade>('/organization/keys', json('POST', body)),
  /** The member's own key with a new secret; the one before stops at once. */
  rollKey: (id: number) => request<ApiKeyMade>(`/organization/keys/${id}/roll`, { method: 'POST' }),
  revokeKey: (id: number) => request<void>(`/organization/keys/${id}`, { method: 'DELETE' }),
  /** A domain is added only by someone whose own confirmed address is at it. */
  setJoining: (body: { domains: string[]; role: JoinRole }) => request<Me>('/organization/joining', json('PUT', body)),
  /** One of `me.joinable`. */
  join: (id: number) => request<Me>(`/organizations/${id}/join`, { method: 'POST' }),
  /** Owners only: the address of Stripe's Checkout page that sells the plan, with seats on Team. */
  checkout: (body: { plan: SoldPlan; seats?: number }) =>
    request<{ url: string }>('/organization/billing/checkout', json('POST', body)),
  /** Owners only: the address of Stripe's Checkout page that sells the pack for this month. */
  buyPack: (pack: Pack) => request<{ url: string }>('/organization/billing/packs', json('POST', pack)),
  /** The organisation on the plan, or with the pack, the Checkout session it came back from bought. */
  checkoutDone: (checkoutId: string) =>
    request<Me>('/organization/billing/checkout/done', json('POST', { checkout_id: checkoutId })),
  /** Owners only: the address of Stripe's customer portal for the organisation, or with a choice, of its one step that moves the subscription there (Free cancels it). */
  billingPortal: (choice?: PlanChoice) =>
    request<{ url: string }>('/organization/billing/portal', choice ? json('POST', choice) : { method: 'POST' }),
  /** Owners only: what moving the subscription to the plan would do, asked before Stripe's page. */
  planChange: ({ plan, seats }: PlanChoice) => {
    const params = new URLSearchParams({ plan })
    if (seats !== undefined) params.set('seats', String(seats))
    return request<PlanChange>(`/organization/billing/change?${params}`)
  },
  /** The organisation as its subscription stands at Stripe, once back from the portal. */
  portalDone: () => request<Me>('/organization/billing/portal/done', { method: 'POST' }),
  /** Every member: what the organisation holds and used of its plan, with the history. */
  usage: () => request<UsagePage>('/organization/usage'),
  /** The organisation's first steps that the member's role is offered, and which it took. */
  onboarding: () => request<Onboarding>('/organization/onboarding'),
}

/**
 * The user's notification centre, their notices about every organisation they belong to.
 * Mirrors backend/app/api/notifications.py.
 */
export const notifications = {
  page: (before: number | null = null) =>
    request<NotificationPage>(`/notifications${before === null ? '' : `?before=${before}`}`),
  /** The notices named, or every one. */
  read: (ids?: number[]) => request<void>('/notifications/read', json('POST', { ids })),
}

export const api = {
  status: () => request<SystemStatus>('/status'),
  /** Public: the readiness probe, which also names the version that answered. */
  health: () => request<Health>('/health'),
  /** Public: answers without a session. */
  subprocessors: () => request<Subprocessors>('/subprocessors'),
  /** Public: the terms of service and privacy policy. */
  legal: () => request<Legal>('/legal'),
  /** Public, since a sign-in page can fail too: the reference is the id the app logged the report under. */
  reportPageError: (report: PageError) =>
    request<{ reference: string | null }>('/page-errors', json('POST', report)),
  /** Public, since the sign-in pages lead to help too. */
  help: () => request<Help>('/help'),
  /** Public: what changed for members, the page of days before `before`, the newest first. */
  changelog: (before: string | null) =>
    request<Changelog>(`/changelog${before ? `?${new URLSearchParams({ before })}` : ''}`),
  /** Mails the member's message to support; a 429 says when they may write again. */
  sendFeedback: (feedback: Feedback) => request<FeedbackSent>('/feedback', json('POST', feedback)),
  /** Owners and admins only; anyone else gets a 403. The page older than the row `before`, of the rows the filter picks. */
  audit: (filter: AuditFilter, before: number | null = null, limit = 50) => {
    const params = auditQuery(filter)
    params.set('limit', String(limit))
    if (before != null) params.set('before', String(before))
    return request<AuditPage>(`/audit?${params}`)
  },

  projects: () => request<Project[]>('/projects'),
  project: (id: number) => request<Project>(`/projects/${id}`),
  /** A private project is seen by its maker alone at first, and by the owners and admins. */
  createProject: (body: { name: string; description: string; visibility: ProjectVisibility }) =>
    request<Project>('/projects', json('POST', body)),
  /** Who sees the project besides the owners and admins. */
  projectAccess: (id: number) => request<ProjectAccess>(`/projects/${id}/access`),
  /**
   * Whoever may change the project chooses who sees it; one who is not an owner or admin must
   * stay among a private project's members, and opening it forgets them.
   */
  setProjectAccess: (id: number, access: ProjectAccess) =>
    request<ProjectAccess>(`/projects/${id}/access`, json('PUT', access)),
  /** Into the organisation's trash, with everything in it. */
  deleteProject: (id: number) => request<void>(`/projects/${id}`, { method: 'DELETE' }),
  /** The member's own exports of the project that have not expired, the newest first. */
  projectExports: (id: number) => request<ProjectExport[]>(`/projects/${id}/exports`),
  /** A member or better asks; the worker makes it and tells them. A 409 while one is being made. */
  exportProject: (id: number) => request<ProjectExport>(`/projects/${id}/exports`, { method: 'POST' }),
  /** The organisation's projects in the trash, and how many days it keeps them. */
  trashedProjects: () => request<{ projects: TrashedProject[]; days: number }>('/trash'),
  projectTrash: (projectId: number) => request<ProjectTrash>(`/projects/${projectId}/trash`),
  /** Delete for good everything in the project's trash, the asker's own chats among it. */
  emptyTrash: (projectId: number) => request<void>(`/projects/${projectId}/trash`, { method: 'DELETE' }),
  /** Take something out of the trash; a project, a folder or a document is held to the plan. */
  restore: <T>(kind: TrashKind | 'project', id: number) =>
    request<T>(`/trash/${kind}s/${id}/restore`, { method: 'POST' }),
  /** Delete something in the trash for good, with its files. */
  purge: (kind: TrashKind | 'project', id: number) => request<void>(`/trash/${kind}s/${id}`, { method: 'DELETE' }),
  /** Without a stage, the project's partly indexed documents, each from the first stage it went without ("Index the rest"); with one, every document from it. */
  reprocessProject: (id: number, fromStage?: StageName) =>
    request<{ queued: number }>(`/projects/${id}/reprocess${fromStage ? `?from_stage=${fromStage}` : ''}`, {
      method: 'POST',
    }),

  /** The extraction templates in force for the project, its own first, and how many of its own it may have. */
  templates: (projectId: number) =>
    request<{ templates: ExtractionTemplate[]; max_templates: number }>(`/projects/${projectId}/templates`),
  createTemplate: (projectId: number, body: TemplateInput) =>
    request<ExtractionTemplate>(`/projects/${projectId}/templates`, json('POST', body)),
  updateTemplate: (id: number, body: TemplateInput) => request<ExtractionTemplate>(`/templates/${id}`, json('PUT', body)),
  /** For good; the built-in template of its kind, if any, applies again. */
  deleteTemplate: (id: number) => request<void>(`/templates/${id}`, { method: 'DELETE' }),
  /** Read the project's documents of a kind again for its template as it is now, from their summaries and facts. */
  readAgain: (projectId: number, body: ReadAgainInput) =>
    request<{ queued: number }>(`/projects/${projectId}/templates/read-again`, json('POST', body)),
  /** What the last reading of each of the project's documents of a kind changed, a page past `after`. */
  factChanges: (projectId: number, kind: string, after: string | null = null) =>
    request<FactChanges>(
      `/projects/${projectId}/fact-changes?${new URLSearchParams(after ? { kind, after } : { kind })}`,
    ),
  /** A page of the facts table of the project's documents of one kind, as `tableQuery` in factTable.ts asks for it. */
  factTable: (projectId: number, query: URLSearchParams) => request<FactTable>(`/projects/${projectId}/fact-table?${query}`),
  /** Check a fact as it reads, or correct it to `value`; the member's value replaces the model's wherever the fact is read. */
  checkFact: (id: number, body: FactCheckInput) => request<Fact>(`/facts/${id}/check`, json('PUT', body)),
  /** Take a check back: its fact reads as the model read it again. */
  removeCheck: (id: number) => request<void>(`/fact-checks/${id}`, { method: 'DELETE' }),

  folders: (projectId: number) => request<Folder[]>(`/projects/${projectId}/folders`),
  createFolder: (projectId: number, body: { name: string; parent_id: number | null }) =>
    request<Folder>(`/projects/${projectId}/folders`, json('POST', body)),
  updateFolder: (id: number, body: { name?: string; parent_id?: number | null }) =>
    request<Folder>(`/folders/${id}`, json('PATCH', body)),
  deleteFolder: (id: number) => request<void>(`/folders/${id}`, { method: 'DELETE' }),

  /**
   * A page of a folder's documents (the root's for null) past `after`, with the ETag that
   * names it; null when `held`, the tag of the page the caller has, still names it.
   */
  documentPage: async (
    projectId: number,
    folderId: number | null,
    after: string | null,
    held: string | null = null,
    signal?: AbortSignal,
  ): Promise<(DocumentPage & { etag: string | null }) | null> => {
    const params = new URLSearchParams()
    if (folderId != null) params.set('folder_id', String(folderId))
    if (after != null) params.set('after', after)
    const query = params.size ? `?${params}` : ''
    const headers: Record<string, string> = held ? { 'If-None-Match': held } : {}
    const response = await send(`/projects/${projectId}/documents${query}`, { headers, signal })
    if (response.status === 304) return null
    if (!response.ok) throw await refusal(response, response.statusText)
    const page: DocumentPage = await response.json()
    return { ...page, etag: response.headers.get('ETag') }
  },
  /**
   * One request of an upload into a folder (`sendInBatches` in workspace/uploads.ts makes several
   * of one that is more than a request may carry), each file into the folder its `folder` names
   * below it, which the server makes where none of its name is.
   */
  upload: (
    projectId: number,
    items: UploadItem[],
    folderId: number | null,
    sending: { onProgress?: Progress; signal?: AbortSignal } = {},
  ) => {
    const form = new FormData()
    for (const { file } of items) form.append('files', file)
    // Each file's folder, sent when any is in one, as the files of an uploaded folder are.
    if (items.some(({ folder }) => folder)) for (const { folder } of items) form.append('paths', folder)
    const query = folderId == null ? '' : `?folder_id=${folderId}`
    const init = { method: 'POST', body: form, signal: sending.signal }
    return request<UploadResult>(`/projects/${projectId}/documents${query}`, init, sending.onProgress)
  },
  /** How far the documents of the project's uploads named are indexed (`MAX_FOLLOWED` of them a request). */
  uploadStatus: (projectId: number, ids: number[], signal?: AbortSignal) =>
    request<UploadStatus>(
      `/projects/${projectId}/uploads?${new URLSearchParams(ids.map((id) => ['ids', String(id)]))}`,
      { signal },
    ),
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
  /** Add a question; the worker writes the answer, which `streamEvents` follows. */
  ask: (chatId: number, content: string) => request<AskResult>(`/chats/${chatId}/messages`, json('POST', { content })),
  /** End an answer with what arrived so far. */
  stopAnswer: (chatId: number, messageId: number) =>
    request<ChatMessage>(`/chats/${chatId}/messages/${messageId}/stop`, { method: 'POST' }),
  /** Write the chat's last answer again in its place, once it was stopped or failed. */
  retryAnswer: (chatId: number, messageId: number) =>
    request<RetryResult>(`/chats/${chatId}/messages/${messageId}/retry`, { method: 'POST' }),
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

/** An event of an answer, with the id the server gave it; `end` has none. */
export interface NumberedEvent {
  seq: number | null
  event: ChatEvent
}

/**
 * Follow an answer's events as the server streams them, from the start or past the event
 * with id `after`. The stream ends after an `end` event; ending without one means the
 * connection dropped and the caller follows again from the last id it saw.
 */
export async function* streamEvents(
  chatId: number,
  messageId: number,
  after: number | null,
  signal: AbortSignal,
): AsyncGenerator<NumberedEvent> {
  const headers: Record<string, string> = after == null ? {} : { 'Last-Event-ID': String(after) }
  const response = await send(`/chats/${chatId}/messages/${messageId}/events`, { headers, signal })
  if (!response.ok || !response.body) {
    throw await refusal(response, 'The answer could not be followed.')
  }
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader()
  let buffer = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) return
    buffer += value
    // Server-sent events are separated by a blank line; each is an id line and a data line.
    // A block of comment lines (`: keep-alive`, sent while the server has nothing to say so
    // that no proxy ends the idle connection) has neither and yields nothing.
    let end: number
    while ((end = buffer.indexOf('\n\n')) >= 0) {
      const block = buffer.slice(0, end)
      buffer = buffer.slice(end + 2)
      let seq: number | null = null
      let data: string | null = null
      for (const line of block.split('\n')) {
        if (line.startsWith('id: ')) seq = Number(line.slice(4))
        else if (line.startsWith('data: ')) data = line.slice(6)
      }
      if (data !== null) yield { seq, event: JSON.parse(data) as ChatEvent }
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
