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

export type UsageKind =
  | 'answer'
  | 'upload'
  | 'ocr'
  | 'infer_sections'
  | 'enrich'
  | 'embed'
  | 'render'
  | 'report'
  | 'draft'
  | 'suggestions'

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
  /** The sample project a new organisation starts with (backend/app/sample): Emudoc's own documents, which the usage page leaves out and which take no uploads. */
  sample: boolean
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

/** What a fact's value is (FACT_TYPES in backend/app/models.py). */
export type FactType = 'text' | 'number' | 'date' | 'money'

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
  /** How many documents the member chose in the library, for an export of those alone; null for the whole project. */
  chosen: number | null
}

/** What became of the documents chosen to be indexed again (DocumentsReprocessOut in backend/app/schemas.py). */
export interface DocumentsReprocessed {
  /** Those queued, as they stand now. */
  documents: Document[]
  /** How many were being indexed already, and were left as they are. */
  skipped: number
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
  /** Its title, type and date as everything reads them: a member's where one set it, else what its reading found. */
  title: string | null
  doc_type: string | null
  doc_date: string | null
  /** The text search configuration its words are searched in, null for its organisation's (backend/app/languages.py). */
  language: string | null
  /** What its last reading found of the four above, and which of them a member set in its place (backend/app/details.py). */
  read_title: string | null
  read_doc_type: string | null
  read_doc_date: string | null
  read_language: string | null
  edited_details: DocumentDetail[]
  summary: string | null
  /** Its current version's (backend/app/versions.py). */
  usage: Partial<Record<UsageKind, Usage>>
  created_at: string
  /** Which version of the document its file is, from 1 (backend/app/versions.py). */
  version: number
  /** Its view, the PDF of a Word, PowerPoint, Excel, RTF or HTML file that shows it as it looks (backend/app/conversion.py): being made, made, or failed with the sentence why in `view_error`; null for none. */
  view_state: ViewState | null
  view_error: string | null
}

export type ViewState = 'queued' | 'ready' | 'failed'

/** DOCUMENT_DETAILS in backend/app/models.py: what a member may set in place of what a document's reading found. */
export type DocumentDetail = 'title' | 'doc_type' | 'doc_date' | 'language'

/**
 * A change to a document's details (backend/app/details.py): a field given is the member's, null for none, which later
 * readings keep; a field left out stays; one in `as_read` takes what the reading found again.
 */
export interface DocumentDetailsInput {
  title?: string | null
  doc_type?: string | null
  doc_date?: string | null
  /** One of the organisation's languages (`organizations.languages`), null for the organisation's own. */
  language?: string | null
  as_read?: DocumentDetail[]
}

/** A document's note (backend/app/notes.py), '' for none, with the name of whoever changed it last as it is now and when. */
export interface DocumentNote {
  text: string
  changed_by: string | null
  changed_at: string | null
}

/** A document of the same bytes as another, in another project the member sees (backend/app/duplicates.py). */
export interface DocumentCopy {
  id: number
  project_id: number
  project_name: string
  /** Its folder's path from the project's root, '' for the root. */
  folder: string
  folder_id: number | null
  filename: string
}

/** A file held in more than one of the projects the member sees, with every copy of it by project name. */
export interface Duplicate {
  size_bytes: number
  mime_type: string
  copies: DocumentCopy[]
}

/** A page of the duplicates report, with how many files there are and the bytes their copies past the first take. */
export interface DuplicatesPage {
  files: number
  copy_bytes: number
  duplicates: Duplicate[]
  /** The `after` of the page after it; null on the last. */
  next: number | null
}

/** The orders the library lists documents in (`SORTS` in backend/app/api/documents.py): by name, as added, by the documents' own dates (the undated after the dated either way), by size. */
export type DocumentSort = 'name' | 'added' | 'date' | 'size'
export type SortOrder = 'asc' | 'desc'

/**
 * Where the library lists documents (`list_documents` in backend/app/api/documents.py): directly in a folder, the root's
 * for null; or wherever they are in the project, the reader's favourites, or the documents they opened lately, newest
 * first whatever the order; each of those narrowed to the files whose names hold `name`, which alone is every such file of
 * the project's.
 */
export type ListingPlace =
  | { folder: number | null }
  | { favorites: true; name?: string }
  | { recent: true; name?: string }
  | { name: string }

/**
 * What a search searches within (DocumentFilter in backend/app/schemas.py), each part narrowing the others; a part left
 * empty matches every document.
 */
export interface DocumentFilter {
  types: string[]
  /** Dated on or after, and on or before, as `YYYY-MM-DD`; either leaves out the undated. */
  after: string | null
  before: string | null
  /** A folder with its subfolders. */
  folder_id: number | null
  /** The documents with a passage that holds every word of each: what the earlier searches of a search within found. */
  mentions: string[]
}

/** A type the model gave a project's documents, and how many of them out of the trash. */
export interface DocumentType {
  type: string
  count: number
}

/** A version of a document (backend/app/versions.py): its current file, or an earlier one kept to open and download. */
export interface DocumentVersion {
  number: number
  current: boolean
  filename: string
  mime_type: string
  size_bytes: number
  page_count: number | null
  uploaded_at: string
  /** Who brought it in, by their name as it is now; null for nobody's or an account gone. */
  uploaded_by: string | null
  /** Whether its file is served: the malware scan passed it. */
  served: boolean
  /** Whether its text was read, which an earlier version shows. */
  has_text: boolean
}

/** A page of the documents directly in one folder, in the library's order (by name). */
export interface DocumentPage {
  documents: Document[]
  /** What to pass as `after` for the next page; null on the last one. */
  next_after: string | null
}

export interface UploadResult {
  documents: Document[]
  /**
   * The files left out, each with why. The server also gives each its place in the request (`index`) and the document
   * holding the same bytes when that was why (`duplicate_of`), for a program; the page names a file by its path, and
   * leaves both out of those it leaves out itself.
   */
  rejected: { filename: string; reason: string; index?: number | null; duplicate_of?: number | null }[]
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
  /** The extensions the server reads, which the upload client leaves the others out by; the page knows them as ACCEPTED_EXTENSIONS. */
  extensions: string[]
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
  /** The pages of its view, for a document shown as it looks (backend/app/conversion.py); empty until the view is made. */
  view_page_sizes: [number, number][]
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
  value_type: FactType
  value: string
  number: number | null
  date: string | null
  currency: string | null
  quote: string | null
  page: number | null
  block_ordinal: number | null
  passage_id: number | null
  /** Whether the model's quote was found in the text. */
  verified: boolean
}

/** A project's address as its members set it (MailboxIn): whom it takes mail from besides themselves, each an address or a domain, and the folder what arrives goes into, the root when null. */
export interface MailboxInput {
  senders: string[]
  folder_id: number | null
}

/** A message sent to the project's address and what became of it (InboundMessageOut): the documents kept, the files that were not, or why it was turned away. */
export interface InboundMessage {
  id: number
  received_at: string
  sender: string
  /** The member whose address sent it, by their name; null for anyone else. */
  member: string | null
  subject: string
  outcome: 'kept' | 'refused' | 'empty'
  reason: string
  kept: number
  rejected: { filename: string; reason: string }[]
}

/** A project's address, or that it has none (`address` null), with what it received lately, newest first (MailboxOut); `available` is whether the deployment receives mail at all. */
export interface Mailbox extends MailboxInput {
  available: boolean
  address: string | null
  /** The folder's path; null for the project's root, which a folder gone or in the trash falls back to. */
  folder: string | null
  max_senders: number
  days: number
  messages: InboundMessage[]
}

/** A service folders can be connected from (backend/app/connectors/). */
export type ConnectorProvider = 'google' | 'dropbox' | 'onedrive'

/** A folder of a member's Google Drive, Dropbox or OneDrive that the project keeps in step with (ConnectorOut). */
export interface Connector {
  id: number
  provider: ConnectorProvider
  label: string
  /** The account at the service it was connected from, and the member who connected it, by their name. */
  account: string
  member: string
  /** Whether it is the reader's own: only its member chooses its folder or connects it again. */
  mine: boolean
  status: 'choosing' | 'active' | 'paused'
  /** The folder's name at the service; null while it is being chosen. */
  folder: string | null
  target_folder_id: number | null
  /** Where its files go, the folder's path; null for the project's root. */
  target: string | null
  /** Why the last sync did not finish, or why it is paused; empty when all went well. */
  problem: string
  synced_at: string | null
  next_sync_at: string | null
  syncing: boolean
  /** The documents of its files in the library. */
  kept: number
  /** The files that are no document and why, the first of them, and how many in all. */
  rejected: { path: string; reason: string }[]
  rejected_count: number
}

/** The project's connected folders, and the services another may be connected from (ConnectorsOut). */
export interface Connectors {
  services: { provider: ConnectorProvider; label: string }[]
  connectors: Connector[]
  max_connectors: number
  interval_minutes: number
}

/** A folder at the service, to browse into or choose; `choosable` is false for a place browsing passes through ("Shared with me"). */
export interface RemoteFolder {
  ref: string
  name: string
  choosable: boolean
}

/** What a file of a member's My files is (MY_FILE_KINDS in backend/app/models.py). */
export type MyFileKind = 'docx' | 'xlsx'
/** Where a version stands with the malware scan: only `passed` and `skipped` are served, drawn or copied. */
export type MyFileScan = 'pending' | 'passed' | 'skipped' | 'flagged'

/** A version of a file in My files (MyFileVersionOut): the agent's writing or the member's upload, what changed, its scan and its view. */
export interface MyFileVersion {
  number: number
  size_bytes: number
  by_model: boolean
  note: string
  scan: MyFileScan
  view_state: 'queued' | 'ready' | 'failed' | null
  view_page_sizes: [number, number][]
  view_error: string | null
  created_at: string
}

/** A file of the member's own in a project (MyFileOut, backend/app/my_files.py), seen by them alone: its newest version, how many it has, and the library's document it was copied from while that is there. */
export interface MyFile {
  id: number
  project_id: number
  name: string
  kind: MyFileKind
  /** The member's folder it is in, null for the top of their My files. */
  folder_id: number | null
  origin_document_id: number | null
  origin_name: string | null
  latest: MyFileVersion
  versions: number
  created_at: string
  updated_at: string
}

/** A file with every version, the newest first (MyFileDetailOut). */
export interface MyFileDetail extends MyFile {
  history: MyFileVersion[]
}

/** The files a text citing passages downloads as: an answer, a chat (backend/app/draft_export.py, backend/app/chat_export.py). */
export type TextFormat = 'docx' | 'pdf' | 'md'

/** What an upload into My files kept, in the order sent, and what it refused, each by its path below the folder chosen (MyFilesAddedOut). */
export interface MyFilesAdded {
  files: MyFileDetail[]
  rejected: UploadResult['rejected']
}

/** A folder of the member's own My files (MyFolderOut): its name and the folder it is in, null for the top. */
export interface MyFolder {
  id: number
  parent_id: number | null
  name: string
}

/** The member's files in a project, the one changed last first, their folders by name, and how many files it keeps (MyFilesOut). */
export interface MyFiles {
  files: MyFile[]
  folders: MyFolder[]
  limit: number
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

/** The library's search as its route asks it (SearchIn in backend/app/schemas.py): each part of the filter given, a list's each value once. */
export function searchParams(query: string, filter: DocumentFilter, limit = 20): URLSearchParams {
  const params = new URLSearchParams({ q: query, limit: String(limit) })
  for (const type of filter.types) params.append('types', type)
  if (filter.after) params.set('after', filter.after)
  if (filter.before) params.set('before', filter.before)
  if (filter.folder_id != null) params.set('folder_id', String(filter.folder_id))
  for (const words of filter.mentions) params.append('mentions', words)
  return params
}

/** A passage the search of every project the member sees found (SearchAllHitOut), with the project it is in by its name now. */
export interface SearchAllHit extends SearchHit {
  project_id: number
  project_name: string
}

export interface SearchAllResult {
  hits: SearchAllHit[]
  semantic: boolean
}

/** What narrows the search of every project the member sees (SearchAllIn): some of them, the documents' types, a span of their dates; a part left empty takes everything. */
export interface SearchAllFilter {
  projects: number[]
  types: string[]
  /** As `YYYY-MM-DD`; either leaves out the undated. */
  after: string | null
  before: string | null
}

/** The search of every project as its route asks it: each part of the filter given, a list's each value once. */
export function searchAllParams(query: string, filter: SearchAllFilter, limit = 20): URLSearchParams {
  const params = new URLSearchParams({ q: query, limit: String(limit) })
  for (const id of filter.projects) params.append('projects', String(id))
  for (const type of filter.types) params.append('types', type)
  if (filter.after) params.set('after', filter.after)
  if (filter.before) params.set('before', filter.before)
  return params
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
  /** Whether Word, PowerPoint, Excel, RTF and HTML documents are shown as they look (backend/app/conversion.py); without, by their text. */
  views: boolean
  /** Whether projects may take mail at an address of their own (INBOUND_DOMAIN, backend/app/inbound.py), and whether a folder may be connected from a service the deployment has an OAuth client for (backend/app/connectors/): the page offers neither without. */
  email_in: boolean
  connectors: boolean
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

/** Where members write for help (backend/app/api/help.py): SUPPORT_EMAIL, or none where the help menu writes to the workspace's owners and admins. */
export interface Help {
  support_email: string | null
  /** The status page outside the deployment, which says whether it is up; none where it names none. */
  status_page: string | null
  /** The install mails something; without, the menu sends nothing and says whom to write to. */
  mail: boolean
  /** Where the running code's source is, which the AGPL has the app offer: SOURCE_URL, else the public repository at the running release. */
  source: string
}

/** What a member writes about from the help menu, which the mail's subject says. */
export type FeedbackKind = 'problem' | 'question' | 'idea'

/** A request the API refused or failed, by the reference its answer carried, under which the log keeps it. */
export interface FailedRequest {
  reference: string
  status: number
}

/**
 * A message from the help menu: the member's words, and where they were, as the page's
 * path with the ids its query names, never its words (src/help/feedback.ts).
 */
export interface Feedback {
  kind: FeedbackKind
  message: string
  page: string
  failed: FailedRequest | null
}

/** A message posted from the help menu: the reference it went under, for the member to quote, and the address an answer comes from, null for the workspace's owners and admins. */
export interface FeedbackSent {
  reference: string
  to: string | null
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

/** How many documents a chat may be about: more is a folder's work. */
export const SCOPE_DOCUMENTS = 20

/**
 * What a chat is about, which its answers read alone: the whole project, a folder with its
 * subfolders, or documents chosen from it. The folder is null once it was deleted or put in the
 * trash, and the documents leave out those that were.
 */
export interface ChatScope {
  kind: 'project' | 'folder' | 'documents'
  folder: { id: number; name: string } | null
  documents: { id: number; filename: string }[]
}

/** What a chat is made about: a folder, or documents; neither is the whole project. */
export type ScopeIn = { folder_id: number } | { document_ids: number[] } | Record<string, never>

export interface Chat {
  id: number
  project_id: number
  /** Who started the chat, and their name: a shared chat is theirs alone to ask in, rename and delete. */
  user_id: number
  started_by: string
  title: string
  visibility: ChatVisibility
  scope: ChatScope
  /** Whether the reader pinned it to the top of their chat list: each reader's own. */
  pinned: boolean
  created_at: string
  updated_at: string
}

/** A file of the asker's My files that a step wrote (backend/app/agent/writing.py), which the step opens. */
export interface StepFile {
  id: number
  name: string
  version: number
}

/** One tool call the agent made while answering. */
export interface ChatStep {
  id: string
  name: string
  input: Record<string, unknown>
  summary: string
  is_error: boolean
  file?: StepFile | null
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

/** The chat's last question, edited or not, and its answer written again, followed as a try's is. */
export interface RegenerateResult extends AskResult {
  last_event_id: number | null
}

export type ChatEvent =
  | { type: 'text'; text: string }
  | { type: 'thinking' }
  | { type: 'retry' }
  | { type: 'restart' }
  | { type: 'tool_call'; id: string; name: string; input: Record<string, unknown> }
  | { type: 'tool_result'; id: string; summary: string; is_error: boolean; file?: StepFile }
  | { type: 'sources'; sources: Source[] }
  | { type: 'notice'; message: string }
  | { type: 'error'; message: string }
  | { type: 'done'; usage: Usage }
  // Sent by the events route itself, last, once the answer is settled.
  | { type: 'end'; status: MessageStatus }

export interface Organization {
  id: number
  name: string
  slug: string
  /** Its settings, as its owners and admins chose them: the language its documents are searched in, null for the deployment's (backend/app/languages.py); the days it keeps a chat after its last question (backend/app/retention.py) and an audit log entry, null until they are deleted; how its members are told each kind of notice until they choose. */
  language: string | null
  chat_days: number | null
  audit_days: number | null
  notice_defaults: NoticeChoice[]
  /** Its security settings (backend/app/security.py): whether everyone who works in it signs in with two-factor, and the days a session may work in it after signing in, null for as long as the deployment keeps one, which is the most it may choose. */
  mfa_required: boolean
  session_days: number | null
  max_session_days: number
  /** Whether its members may share a document by a link that needs no account (backend/app/share_links.py); off, every link it has stops. */
  share_links: boolean
}

/** What the organisation page changes about the organisation (OrganizationChangeIn in backend/app/schemas.py). */
export interface OrganizationChange {
  name?: string
  language?: string | null
  chat_days?: number | null
  audit_days?: number | null
  notices?: { kind: NoticeKind; app?: boolean; email?: boolean }[]
  /** Turned on only by someone who signs in with two-factor themselves. */
  mfa_required?: boolean
  session_days?: number | null
  /** Off, every share link the organisation has stops. */
  share_links?: boolean
}

/** The text search configurations the database has, which an organisation may search its documents in, and the deployment's. */
export interface Languages {
  languages: string[]
  default: string
}

/** What the workspace used over one UTC day, or one month from its first day: the pages its uploads brought in, the questions it asked, and what all its work cost, null to members and while nothing has a known cost. */
export interface Metered {
  start: string
  pages: number
  questions: number
  cost_micros: number | null
}

/** What the workspace holds now, and what it used, day by day and month by month, in UTC (backend/app/api/usage.py). */
export interface UsagePage {
  /** The first day of the month the last of `months` counts, and of the next. */
  month: string
  next_month: string
  /** What it holds now: its projects and documents, their bytes with its members' My files, and its members with the open invitations. */
  projects: number
  documents: number
  storage_bytes: number
  members: number
  /** The characters of text a document without pages (a Word file, a sheet, a text file) counts a page for. */
  chars_per_page: number
  /** The sample project's documents, which `projects` and `documents` leave out; 0 once it is deleted. */
  sample_documents: number
  /** Every day of the last 30 up to today, and every month of the last 12 up to this one, the oldest first. */
  days: Metered[]
  months: Metered[]
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

/**
 * An invitation just made or sent again, with its link for whoever sent it to copy and hand on: the one way it
 * arrives where the install has no mail relay (`mailed` false). Never shown again, since only its hash is kept.
 */
export interface InvitationSent extends Invitation {
  link: string
  mailed: boolean
}

/** A link that sets a member's password, for an owner or admin to hand on, and when it stops working. */
/** A link an owner or admin makes for a member to hand on: one that sets their password, or one that moves their account to a new address. */
export interface HandedLink {
  link: string
  expires_at: string
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
  /** When one of their sessions was last seen, shown to the owners and admins alone; null for anyone else, and for no session kept. */
  last_seen_at: string | null
}

/** The kinds of notice (NOTICE_KINDS in backend/app/models.py, backend/app/notices.py). */
export type NoticeKind = 'upload_indexed' | 'project_export'

/** The kinds of notice no longer told, whose notices stay in the centre to read (PAST_NOTICE_KINDS in backend/app/models.py). */
export type PastNoticeKind = 'mentioned' | 'thread_replied' | 'review_assigned' | 'report_ready' | 'weekly_digest' | 'checks_run'

/** How one kind of notice is told: in the notification centre, by email, both or neither. */
export interface NoticeChoice {
  kind: NoticeKind
  app: boolean
  email: boolean
}

/** A notice in the user's notification centre. */
export interface NotificationItem {
  id: number
  kind: NoticeKind | PastNoticeKind
  title: string
  body: string
  /** The page it leads to, a path of the app. */
  link: string
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
  /** Whether the workspace requires two-factor sign-in and the account signs in without it; until it is set up, the app offers only that and signing out. */
  mfa_due: boolean
  /** Providers the account can sign in through, such as "google". */
  identities: string[]
  organization: Organization
  role: Role
  /** Whether the account has a password, which then confirms its deletion. */
  has_password: boolean
  /** When the account will be deleted, if its user asked; until then the app offers only to keep it. */
  deletion_due_at: string | null
  /** Whether the user hid the projects page's list of first steps and ended the workspace's tour, for every organisation. */
  checklist_hidden: boolean
  tour_seen: boolean
}

/** A signed-in user an admin removed from the workspace: in none until invited again (backend/app/api/deps.py), which is all the app shows them. */
export type Outsider = Omit<Me, 'organization' | 'role'> & { organization: null; role: null }

/** Whoever is signed in: a member of the workspace, or one removed from it. */
export type SignedIn = Me | Outsider

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

export interface OAuthProvider {
  name: string
  label: string
}

/** What the sign-in pages can offer besides a password, and whether anyone may sign up. */
export interface AuthOptions {
  providers: OAuthProvider[]
  /** Closed, the sign-in pages say to ask the team's admin for an invitation. */
  signup_open: boolean
  /** The install has no account yet: every page sends to the setup, `/setup`. */
  setup_needed: boolean
  /** The install mails something; without, a forgotten password's link, or one that moves an account, is asked of an owner or admin, and notices are told in the app alone. */
  mail: boolean
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
  | 'webhook_created'
  | 'webhook_changed'
  | 'webhook_rolled'
  | 'webhook_deleted'
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
  | 'facts_exported'
  | 'cross_check_created'
  | 'cross_check_changed'
  | 'cross_check_deleted'
  | 'cross_checks_run'
  | 'check_report_requested'
  | 'check_run_deleted'
  | 'automation_created'
  | 'automation_changed'
  | 'automation_deleted'
  | 'question_set_created'
  | 'question_set_changed'
  | 'question_set_deleted'
  | 'question_set_run'
  | 'question_report_deleted'
  | 'filing_rule_created'
  | 'filing_rule_changed'
  | 'filing_rule_deleted'
  | 'draft_created'
  | 'draft_changed'
  | 'draft_deleted'
  | 'draft_exported'
  | 'draft_template_created'
  | 'draft_template_changed'
  | 'draft_template_deleted'
  | 'mailbox_created'
  | 'mailbox_changed'
  | 'mailbox_deleted'
  | 'connector_created'
  | 'connector_changed'
  | 'connector_deleted'
  | 'project_deleted'
  | 'project_restored'
  | 'project_purged'
  | 'sample_added'
  | 'folder_deleted'
  | 'folder_restored'
  | 'folder_purged'
  | 'document_uploaded'
  | 'document_version_uploaded'
  | 'document_downloaded'
  | 'document_deleted'
  | 'document_restored'
  | 'document_purged'
  | 'document_quarantined'
  | 'document_requeued'
  | 'document_review_changed'
  | 'document_assigned'
  | 'share_link_created'
  | 'share_link_revoked'
  | 'share_link_viewed'
  | 'share_link_downloaded'
  | 'chat_shared'
  | 'chat_unshared'
  | 'chat_exported'
  | 'answer_exported'
  | 'chat_deleted'
  | 'chat_restored'
  | 'chat_purged'
  | 'comment_added'
  | 'comment_deleted'
  | 'thread_resolved'
  | 'thread_reopened'
  | 'database_restored'
  | 'my_file_added'
  | 'my_file_version_added'
  | 'my_file_renamed'
  | 'my_file_deleted'
  | 'my_file_downloaded'
  | 'my_file_copied'
  | 'reset_link_made'
  | 'provider_switched'
  | 'email_link_made'
  | 'my_folder_created'
  | 'my_folder_renamed'
  | 'my_folder_deleted'
  | 'my_file_moved'

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

/** What a webhook is told of (WEBHOOK_EVENTS in backend/app/models.py), and how a delivery to it stands. */
export type WebhookEvent = 'document.ready' | 'document.failed'
export type WebhookDeliveryStatus = 'pending' | 'delivered' | 'failed'

/** An event sent to a webhook, as its delivery log shows it (WebhookDeliveryOut in backend/app/schemas.py): never what it said. */
export interface WebhookDelivery {
  id: number
  event_id: string
  event_type: WebhookEvent
  /** The document's or the run's id. */
  subject_id: number
  status: WebhookDeliveryStatus
  attempts: number
  /** How the last try ended: the address's status, and the sentence why it was no delivery. */
  response_status: number | null
  error: string | null
  created_at: string
  attempted_at: string | null
  retry_at: string | null
  delivered_at: string | null
}

/** One of the organisation's webhooks, as its owners and admins see it (WebhookOut): never its secret. */
export interface Webhook {
  id: number
  url: string
  events: WebhookEvent[]
  enabled: boolean
  /** Who added it, by name or address as they are now; null once the account is gone. */
  made_by: string | null
  created_at: string
  rolled_at: string | null
  last_delivery: WebhookDelivery | null
}

/** A webhook just added or rolled, with its secret, which nothing shows again. */
export interface WebhookMade extends Webhook {
  secret: string
}

/** The days a share link may run, of which its maker chooses one (DAYS in backend/app/share_links.py). */
export const SHARE_DAYS = [1, 7, 30] as const
export type ShareDays = (typeof SHARE_DAYS)[number]
export const DEFAULT_SHARE_DAYS: ShareDays = 7

/** What a share link shows: a document, or an answer of a chat with the passages it cites. */
export type SharedKind = 'document' | 'answer'

/** A link to a document or an answer that has not expired (ShareLinkOut): never its address, which its maker saw once. */
export interface ShareLink {
  id: number
  kind: SharedKind
  /** The document it shows, or the answer. */
  document_id: number | null
  message_id: number | null
  /** Its maker, by id to tell the member's own, and by name as it is now. */
  user_id: number
  made_by: string
  created_at: string
  expires_at: string
  /** Whether it asks for a password. */
  password: boolean
  /** How often it was opened, and when last. */
  views: number
  last_viewed_at: string | null
}

/** A link just made, with its address, which nothing shows again. */
export interface ShareLinkMade extends ShareLink {
  url: string
}

/** A link of the organisation's, as its owners and admins list them, with what it shows: a document by its name, an answer by none, since a chat's words are its members'. */
export interface OrganizationShareLink extends ShareLink {
  filename: string | null
  project_id: number
  project_name: string
}

/** What a document's share link shows its visitor (SharedDocumentOut): the document, read only, and who shared it. */
export interface SharedDocument {
  kind: 'document'
  link_id: number
  filename: string
  title: string | null
  doc_type: string | null
  doc_date: string | null
  mime_type: string
  page_count: number | null
  /** The member who made the link, and their organisation. */
  shared_by: string
  organization: string
  /** When the link ends, and when this opening of it does, after which the page opens it again. */
  expires_at: string
  view_ends_at: string
  content: DocumentContent
}

/** A passage an answer cites, as its share link shows it: its text alone, with where it is. */
export interface SharedPassage {
  id: number
  document_title: string
  context: string
  text: string
  page_start: number | null
  page_end: number | null
}

/** What an answer's share link shows its visitor (SharedAnswerOut): its question, its text and the passages it cites, nothing else of the documents. */
export interface SharedAnswer {
  kind: 'answer'
  link_id: number
  question: string
  answer: string
  answered_at: string
  passages: SharedPassage[]
  shared_by: string
  organization: string
  expires_at: string
}

export type Shared = SharedDocument | SharedAnswer

/** A page of a webhook's delivery log, the newest first. */
export interface WebhookDeliveries {
  deliveries: WebhookDelivery[]
  next_before: number | null
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

  constructor(status: number, message: string) {
    super(message)
    this.status = status
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

let onUnauthorized: (() => void) | null = null
let onForbidden: (() => void) | null = null
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
 * What happens when the server refused a request as not the account's to make (403): the
 * account may have changed since the page asked who is signed in, a role lowered by an admin,
 * two-factor required or a deletion asked for elsewhere, so the app registers a handler that asks
 * again, and the page stops offering what the server refuses. Sign-in's own routes are left
 * out, as for a 401.
 */
export function handleForbidden(handler: (() => void) | null) {
  onForbidden = handler
}

/**
 * The last request of this page's that the API refused or failed, by the id its answer carried
 * (X-Request-ID, backend/app/logs.py): what a message from the help menu attaches, for whoever
 * answers to find in the log what the member met. Null until one did.
 */
export function lastFailedRequest(): FailedRequest | null {
  return lastFailure
}

/**
 * Every request carries this header. The server refuses a change without it, which is what keeps
 * a page on another site from making one with the user's cookie (backend/app/api/deps.py).
 */
async function send(path: string, init?: RequestInit, onProgress?: Progress): Promise<Response> {
  const headers = new Headers(init?.headers)
  headers.set('X-Requested-With', 'fetch')
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
  // A share link's routes answer for the link, never for the session (backend/app/share_links.py).
  const forSession = !path.startsWith('/auth/') && !path.startsWith('/shared')
  if (response.status === 401 && forSession) onUnauthorized?.()
  if (response.status === 403 && forSession) onForbidden?.()
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
 * fallback when it has none.
 */
async function refusal(response: Response, fallback: string): Promise<ApiError> {
  try {
    const body = await response.json()
    return new ApiError(response.status, typeof body.detail === 'string' ? body.detail : fallback)
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
  me: async (): Promise<SignedIn | null> => {
    const response = await send('/auth/me')
    if (response.status === 401) return null
    if (!response.ok) throw new ApiError(response.status, response.statusText)
    return response.json()
  },
  options: () => request<AuthOptions>('/auth/options'),
  /**
   * Makes the install's first account, its admin, an owner of the workspace named `workspace`, and signs it in; a
   * 409 once any account exists (backend/app/signups.py).
   */
  setup: (body: {
    email: string
    name: string
    password: string
    workspace: string
    time_zone: string
  }) => request<Me>('/auth/setup', json('POST', body)),
  /**
   * Mails `email` the link that makes the account. Answers alike whether or not the address has an
   * account; one that has is told so by mail instead.
   */
  signup: (email: string) => request<void>('/auth/signup', json('POST', { email })),
  /** The address a sign-up link was sent to; the link stays unused. */
  signupLink: (token: string) => request<{ email: string }>('/auth/signup/link', json('POST', { token })),
  /**
   * Makes the account a sign-up link was sent for and signs it in; `time_zone` is the browser's.
   */
  finishSignup: (body: { token: string; name: string; password: string; time_zone: string }) =>
    request<Me>('/auth/signup/finish', json('POST', body)),
  /** What an invitation's link is for; an expired one too, a gone one is a 400. */
  invitation: (token: string) => request<InvitationLink>('/auth/invitation', json('POST', { token })),
  /** Joins the workspace an invitation to the signed-in account's address is to, a member removed from it too. */
  acceptInvitation: (token: string) => request<Me>('/auth/invitation/accept', json('POST', { token })),
  /** Makes an account for an invitation's address, a member of that organisation alone, and signs it in. */
  signUpInvited: (body: { token: string; name: string; password: string; time_zone: string }) =>
    request<Me>('/auth/invitation/signup', json('POST', body)),
  /** With two-factor on, the session grants nothing until `verifyMfa` succeeds. */
  login: (body: { email: string; password: string }) =>
    request<{ mfa_required: boolean }>('/auth/login', json('POST', body)),
  verifyMfa: (code: string) => request<SignedIn>('/auth/mfa/verify', json('POST', { code })),
  logout: () => request<void>('/auth/logout', { method: 'POST' }),

  /** A field left out stays as it is, and so does a way of a kind of notice; turning one on by email writes again to an address that bounced. */
  updateProfile: (body: {
    name?: string
    time_zone?: string
    notices?: { kind: NoticeKind; app?: boolean; email?: boolean }[]
  }) => request<Me>('/auth/profile', json('PATCH', body)),
  /** Hide the list of first steps or show it again, or say the tour was seen; a field left out stays as it is. */
  chooseOnboarding: (body: { checklist_hidden?: boolean; tour_seen?: boolean }) =>
    request<Me>('/auth/onboarding', json('PATCH', body)),
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
   * Where the install mails nothing, an owner or admin is `moved` at once, and a member refused.
   */
  requestEmailChange: (body: { email: string; password?: string; current_email?: string }) =>
    request<{ moved: boolean }>('/auth/email', json('POST', body)),
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
 * The workspace: its name, settings, members and their roles, owner, invitations and API keys.
 * Mirrors backend/app/api/organizations.py, backend/app/api/invitations.py and
 * backend/app/api/apikeys.py; each change of the workspace answers with the account as `auth.me` does.
 */
export const organizations = {
  /** Owners and admins only: its name or its settings; what is left out stays, a null language goes back to the deployment's and a null number of days keeps until deleted. */
  change: (body: OrganizationChange) => request<Me>('/organization', json('PATCH', body)),
  /** The languages its documents may be searched in, and the deployment's. */
  languages: () => request<Languages>('/organization/languages'),
  members: () => request<Member[]>('/organization/members'),
  /** Owners and admins only, for another member whose role is within their own, to a role within it. */
  changeRole: (userId: number, role: Role) => request<Member>(`/organization/members/${userId}`, json('PATCH', { role })),
  /** As `changeRole`; the member's private chats in its projects go, and they keep their account in no workspace until invited again. */
  removeMember: (userId: number) => request<void>(`/organization/members/${userId}`, { method: 'DELETE' }),
  /** As `changeRole`, for another member: a link that sets their password, which stops their earlier ones. */
  resetLink: (userId: number) => request<HandedLink>(`/organization/members/${userId}/reset-link`, { method: 'POST' }),
  /** As `resetLink`: a link that moves their account to the address they asked for, which stops their earlier ones. */
  emailLink: (userId: number, email: string) =>
    request<HandedLink>(`/organization/members/${userId}/email-link`, json('POST', { email })),
  /** Owners only, confirmed by the password, or by the email address for an account without one. */
  handOver: (body: { user_id: number; password?: string; email?: string }) =>
    request<Me>('/organization/owner', json('POST', body)),
  /** Owners and admins only, as is everything about invitations. */
  invitations: () => request<Invitation[]>('/organization/invitations'),
  /** Mails the address its link, also given back; a 409 for a member or one invited already. */
  invite: (body: { email: string; role: Role }) => request<InvitationSent>('/organization/invitations', json('POST', body)),
  /** Mails a new link, also given back, which stops the one before. */
  resendInvitation: (id: number) => request<InvitationSent>(`/organization/invitations/${id}/resend`, { method: 'POST' }),
  revokeInvitation: (id: number) => request<void>(`/organization/invitations/${id}`, { method: 'DELETE' }),
  /** Owners and admins only, as is everything about API keys; none of these takes a key. */
  keys: () => request<ApiKey[]>('/organization/keys'),
  /** A key that acts as the member who makes it. */
  makeKey: (body: { name: string; scope: ApiKeyScope; days: number | null }) =>
    request<ApiKeyMade>('/organization/keys', json('POST', body)),
  /** The member's own key with a new secret; the one before stops at once. */
  rollKey: (id: number) => request<ApiKeyMade>(`/organization/keys/${id}/roll`, { method: 'POST' }),
  revokeKey: (id: number) => request<void>(`/organization/keys/${id}`, { method: 'DELETE' }),
  /** Owners and admins only, as is everything about webhooks; none of these takes a key. */
  webhooks: () => request<Webhook[]>('/organization/webhooks'),
  /** A 400 for an address a webhook may not have. */
  makeWebhook: (body: { url: string; events: WebhookEvent[] }) =>
    request<WebhookMade>('/organization/webhooks', json('POST', body)),
  /** A field left out stays as it is; `enabled: false` pauses it. */
  changeWebhook: (id: number, body: { url?: string; events?: WebhookEvent[]; enabled?: boolean }) =>
    request<Webhook>(`/organization/webhooks/${id}`, json('PATCH', body)),
  /** A new secret, every try from now on signed with it; the one before ends at once. */
  rollWebhook: (id: number) => request<WebhookMade>(`/organization/webhooks/${id}/roll`, { method: 'POST' }),
  removeWebhook: (id: number) => request<void>(`/organization/webhooks/${id}`, { method: 'DELETE' }),
  /** Owners and admins only: every link of the organisation that has not expired, the newest first. */
  shareLinks: () => request<OrganizationShareLink[]>('/organization/share-links'),
  webhookDeliveries: (id: number, before: number | null = null) =>
    request<WebhookDeliveries>(`/organization/webhooks/${id}/deliveries${before === null ? '' : `?before=${before}`}`),
  /** Every member: what the workspace holds and used, with the history; what it cost to owners and admins alone. */
  usage: () => request<UsagePage>('/organization/usage'),
  /** The organisation's first steps that the member's role is offered, and which it took. */
  onboarding: () => request<Onboarding>('/organization/onboarding'),
}

// The Administration page, the workspace's owners and admins alone: what the maintenance commands
// print (backend/app/api/admin.py, the Admin* models in backend/app/schemas.py). Amounts are
// millionths of the currency, as the server keeps them.

/** What the workspace's work cost over a span of days, against DAILY_SPEND_CAP (null for none). */
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
  cost: Cost
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

/** A model provider the settings name a model of, or that the page holds switched off, and what holds it off. */
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
  /** How the mail goes; null where nothing is mailed. */
  mail: 'smtp' | 'resend' | null
  storage_origin: string | null
  concerns: Concern[]
  unchecked: string[]
  database: { version: string; role: string; vector: string | null; iterative_scans: boolean; timeouts: Record<string, string> }
}

export const admin = {
  costs: (days: number) => request<Costs>(`/organization/costs?days=${days}`),
  jobs: () => request<Jobs>('/organization/jobs'),
  services: () => request<Services>('/organization/services'),
  /** Run the workspace's failed jobs again by id, or every one for null. */
  requeue: (jobIds: number[] | null) => request<Requeued>('/organization/jobs/requeue', json('POST', { job_ids: jobIds })),
  /** Switch a provider off for every process of the install, or on again. */
  switchProvider: (name: string, off: boolean) =>
    request<Provider[]>(`/organization/providers/${encodeURIComponent(name)}`, json('PUT', { off })),
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
  /** Public, since a sign-in page can fail too: the reference is the id the app logged the report under. */
  reportPageError: (report: PageError) =>
    request<{ reference: string | null }>('/page-errors', json('POST', report)),
  /** Public and reading no database, since the page that cannot reach its server asks it too. */
  help: () => request<Help>('/help'),
  /** The help pages, a member's, in the contents' order. */
  helpArticles: () => request<HelpArticle[]>('/help/articles'),
  /** Public: what a share link shows, and its view's cookie; a 401 asks for its password, a 404 says it stopped. */
  openShared: (token: string, password?: string) => request<Shared>('/shared', json('POST', { token, password })),
  /** Mails the member's message to SUPPORT_EMAIL or the workspace's owners and admins; a 429 says when they may write again, a 409 that a sole admin has nobody to write to. */
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
  /** An export of the project, or of the documents chosen in it alone. */
  exportProject: (id: number, documentIds?: number[]) =>
    request<ProjectExport>(`/projects/${id}/exports`, documentIds ? json('POST', { document_ids: documentIds }) : { method: 'POST' }),
  /** The organisation's projects in the trash, and how many days it keeps them. */
  trashedProjects: () => request<{ projects: TrashedProject[]; days: number }>('/trash'),
  projectTrash: (projectId: number) => request<ProjectTrash>(`/projects/${projectId}/trash`),
  /** Delete for good everything in the project's trash, the asker's own chats among it. */
  emptyTrash: (projectId: number) => request<void>(`/projects/${projectId}/trash`, { method: 'DELETE' }),
  /** Take something out of the trash. */
  restore: <T>(kind: TrashKind | 'project', id: number) =>
    request<T>(`/trash/${kind}s/${id}/restore`, { method: 'POST' }),
  /** Delete something in the trash for good, with its files. */
  purge: (kind: TrashKind | 'project', id: number) => request<void>(`/trash/${kind}s/${id}`, { method: 'DELETE' }),
  /** Without a stage, the project's partly indexed documents, each from the first stage it went without ("Index the rest"); with one, every document from it. */
  reprocessProject: (id: number, fromStage?: StageName) =>
    request<{ queued: number }>(`/projects/${id}/reprocess${fromStage ? `?from_stage=${fromStage}` : ''}`, {
      method: 'POST',
    }),

  /** The project's address, with what it received lately. */
  mailbox: (projectId: number) => request<Mailbox>(`/projects/${projectId}/mailbox`),
  /** Give the project an address, or change whom it takes mail from and where it goes. */
  setMailbox: (projectId: number, body: MailboxInput) => request<Mailbox>(`/projects/${projectId}/mailbox`, json('PUT', body)),
  /** A new address in place of the project's, which then reaches nothing. */
  newMailAddress: (projectId: number) => request<Mailbox>(`/projects/${projectId}/mailbox/address`, { method: 'POST' }),
  deleteMailbox: (projectId: number) => request<void>(`/projects/${projectId}/mailbox`, { method: 'DELETE' }),
  /** The project's connected folders. */
  connectors: (projectId: number) => request<Connectors>(`/projects/${projectId}/connectors`),
  /** Where to send the browser for the member's consent to read their files: a new connector, or a paused one of theirs connected again. */
  connect: (projectId: number, provider: ConnectorProvider, connectorId: number | null = null) =>
    request<{ url: string }>(`/projects/${projectId}/connectors`, json('POST', { provider, connector_id: connectorId })),
  /** The folders in one of the member's folders at the service, or where browsing starts. */
  remoteFolders: (projectId: number, connectorId: number, parent: string | null) =>
    request<{ folders: RemoteFolder[] }>(
      `/projects/${projectId}/connectors/${connectorId}/folders${parent ? `?${new URLSearchParams({ parent })}` : ''}`,
    ),
  /** Choose the connector's folder, while it is being chosen, and where its files go. */
  setConnector: (projectId: number, connectorId: number, body: { folder_ref?: string | null; target_folder_id: number | null }) =>
    request<Connector>(`/projects/${projectId}/connectors/${connectorId}`, json('PUT', body)),
  syncConnector: (projectId: number, connectorId: number) =>
    request<Connector>(`/projects/${projectId}/connectors/${connectorId}/sync`, { method: 'POST' }),
  disconnect: (projectId: number, connectorId: number) =>
    request<void>(`/projects/${projectId}/connectors/${connectorId}`, { method: 'DELETE' }),
  /** The member's own files in the project, the one changed last first, and how many it keeps. */
  myFiles: (projectId: number) => request<MyFiles>(`/projects/${projectId}/my-files`),
  /** Keep Word and Excel files in the member's My files: in their folder `folderId` or at the top, each below it in the folders its item names, made as needed ("Letters/2026", a folder uploaded whole). */
  addMyFiles: (projectId: number, items: UploadItem[], folderId: number | null) => {
    const form = new FormData()
    for (const { file } of items) form.append('files', file)
    if (items.some(({ folder }) => folder)) for (const { folder } of items) form.append('paths', folder)
    if (folderId != null) form.append('folder_id', String(folderId))
    return request<MyFilesAdded>(`/projects/${projectId}/my-files`, { method: 'POST', body: form })
  },
  myFile: (id: number) => request<MyFileDetail>(`/my-files/${id}`),
  /** Keep a file edited elsewhere as the next version, made from version `base`, which must still be the newest. */
  addMyFileVersion: (id: number, file: File, base: number) => {
    const form = new FormData()
    form.append('file', file)
    form.append('base', String(base))
    return request<MyFileDetail>(`/my-files/${id}/versions`, { method: 'POST', body: form })
  },
  renameMyFile: (id: number, name: string) => request<MyFileDetail>(`/my-files/${id}`, json('PATCH', { name })),
  /** Put a file into another of the member's folders, null for the top. */
  moveMyFile: (id: number, folderId: number | null) => request<MyFileDetail>(`/my-files/${id}`, json('PATCH', { folder_id: folderId })),
  createMyFolder: (projectId: number, name: string, parentId: number | null) =>
    request<MyFolder>(`/projects/${projectId}/my-folders`, json('POST', { name, parent_id: parentId })),
  renameMyFolder: (id: number, name: string) => request<MyFolder>(`/my-folders/${id}`, json('PATCH', { name })),
  /** Delete a folder for good with the folders and files in it. */
  deleteMyFolder: (id: number) => request<void>(`/my-folders/${id}`, { method: 'DELETE' }),
  deleteMyFile: (id: number) => request<void>(`/my-files/${id}`, { method: 'DELETE' }),
  /** Have a version scanned and shown again, once either failed. */
  showMyFile: (id: number, number: number) => request<MyFileDetail>(`/my-files/${id}/versions/${number}/view`, { method: 'POST' }),
  /** Copy the newest version into the library: a new document in a folder, or the next version of the document it came from. */
  copyMyFile: (id: number, body: { folder_id?: number | null; as_version?: boolean }) =>
    request<Document>(`/my-files/${id}/copy`, json('POST', body)),

  folders: (projectId: number) => request<Folder[]>(`/projects/${projectId}/folders`),
  createFolder: (projectId: number, body: { name: string; parent_id: number | null }) =>
    request<Folder>(`/projects/${projectId}/folders`, json('POST', body)),
  updateFolder: (id: number, body: { name?: string; parent_id?: number | null }) =>
    request<Folder>(`/folders/${id}`, json('PATCH', body)),
  deleteFolder: (id: number) => request<void>(`/folders/${id}`, { method: 'DELETE' }),

  /**
   * A page of a place's documents in an order past `after`, with the ETag that names it; null when `held`, the tag of
   * the page the caller has, still names it.
   */
  documentPage: async (
    projectId: number,
    place: ListingPlace,
    order: { sort: DocumentSort; order: SortOrder },
    after: string | null,
    held: string | null = null,
    signal?: AbortSignal,
  ): Promise<(DocumentPage & { etag: string | null }) | null> => {
    const params = new URLSearchParams()
    if ('folder' in place && place.folder != null) params.set('folder_id', String(place.folder))
    if ('favorites' in place) params.set('favorites', 'true')
    if ('recent' in place) params.set('recent', 'true')
    if ('name' in place && place.name) params.set('name', place.name)
    // The library's first order is the server's when none is named.
    if (order.sort !== 'name') params.set('sort', order.sort)
    if (order.order !== 'asc') params.set('order', order.order)
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
  /** The ids of the project's documents the reader starred, the newest star first. */
  favorites: (projectId: number) => request<{ document_ids: number[] }>(`/projects/${projectId}/favorites`),
  starDocument: (id: number) => request<void>(`/documents/${id}/favorite`, { method: 'PUT' }),
  unstarDocument: (id: number) => request<void>(`/documents/${id}/favorite`, { method: 'DELETE' }),
  /** Tells the server the reader opened the document now, for their recent documents (backend/app/recents.py). */
  visitDocument: (id: number) => request<void>(`/documents/${id}/visit`, { method: 'PUT' }),
  documentTypes: (projectId: number) => request<DocumentType[]>(`/projects/${projectId}/document-types`),
  updateDocument: (id: number, body: { filename?: string; folder_id?: number | null }) =>
    request<Document>(`/documents/${id}`, json('PATCH', body)),
  deleteDocument: (id: number) => request<void>(`/documents/${id}`, { method: 'DELETE' }),
  /** Sets the document's details as the member's, or takes what the reading found again. */
  changeDetails: (id: number, change: DocumentDetailsInput) =>
    request<Document>(`/documents/${id}/details`, json('PATCH', change)),
  note: (id: number) => request<DocumentNote>(`/documents/${id}/note`),
  /** Saves the document's note whole; a blank one deletes it. */
  saveNote: (id: number, text: string) => request<DocumentNote>(`/documents/${id}/note`, json('PUT', { text })),
  /** The copies of the document's file in the other projects the member sees. */
  copies: (id: number) => request<{ copies: DocumentCopy[] }>(`/documents/${id}/copies`),
  /** A page of the duplicates report from the one `after` names, the first for null. */
  duplicates: (after: number | null, limit?: number) => {
    const params = new URLSearchParams()
    if (after != null) params.set('after', String(after))
    if (limit != null) params.set('limit', String(limit))
    const query = params.toString()
    return request<DuplicatesPage>(query ? `/duplicates?${query}` : '/duplicates')
  },
  /** Have the document's view made: one it never had, or one that failed; one queued or made is left as it is. */
  makeView: (id: number) => request<Document>(`/documents/${id}/view`, { method: 'POST' }),
  reprocessDocument: (id: number, fromStage: StageName) =>
    request<Document>(`/documents/${id}/reprocess?from_stage=${fromStage}`, { method: 'POST' }),
  /** Index the chosen documents again from a stage, or without one the rest of those partly indexed; one being indexed is left. */
  reprocessDocuments: (projectId: number, documentIds: number[], fromStage: StageName | null) =>
    request<DocumentsReprocessed>(`/projects/${projectId}/documents/reprocess`, json('POST', { document_ids: documentIds, from_stage: fromStage })),
  outline: (id: number) => request<Section[]>(`/documents/${id}/outline`),
  content: (id: number) => request<DocumentContent>(`/documents/${id}/content`),
  versions: (id: number) => request<DocumentVersion[]>(`/documents/${id}/versions`),
  /** Give the document a new file, which it reads in the earlier one's place. */
  uploadVersion: (id: number, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<Document>(`/documents/${id}/versions`, { method: 'POST', body: form })
  },
  versionContent: (id: number, number: number) =>
    request<DocumentContent>(`/documents/${id}/versions/${number}/content`),
  documentFacts: (id: number) => request<Fact[]>(`/documents/${id}/facts`),
  passage: (id: number) => request<Passage>(`/passages/${id}`),

  search: (projectId: number, query: string, filter: DocumentFilter) =>
    request<SearchResult>(`/projects/${projectId}/search?${searchParams(query, filter)}`),
  /** The search of every project the member sees, one ranking over them all. */
  searchAll: (query: string, filter: SearchAllFilter) => request<SearchAllResult>(`/search?${searchAllParams(query, filter)}`),
  /** The types of the documents of every project the member sees, the most common first. */
  allDocumentTypes: () => request<DocumentType[]>('/document-types'),
  facts: (projectId: number, filters: { name?: string; q?: string }) => {
    const params = new URLSearchParams({ limit: '500' })
    if (filters.name) params.set('name', filters.name)
    if (filters.q) params.set('q', filters.q)
    return request<{ total: number; facts: Fact[] }>(`/projects/${projectId}/facts?${params}`)
  },
  factNames: (projectId: number) =>
    request<{ name: string; count: number }[]>(`/projects/${projectId}/facts/names`),

  chats: (projectId: number) => request<Chat[]>(`/projects/${projectId}/chats`),
  /** A new chat, about the whole project or about what `scope` names. */
  createChat: (projectId: number, scope: ScopeIn = {}) =>
    request<Chat>(`/projects/${projectId}/chats`, json('POST', { scope })),
  chat: (id: number) => request<ChatDetail>(`/chats/${id}`),
  renameChat: (id: number, title: string) => request<Chat>(`/chats/${id}`, json('PATCH', { title })),
  shareChat: (id: number, visibility: ChatVisibility) =>
    request<Chat>(`/chats/${id}`, json('PATCH', { visibility })),
  /** Change what the chat is about: its next answer reads it. */
  scopeChat: (id: number, scope: ScopeIn) => request<Chat>(`/chats/${id}`, json('PATCH', { scope })),
  deleteChat: (id: number) => request<void>(`/chats/${id}`, { method: 'DELETE' }),
  pinChat: (id: number) => request<Chat>(`/chats/${id}/pin`, { method: 'PUT' }),
  unpinChat: (id: number) => request<Chat>(`/chats/${id}/pin`, { method: 'DELETE' }),
  /** Add a question; the worker writes the answer, which `streamEvents` follows. */
  ask: (chatId: number, content: string) => request<AskResult>(`/chats/${chatId}/messages`, json('POST', { content })),
  /** End an answer with what arrived so far. */
  stopAnswer: (chatId: number, messageId: number) =>
    request<ChatMessage>(`/chats/${chatId}/messages/${messageId}/stop`, { method: 'POST' }),
  /** Write the chat's last answer again in its place, once it was stopped or failed. */
  retryAnswer: (chatId: number, messageId: number) =>
    request<RetryResult>(`/chats/${chatId}/messages/${messageId}/retry`, { method: 'POST' }),
  /** Write the chat's last answer again in its place, from its question or `question` in its place; it counts a question. */
  regenerateAnswer: (chatId: number, messageId: number, question?: string) =>
    request<RegenerateResult>(`/chats/${chatId}/messages/${messageId}/regenerate`, json('POST', question === undefined ? {} : { question })),

  /** The document's links that have not expired, the newest first. */
  shareLinks: (documentId: number) => request<ShareLink[]>(`/documents/${documentId}/share-links`),
  /** A 403 when the organisation turned them off. */
  makeShareLink: (documentId: number, body: { days: ShareDays; password?: string }) =>
    request<ShareLinkMade>(`/documents/${documentId}/share-links`, json('POST', body)),
  /** The answer's links that have not expired, the newest first. */
  answerShareLinks: (chatId: number, messageId: number) => request<ShareLink[]>(`/chats/${chatId}/messages/${messageId}/share-links`),
  /** As a document's, of a finished answer in a chat the member sees: a 409 while it is being written. */
  makeAnswerShareLink: (chatId: number, messageId: number, body: { days: ShareDays; password?: string }) =>
    request<ShareLinkMade>(`/chats/${chatId}/messages/${messageId}/share-links`, json('POST', body)),
  /** Its maker's, or an owner's or admin's; it stops at once. */
  revokeShareLink: (id: number) => request<void>(`/share-links/${id}`, { method: 'DELETE' }),
}

export const fileUrl = (documentId: number) => `/api/documents/${documentId}/file`
export const myFileUrl = (fileId: number, number: number) => `/api/my-files/${fileId}/versions/${number}/file`
export const myFilePageUrl = (fileId: number, number: number, page: number) =>
  `/api/my-files/${fileId}/versions/${number}/pages/${page}.png`
export const versionFileUrl = (documentId: number, number: number) =>
  `/api/documents/${documentId}/versions/${number}/file`
export const pageImageUrl = (documentId: number, page: number) =>
  `/api/documents/${documentId}/pages/${page}.png`
/** A share link's file and pages, which its view's cookie reaches. */
export const sharedFileUrl = (linkId: number) => `/api/shared/${linkId}/file`
export const sharedPageUrl = (linkId: number, page: number) => `/api/shared/${linkId}/pages/${page}.png`

/**
 * The file types the backend indexes (backend/app/parsing/__init__.py), for upload inputs, and the files the page's upload
 * opens for what they bring (PACKED in backend/app/unpacking.py), which a program's may not send.
 */
export const ACCEPTED_EXTENSIONS = ['.pdf', '.docx', '.xlsx', '.pptx', '.rtf', '.html', '.htm', '.eml', '.zip', '.csv', '.txt', '.md', '.png', '.jpg', '.jpeg', '.webp', '.tif', '.tiff', '.heic', '.heif']
export const ACCEPTED_TYPES_LABEL = 'PDF, Word, Excel, PowerPoint, CSV, text, Markdown and images'

export const isIndexing = (document: Document) =>
  document.status === 'queued' || document.status === 'processing'
export const isPdf = (document: Pick<Document, 'mime_type'>) => document.mime_type === 'application/pdf'
/** Scans a browser does not show, a TIFF or a HEIC photo, whose pages the server draws as a PDF's (SCAN_TYPES in backend/app/parsing/__init__.py). */
const SCAN_TYPES = ['image/tiff', 'image/heic', 'image/heif']
/** Whether the document is shown by its pages, drawn by the server: a PDF, or a scan. */
export const hasPages = (document: Pick<Document, 'mime_type'>) => isPdf(document) || SCAN_TYPES.includes(document.mime_type)
/** An image the browser shows as it is. */
export const isImage = (document: Pick<Document, 'mime_type'>) =>
  document.mime_type.startsWith('image/') && !SCAN_TYPES.includes(document.mime_type)
export const isSheet = (document: Pick<Document, 'mime_type'>) =>
  document.mime_type === 'text/csv' || document.mime_type.endsWith('spreadsheetml.sheet')
/** The types shown as they look by a view, the PDF the converter makes of them (CONVERTIBLE in backend/app/conversion.py). */
const VIEW_TYPES = new Set([
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  'application/vnd.openxmlformats-officedocument.presentationml.presentation',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  'application/rtf',
  'text/html',
])
/** Whether the document is of a type shown as it looks, where the deployment converts files. */
export const hasView = (document: Pick<Document, 'mime_type'>) => VIEW_TYPES.has(document.mime_type)

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

/** A cited passage in a line: its document, section and pages. */
export function sourceLabel(source: Source): string {
  return [source.document_title, sectionPath(source.context, source.document_title), pageLabel(source.page_start, source.page_end)]
    .filter(Boolean)
    .join(' · ')
}
