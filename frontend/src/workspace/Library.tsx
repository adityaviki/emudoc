import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Activity as ActivityIcon,
  BadgeCheck,
  CalendarClock,
  Camera,
  ChevronRight,
  ChevronsDownUp,
  ChevronsUpDown,
  ClipboardCheck,
  ClipboardList,
  ClipboardX,
  Download,
  FileUp,
  Flag,
  Folder as FolderIcon,
  FolderInput,
  FolderPlus,
  FolderSync,
  FolderUp,
  GitCompareArrows,
  ListChecks,
  Mail,
  MessageSquare,
  MoreHorizontal,
  Pencil,
  PenLine,
  RefreshCw,
  ScrollText,
  Search,
  SlidersHorizontal,
  Star,
  StarOff,
  TableProperties,
  Tag as TagIcon,
  Trash2,
  Upload,
  WifiOff,
  X,
} from 'lucide-react'
import { createElement, Fragment, useEffect, useEffectEvent, useRef, useState } from 'react'
import type { DragEvent, FocusEvent, KeyboardEvent, MouseEvent, RefObject } from 'react'
import {
  ACCEPTED_EXTENSIONS,
  ACCEPTED_TYPES_LABEL,
  api,
  mayChange,
  pageLabel,
  SCOPE_DOCUMENTS,
  sectionPath,
  thumbnailUrl,
} from '../api.ts'
import type { Document, Folder, ListingPlace, ScopeIn, SearchHit, Tag, UploadItem } from '../api.ts'
import { useMe } from '../auth.ts'
import { folderChoices } from '../automations.ts'
import { startChat } from '../chat.ts'
import { ErrorBoundary } from '../components/ErrorBoundary.tsx'
import { StatusIcon } from '../components/IndexingStatus.tsx'
import type { MenuItem } from '../components/Menu.tsx'
import { PlanLimitNotice, PlanOffer } from '../components/PlanLimit.tsx'
import { Confirm } from '../components/Confirm.tsx'
import { Button, Chip, EmptyState, IconButton, Loading, Notice, SelectField, Spinner } from '../components/ui.tsx'
import { useContextMenu } from '../components/useContextMenu.tsx'
import { uploadCutOff, uploadWaiting } from '../connection.ts'
import { libraryPlace } from '../failures.ts'
import { cursorAfter } from '../focus.ts'
import { useFolders, useLocalStorage, useOnline, useStatus } from '../hooks.ts'
import type { FolderTree } from '../hooks.ts'
import { partlyIndexedWords, restHeld, unfinishedStages } from '../indexing.ts'
import {
  EMPTY_FILTER,
  MAX_WITHIN,
  SORT_NAMES,
  emptyPlaceWords,
  filterParts,
  filterWords,
  gridMove,
  hasThumbnail,
  narrows,
  placeName,
  placeOf,
  tagsOf,
  viewedSearch,
  withoutPart,
} from '../library.ts'
import type { LibraryLayout, Search as LibrarySearch } from '../library.ts'
import { stateMoves } from '../reviews.ts'
import { SUMMARY_QUESTION, comparisonQuestion } from '../scopes.ts'
import { MAX_CHOSEN, retagged } from '../selection.ts'
import type { TagsChange } from '../selection.ts'
import { askHeld } from '../status.ts'
import { VERSION_EXTENSIONS } from '../versions.ts'
import { describeChange } from './changes.ts'
import { chosenFolder, droppedEntries, readEntries } from './dropped.ts'
import { photoName } from './uploads.ts'
import { RunView } from './CheckRuns.tsx'
import { CrossChecksView, FindingsView } from './CrossChecks.tsx'
import { DraftTemplatesView } from './DraftTemplates.tsx'
import { DraftView, DraftsView } from './Drafts.tsx'
import { ActivityView } from './Activity.tsx'
import { ConnectorsView } from './Connectors.tsx'
import { MailboxView } from './Mailbox.tsx'
import { ChangesView } from './FactChanges.tsx'
import { FactsView } from './FactsTable.tsx'
import { fileKind, originalFileItem } from './files.ts'
import { FilingView } from './Filing.tsx'
import { BY_NAME, growing, hasMore, listed, placeKey, showMore, useListings } from './listing.ts'
import type { Arrangement, Listing } from './listing.ts'
import { useLibraryMutations, useReviewChange } from './mutations.ts'
import { QuestionSetsView, ReportView } from './QuestionSets.tsx'
import type { LibraryMutations } from './mutations.ts'
import { useDocumentMarks, useDocumentTypes, useFavorites, useOrganizing, useTags } from './organizing.ts'
import { DocumentTagsDialog, PlaceBar, SearchFilterDialog, Seen, TagChips } from './Places.tsx'
import { ReviewMark, ReviewsView } from './Reviews.tsx'
import { useWorkspace } from './state.ts'
import { TagsView } from './Tags.tsx'
import { TemplatesView } from './Templates.tsx'
import { TrashView } from './Trash.tsx'
import { UploadPanel } from './UploadPanel.tsx'
import { UploadStopped, isAbort, uploadStopped } from './uploads.ts'

type Item = { kind: 'folder'; folder: Folder } | { kind: 'file'; document: Document }

const itemKey = (item: Item) => (item.kind === 'folder' ? `f:${item.folder.id}` : `d:${item.document.id}`)
const itemName = (item: Item) => (item.kind === 'folder' ? item.folder.name : item.document.filename)
const parentOf = (item: Item) => (item.kind === 'folder' ? item.folder.parent_id : item.document.folder_id)

/** An item in the order it is shown, with how far below the shown folder it sits. */
interface Entry {
  item: Item
  depth: number
}

/**
 * The line after a place's files that is not one: while its first page comes, when it has
 * more to show, or when it could not be listed.
 */
interface Gap {
  gap: 'loading' | 'more' | 'failed'
  place: ListingPlace
  depth: number
}

type Line = Entry | Gap

const isEntry = (line: Line): line is Entry => 'item' in line

// Internal drags carry the selected items under this type; drags from the desktop carry Files.
const ITEMS_TYPE = 'application/x-emudoc-items'
interface DragPayload {
  folderIds: number[]
  documentIds: number[]
}
// What is being dragged, while it is: dataTransfer data cannot be read until the drop, only its types.
type DragRef = RefObject<DragPayload | null>

const hasItems = (event: DragEvent) => event.dataTransfer.types.includes(ITEMS_TYPE)
const hasFiles = (event: DragEvent) => event.dataTransfer.types.includes('Files')
// What a drop of files onto the sample project's folders says (backend/app/sample, SAMPLE_UPLOADS).
const SAMPLE_UPLOADS = 'This is the sample project, which holds Emudoc’s sample documents alone. Create a project of your own to upload yours.'
// What a browser without the entries API hands over for a dropped folder: an empty, typeless file.
const NO_FOLDER_DROPS = 'This browser cannot upload a folder dropped on the page: use “Upload a folder”.'

// What each move of a document's review shows in its menu, as the reviews view marks each state.
const MOVE_ICONS = { to_review: ClipboardList, approved: BadgeCheck, disputed: Flag } as const

/** How a drop or a picker hands the library what to upload into a folder. */
type Uploads = (transfer: DataTransfer, targetId: number | null) => void

// The folders left open in the tree, per project.
const treeKey = (projectId: number) => `emudoc.tree-open.${projectId}`
const isIdList = (value: unknown): value is number[] =>
  Array.isArray(value) && value.every((id) => typeof id === 'number')
// The order and the layout the member chose last, in this browser.
const ARRANGEMENT_KEY = 'emudoc.library-order'
const LAYOUT_KEY = 'emudoc.library-layout'
const isArrangement = (value: unknown): value is Arrangement =>
  typeof value === 'object' &&
  value !== null &&
  SORT_NAMES.includes((value as Arrangement).sort) &&
  ['asc', 'desc'].includes((value as Arrangement).order)
const isLayout = (value: unknown): value is LibraryLayout => value === 'list' || value === 'grid'
// How wide a tile of the grid is at the least, with the gap after it, in pixels.
const TILE_WIDTH = 128

/**
 * The file manager: a folder's contents, with search over the whole project or the folder. A
 * viewer reads and searches it; uploading, folders, renaming, moving, deleting and indexing
 * again are offered only to a role the server lets change them.
 */
export function Library() {
  const workspace = useWorkspace()
  const { tree, project } = workspace
  const { folderId, q: query } = workspace.url.library
  const folders = useFolders(project.id)
  const mutations = useLibraryMutations()
  const byPlan = mutations.rejected.find((file) => file.limit !== null)
  const { upload } = mutations
  // Offline, TanStack Query holds the upload until the browser is back; one cut off on its way
  // keeps the files it did not send, to send again, and one stopped says what did not go in.
  const waiting = upload.isPaused ? uploadWaiting(upload.variables?.items.length ?? 0) : null
  const stopped = upload.error instanceof UploadStopped ? upload.error : null
  const cutOff = stopped && uploadCutOff(stopped.error, stopped.unsent.length)
  const stoppedByMember = stopped && isAbort(stopped.error) ? uploadStopped(stopped.unsent.length) : null
  const me = useMe()
  const changes = mayChange(me.role)
  // Behind its flag while it is rolled out (backend/app/flags.py).
  const templates = me.organization.flags_on.includes('extraction_templates')
  const drafts = me.organization.flags_on.includes('drafts')
  const mail = me.organization.flags_on.includes('email_in')
  const connectors = me.organization.flags_on.includes('connectors')
  const questionSets = me.organization.flags_on.includes('question_sets')
  const commenting = me.organization.flags_on.includes('comments')
  // The sample project takes no uploads: it counts against nothing on the plan (backend/app/sample).
  const uploads = changes && !project.sample
  const health = useStatus()
  const fileInput = useRef<HTMLInputElement>(null)
  const folderInput = useRef<HTMLInputElement>(null)
  const photoInput = useRef<HTMLInputElement>(null)
  const versionInput = useRef<HTMLInputElement>(null)
  // The document whose new version the file picker is choosing.
  const versionOf = useRef<Document | null>(null)
  const dragging = useRef<DragPayload | null>(null)
  const [creating, setCreating] = useState(false)
  const [overPane, setOverPane] = useState(false)

  // A folder that no longer exists falls back to the root.
  const folderMissing = folders.isSuccess && folderId != null && !tree.byId.has(folderId)
  useEffect(() => {
    if (folderMissing) workspace.goToFolder(null, { replace: true })
  }, [folderMissing, workspace])

  const current = folderId == null ? null : tree.byId.get(folderId)
  const currentName = current?.name ?? project.name
  const folderName = (id: number | null | undefined) => tree.byId.get(id ?? -1)?.name ?? project.name
  const { followed } = mutations

  // A tag's documents, the member's favourites or recent ones, each across the project's folders;
  // else the folder's tree.
  const place: ListingPlace = placeOf(workspace.url.library) ?? { folder: folderId }
  const flat = !('folder' in place)
  const [arrangement, setArrangement] = useLocalStorage<Arrangement>(ARRANGEMENT_KEY, BY_NAME, isArrangement)
  const [layout, setLayout] = useLocalStorage<LibraryLayout>(LAYOUT_KEY, 'list', isLayout)
  const tags = useTags(project.id)
  const search: LibrarySearch | null = viewedSearch(workspace.url.library)
  const searchBox = useRef<HTMLInputElement>(null)
  // A tag deleted, here or elsewhere, falls back to the root.
  const placeGone = 'tag' in place && tags.isSuccess && !tags.data.tags.some((tag) => tag.id === place.tag)
  useEffect(() => {
    if (placeGone) workspace.goToFolder(null, { replace: true })
  }, [placeGone, workspace])

  function uploadItems(items: UploadItem[], targetId: number | null, unread: string[] = []) {
    const refusal = project.sample ? SAMPLE_UPLOADS : mutations.startUpload(items, targetId, unread)
    if (refusal) mutations.setNotice(refusal)
  }

  // A drop's folders are read through before anything is sent, each file with the folders it is in.
  function uploadDropped(transfer: DataTransfer, targetId: number | null) {
    const entries = droppedEntries(transfer)
    if (entries) {
      void readEntries(entries).then(({ items, unread }) => uploadItems(items, targetId, unread))
      return
    }
    const files = [...transfer.files]
    const accepted = files.filter((file) => file.size > 0 || file.type !== '')
    if (accepted.length < files.length) mutations.setNotice(NO_FOLDER_DROPS)
    if (accepted.length) uploadItems(accepted.map((file) => ({ file, folder: '' })), targetId)
  }

  // The partly indexed documents are indexed in full in one go, once the services their rest
  // needs are back: the model for those without summaries and facts, the embeddings for all.
  const partly = project.partly_indexed_count
  const partlyHeld = restHeld(project.unenriched_count ? ['enrich', 'embed'] : ['embed'], health.data)

  if (workspace.url.library.trash) return <TrashView />
  if (workspace.url.library.templates && templates) return <TemplatesView />
  if (workspace.url.library.facts && templates) return <FactsView kind={workspace.url.library.facts} />
  if (workspace.url.library.changes && templates) return <ChangesView kind={workspace.url.library.changes} />
  if (workspace.url.library.checks && templates) return <CrossChecksView />
  if (workspace.url.library.check != null && templates) return <FindingsView checkId={workspace.url.library.check} />
  if (workspace.url.library.run != null && templates) return <RunView runId={workspace.url.library.run} />
  if (workspace.url.library.questionSets && questionSets) return <QuestionSetsView />
  if (workspace.url.library.report != null && questionSets) return <ReportView reportId={workspace.url.library.report} />
  if (workspace.url.library.reviews && templates) return <ReviewsView whose={workspace.url.library.reviews} />
  if (workspace.url.library.activity && commenting) return <ActivityView />
  if (workspace.url.library.drafts && drafts) return <DraftsView />
  if (workspace.url.library.draft != null && drafts) return <DraftView draftId={workspace.url.library.draft} />
  if (workspace.url.library.draftTemplates && drafts) return <DraftTemplatesView />
  if (workspace.url.library.mail && mail) return <MailboxView />
  if (workspace.url.library.connectors && connectors) return <ConnectorsView />
  if (workspace.url.library.tags) return <TagsView />
  if (workspace.url.library.filing) return <FilingView />
  const placeLabel = flat ? placeName(place, tags.data?.tags ?? []) : null
  // The box searches where the member is: a tag's documents, else the folder, within what narrows its search; the
  // favourites and the recent ones are no filter, and it searches the project from them.
  const searchFrom = (q: string) => {
    if ('tag' in place) workspace.showSearch({ q, filter: { ...EMPTY_FILTER, tags: [place.tag] } })
    else workspace.setSearch(q)
  }
  const narrowed = search != null && narrows({ ...search.filter, folder_id: null })
  const searchPlaceholder = narrowed
    ? 'Search within these documents'
    : flat && placeLabel && 'tag' in place
      ? `Search in ${placeLabel}`
      : folderId == null
        ? 'Search the project'
        : `Search in ${currentName}`
  return (
    <div
      data-tour="library"
      className="relative flex h-full min-h-0 flex-col bg-page"
      onDragOver={(event) => {
        if (!uploads || !hasFiles(event)) return
        event.preventDefault()
        event.dataTransfer.dropEffect = 'copy'
        setOverPane(true)
      }}
      onDragLeave={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setOverPane(false)
      }}
      onDrop={(event) => {
        setOverPane(false)
        if (!uploads || !hasFiles(event)) return
        event.preventDefault()
        uploadDropped(event.dataTransfer, folderId)
      }}
    >
      <div className="@container shrink-0 border-b border-line bg-surface">
        <div className="flex h-11 items-center gap-1 px-3">
          <Breadcrumb
            folderId={folderId}
            placeLabel={placeLabel}
            mutations={mutations}
            dragging={dragging}
            onUpload={uploads ? uploadDropped : null}
          />
        </div>
        {/* With every feature on, its buttons take a second line rather than run under the pane beside it. */}
        <div className="flex flex-wrap items-center gap-2 px-3 pb-2.5">
          <SearchBox query={query} placeholder={searchPlaceholder} onSearch={searchFrom} inputRef={searchBox} />
          {templates && (
            <Button
              icon={<TableProperties className="size-4" />}
              onClick={workspace.showTemplates}
              title="Extraction templates: the fields each kind of document is read for"
              aria-label="Extraction templates"
              className="px-2"
            />
          )}
          {templates && (
            <Button
              icon={<ListChecks className="size-4" />}
              onClick={workspace.showChecks}
              title="Cross-checks: rules that compare the facts of one kind of document with another's"
              aria-label="Cross-checks"
              className="px-2"
            />
          )}
          {templates && (
            <Button
              icon={<ClipboardCheck className="size-4" />}
              onClick={() => workspace.showReviews()}
              title="Reviews: the documents put in review, approved or disputed, and whom each is assigned to"
              aria-label="Reviews"
              className="px-2"
            />
          )}
          {commenting && (
            <Button
              icon={<ActivityIcon className="size-4" />}
              onClick={workspace.showActivity}
              title="Activity: what was done in the project lately, its uploads, reviews, comments and runs"
              aria-label="Activity"
              className="px-2"
            />
          )}
          {drafts && (
            <Button
              icon={<PenLine className="size-4" />}
              onClick={workspace.showDrafts}
              title="Drafts: letters, emails, memos, tables and reports the model writes from the documents"
              aria-label="Drafts"
              className="px-2"
            />
          )}
          {questionSets && (
            <Button
              icon={<CalendarClock className="size-4" />}
              onClick={workspace.showQuestionSets}
              title="Question sets: questions asked of the documents on a schedule or as they arrive, their answers kept as reports"
              aria-label="Question sets"
              className="px-2"
            />
          )}
          {mail && (
            <Button
              icon={<Mail className="size-4" />}
              onClick={workspace.showMailbox}
              title="Email in: the project’s address, whose mail becomes its documents"
              aria-label="Email in"
              className="px-2"
            />
          )}
          {connectors && (
            <Button
              icon={<FolderSync className="size-4" />}
              onClick={workspace.showConnectors}
              title="Connected folders: folders of Google Drive, Dropbox or OneDrive the project keeps in step with"
              aria-label="Connected folders"
              className="px-2"
            />
          )}
          {/* A new folder is made from the menu of the listing's empty space, as in a file manager. */}
          {changes && (
            <>
              {/* A narrow pane keeps the icon only. */}
              {uploads && (
                <Button
                  data-tour="upload"
                  icon={
                    upload.isPaused ? <WifiOff className="size-4" /> : upload.isPending ? <Spinner /> : <Upload className="size-4" />
                  }
                  onClick={() => fileInput.current?.click()}
                  disabled={upload.isPending}
                  title={waiting ?? ACCEPTED_TYPES_LABEL}
                  aria-label={upload.isPaused ? 'Waiting for the connection' : upload.isPending ? 'Uploading…' : 'Upload'}
                  className="px-2 @xs:px-3"
                >
                  <span className="hidden @xs:inline">
                    {upload.isPaused ? 'Waiting…' : upload.isPending ? 'Uploading…' : 'Upload'}
                  </span>
                </Button>
              )}
              {uploads && (
                <Button
                  icon={<FolderUp className="size-4" />}
                  onClick={() => folderInput.current?.click()}
                  disabled={upload.isPending}
                  title="Upload a folder, with the folders in it"
                  aria-label="Upload a folder"
                  className="px-2"
                />
              )}
              {/* A phone's or a tablet's camera; a computer worked by a mouse has a file for it already. */}
              {uploads && (
                <Button
                  icon={<Camera className="size-4" />}
                  onClick={() => photoInput.current?.click()}
                  disabled={upload.isPending}
                  title="Take a photo of a page"
                  aria-label="Take a photo"
                  className="px-2 pointer-fine:hidden"
                />
              )}
            </>
          )}
        </div>
        <PlaceBar
          place={place}
          arrangement={arrangement}
          onArrange={setArrangement}
          layout={layout}
          onLayout={setLayout}
          tags={tags.data?.tags ?? []}
          onPlace={(chosen) => ('folder' in chosen ? workspace.goToFolder(null) : workspace.showPlace(chosen))}
          onTags={workspace.showTags}
          onFiling={workspace.showFiling}
          onTrash={workspace.showTrash}
        />
      </div>

      <div className="shrink-0 space-y-2 empty:hidden px-3 pt-2">
        {mutations.notice && (
          <Notice tone="bad">
            <div className="flex items-start justify-between gap-3">
              <span>{mutations.notice}</span>
              <IconButton label="Dismiss" onClick={() => mutations.setNotice(null)} className="p-0.5!">
                <X className="size-3.5" />
              </IconButton>
            </div>
          </Notice>
        )}
        {mutations.planLimit && <PlanLimitNotice refusal={mutations.planLimit} onDismiss={mutations.clearPlanLimit} />}
        {waiting && <Notice tone="info">{waiting}</Notice>}
        <UploadPanel
          progress={mutations.uploadProgress}
          into={folderName(upload.variables?.folderId)}
          onStop={mutations.stopUpload}
          followed={followed && { uploads: followed.uploads, into: folderName(followed.folderId) }}
          onDismiss={mutations.clearFollowed}
        />
        {stoppedByMember && (
          <Notice tone="info">
            <div className="flex items-start justify-between gap-3">
              <span>{stoppedByMember}</span>
              <IconButton label="Dismiss" onClick={() => upload.reset()} className="p-0.5!">
                <X className="size-3.5" />
              </IconButton>
            </div>
          </Notice>
        )}
        {cutOff && (
          <Notice tone="bad">
            <div className="flex items-start justify-between gap-3">
              <span>{cutOff}</span>
              <span className="flex shrink-0 items-center gap-1">
                <Button
                  variant="ghost"
                  className="h-6 px-2"
                  onClick={() => upload.variables && upload.mutate({ ...upload.variables, items: stopped.unsent, resent: true })}
                >
                  Upload again
                </Button>
                <IconButton label="Dismiss" onClick={() => upload.reset()} className="p-0.5!">
                  <X className="size-3.5" />
                </IconButton>
              </span>
            </div>
          </Notice>
        )}
        {mutations.last && (
          <Notice tone="info">
            <div className="flex items-center justify-between gap-3">
              <span>{describeChange(mutations.last)}</span>
              <span className="flex shrink-0 items-center gap-1">
                <Button
                  variant="ghost"
                  className="h-6 px-2"
                  disabled={mutations.undo.isPending}
                  onClick={() => mutations.last && mutations.undo.mutate(mutations.last)}
                >
                  Undo
                </Button>
                <IconButton label="Dismiss" onClick={mutations.clearLast} className="p-0.5!">
                  <X className="size-3.5" />
                </IconButton>
              </span>
            </div>
          </Notice>
        )}
        {mutations.rejected.length > 0 && (
          <Notice>
            <div className="flex items-start justify-between gap-3">
              <div>
                <ul>
                  {mutations.rejected.map((file) => (
                    <li key={file.filename}>
                      <span className="font-medium">{file.filename}</span>: {file.reason}
                    </li>
                  ))}
                </ul>
                {/* A file the plan had no room for: the way past it, once for them all. */}
                {byPlan && <PlanOffer limit={byPlan.limit} />}
              </div>
              <IconButton label="Dismiss" onClick={mutations.clearRejected} className="p-0.5!">
                <X className="size-3.5" />
              </IconButton>
            </div>
          </Notice>
        )}
        {changes && partly > 0 && !query && (
          <Notice tone="info">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <p>{partlyIndexedWords(partly, project.unenriched_count)}</p>
                {partlyHeld && <p className="mt-0.5 text-xs text-muted">{partlyHeld}</p>}
              </div>
              <Button
                icon={<ListChecks className="size-4" />}
                // The notice goes once they are queued: the keyboard goes on from the tree.
                onClick={() =>
                  mutations.indexProjectRest.mutate(undefined, {
                    onSuccess: () => window.document.querySelector<HTMLElement>('[role="tree"]')?.focus(),
                  })
                }
                disabled={mutations.indexProjectRest.isPending || partlyHeld != null}
              >
                Index the rest
              </Button>
            </div>
          </Notice>
        )}
      </div>

      {/* A folder or a search that breaks leaves the path and the search box, from which another opens. */}
      <ErrorBoundary part="library" resetKey={libraryPlace(workspace.url.library)}>
        {search ? (
          <SearchResults
            search={search}
            tags={tags.data?.tags ?? []}
            folders={folders.data ?? []}
            reviews={templates}
            searchBox={searchBox}
          />
        ) : (
          <Listing
            key={`${placeKey(place)}:${layout}`}
            place={place}
            arrangement={arrangement}
            layout={layout}
            tags={tags.data?.tags ?? []}
            canTag={(tags.data?.tags.length ?? 0) < (tags.data?.max_tags ?? 0)}
            creating={creating}
            onCreatingChange={setCreating}
            mutations={mutations}
            changes={changes}
            reviews={templates}
            dragging={dragging}
            onDragChange={(payload) => {
              dragging.current = payload
            }}
            onUploadDropped={uploadDropped}
            onPickFiles={() => fileInput.current?.click()}
            onPickFolder={() => folderInput.current?.click()}
            onPickVersion={
              uploads
                ? (document) => {
                    versionOf.current = document
                    versionInput.current?.click()
                  }
                : undefined
            }
          />
        )}
      </ErrorBoundary>

      {uploads && (
        <>
          <input
            ref={fileInput}
            type="file"
            name="files"
            multiple
            hidden
            accept={ACCEPTED_EXTENSIONS.join(',')}
            onChange={(event) => {
              const files = [...(event.target.files ?? [])]
              if (files.length) uploadItems(files.map((file) => ({ file, folder: '' })), folderId)
              event.target.value = ''
            }}
          />
          {/* A folder's files, each with its path from the folder chosen, which the browser asks the member to allow. */}
          <input
            ref={(input) => {
              folderInput.current = input
              if (input) input.webkitdirectory = true
            }}
            type="file"
            name="folder"
            hidden
            onChange={(event) => {
              const files = [...(event.target.files ?? [])]
              if (files.length) uploadItems(chosenFolder(files), folderId)
              event.target.value = ''
            }}
          />
          {/* The camera that faces away, which a phone opens at once; the photo is read by the model as a scan. */}
          <input
            ref={photoInput}
            type="file"
            name="photo"
            hidden
            accept="image/*"
            capture="environment"
            onChange={(event) => {
              const file = event.target.files?.[0]
              if (file) uploadItems([{ file: new File([file], photoName(file, new Date()), { type: file.type }), folder: '' }], folderId)
              event.target.value = ''
            }}
          />
          {/* A document's new file, chosen from its menu (backend/app/versions.py). */}
          <input
            ref={versionInput}
            type="file"
            name="version"
            hidden
            accept={VERSION_EXTENSIONS.join(',')}
            onChange={(event) => {
              const file = event.target.files?.[0]
              event.target.value = ''
              if (file && versionOf.current) mutations.uploadVersion(versionOf.current, file)
            }}
          />
        </>
      )}

      {workspace.draggingFiles && overPane && (
        <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center bg-page/80 p-4 backdrop-blur-xs">
          <div className="rounded-xl border-2 border-dashed border-accent/60 bg-surface px-8 py-6 text-center text-sm shadow-lg shadow-ink/5">
            <div className="mx-auto mb-3 flex size-10 items-center justify-center rounded-full bg-accent-soft text-accent">
              <Upload className="size-5" />
            </div>
            Drop to upload into <span className="font-semibold">{currentName}</span>
            <p className="mt-0.5 text-xs text-muted">or onto a folder</p>
          </div>
        </div>
      )}
    </div>
  )
}

interface BreadcrumbProps {
  folderId: number | null
  /** The name of a place that is not a folder, which follows the project's in the path; null for a folder's. */
  placeLabel: string | null
  mutations: LibraryMutations
  dragging: DragRef
  /** Null for a role that may not change the library: then the path takes no drops. */
  onUpload: Uploads | null
}

/** The path to the current folder. Each part opens that folder and takes drops into it. */
function Breadcrumb({ folderId, placeLabel, mutations, dragging, onUpload }: BreadcrumbProps) {
  const workspace = useWorkspace()
  // A place that is not a folder follows the project's root, where nothing is dropped; -1 is no folder's id.
  const crumbs: { id: number | null; name: string }[] = [
    { id: null, name: workspace.project.name },
    ...(placeLabel === null
      ? workspace.tree.pathTo(folderId).map((folder) => ({ id: folder.id, name: folder.name }))
      : [{ id: -1, name: placeLabel }]),
  ]
  const [over, setOver] = useState<number | null | 'none'>('none')

  return (
    <nav aria-label="Folder path" className="flex min-w-0 items-center gap-0.5 text-[13px]">
      {crumbs.map((crumb, index) => {
        const last = index === crumbs.length - 1
        return (
          <Fragment key={crumb.id ?? 'root'}>
            {index > 0 && <ChevronRight className="size-3.5 shrink-0 text-muted/60" />}
            <button
              type="button"
              title={crumb.name}
              disabled={last}
              onClick={() => workspace.goToFolder(crumb.id)}
              onDragOver={(event) => {
                if (last || !onUpload) return
                if (hasItems(event) && !canDropItems(workspace.tree, crumb.id, dragging.current)) return
                if (!hasItems(event) && !hasFiles(event)) return
                event.preventDefault()
                event.stopPropagation()
                event.dataTransfer.dropEffect = hasItems(event) ? 'move' : 'copy'
                setOver(crumb.id)
              }}
              onDragLeave={() => setOver('none')}
              onDrop={(event) => {
                setOver('none')
                if (last || !onUpload) return
                event.preventDefault()
                event.stopPropagation()
                if (hasItems(event)) {
                  const payload = JSON.parse(event.dataTransfer.getData(ITEMS_TYPE)) as DragPayload
                  mutations.moveItems.mutate({ ...payload, targetId: crumb.id })
                } else if (hasFiles(event)) {
                  onUpload(event.dataTransfer, crumb.id)
                }
              }}
              // When the path does not fit, the folders above give way before the current one. The factor
              // is large enough that the current one does not lose even a fraction of a pixel, which
              // would already cost it an ellipsis.
              className={`h-6.5 max-w-48 truncate rounded-md px-1.5 transition ${
                last ? 'font-semibold text-ink' : 'min-w-7 shrink-100000 text-muted hover:bg-sunken hover:text-ink'
              } ${over !== 'none' && over === crumb.id ? 'bg-accent-soft text-accent ring-1 ring-accent' : ''}`}
            >
              {crumb.name}
            </button>
          </Fragment>
        )
      })}
    </nav>
  )
}

/** Whether the items being dragged may land in this folder. */
function canDropItems(tree: FolderTree, targetId: number | null, payload: DragPayload | null): boolean {
  if (!payload) return false
  const { folderIds, documentIds } = payload
  // A folder cannot go inside itself or any folder below it.
  if (targetId != null && folderIds.some((id) => tree.subtree(id).has(targetId))) return false
  // Nothing to do when every item is already in the target.
  const alreadyThere =
    folderIds.every((id) => tree.byId.get(id)?.parent_id === targetId) &&
    documentIds.every((id) => documentsParent.get(id) === targetId)
  return !alreadyThere
}
// The folder each document is in, kept current by the Listing for the drop checks above.
const documentsParent = new Map<number, number | null>()

interface SearchBoxProps {
  query: string
  placeholder: string
  onSearch: (q: string) => void
  inputRef: RefObject<HTMLInputElement | null>
  /** Its name, where the page has more than one way to search: the search of every project's. */
  label?: string
}

/** A search box that searches as the words are typed, once they rest. */
export function SearchBox({ query, placeholder, onSearch, inputRef, label = 'Search' }: SearchBoxProps) {
  const [draft, setDraft] = useState(query)
  // The URL can change the query from outside (opening a tab, going back).
  const [lastQuery, setLastQuery] = useState(query)
  if (query !== lastQuery) {
    setLastQuery(query)
    setDraft(query)
  }

  // The library's latest way to search, which a render makes anew, without starting the wait again.
  const search = useEffectEvent(onSearch)
  useEffect(() => {
    const trimmed = draft.trim()
    if (trimmed === query) return
    const timer = window.setTimeout(() => search(trimmed), 300)
    return () => window.clearTimeout(timer)
  }, [draft, query])

  return (
    <div data-tour="search" className="relative min-w-40 flex-1">
      <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted" />
      <input
        ref={inputRef}
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Escape') setDraft('')
        }}
        placeholder={placeholder}
        aria-label={label}
        className="field h-8 pr-8 pl-8 text-[13px]"
      />
      {draft && (
        <button
          type="button"
          aria-label="Clear search"
          onClick={() => setDraft('')}
          className="absolute top-1/2 right-1 flex size-6 -translate-y-1/2 items-center justify-center rounded text-muted transition hover:bg-sunken hover:text-ink"
        >
          <X className="size-3.5" />
        </button>
      )}
    </div>
  )
}

interface ListingProps {
  /** A folder, shown as a tree of it and the folders in it; or a place across the project's folders, shown flat. */
  place: ListingPlace
  arrangement: Arrangement
  /** Rows, or a grid of tiles of the place itself, its folders opened by going into them. */
  layout: LibraryLayout
  /** The project's tags, which each document's are read by. */
  tags: Tag[]
  /** Whether the project has room for one more tag, which the tags' dialog then offers to make. */
  canTag: boolean
  creating: boolean
  onCreatingChange: (creating: boolean) => void
  mutations: LibraryMutations
  /** Whether the member's role may change the library; a viewer opens, downloads and selects only. */
  changes: boolean
  /** Whether documents' reviews are rolled out to the organisation (backend/app/flags.py): each row's mark and menu. */
  reviews: boolean
  dragging: DragRef
  onDragChange: (payload: DragPayload | null) => void
  onUploadDropped: Uploads
  onPickFiles: () => void
  onPickFolder: () => void
  /** Choose a file to give the document as its new version; none where the library takes no uploads. */
  onPickVersion?: (document: Document) => void
}

/** The folders and files in one place, with selection, renaming, drag and drop and a menu. */
function Listing({
  place,
  arrangement,
  layout,
  tags,
  canTag,
  creating,
  onCreatingChange,
  mutations,
  changes,
  reviews,
  dragging,
  onDragChange,
  onUploadDropped,
  onPickFiles,
  onPickFolder,
  onPickVersion,
}: ListingProps) {
  const workspace = useWorkspace()
  const queryClient = useQueryClient()
  // Whether a partly indexed document's rest can run now (`restHeld`), and a question be asked.
  const health = useStatus()
  const online = useOnline()
  const asking = !online || askHeld(health.data) !== null
  const { tree, project } = workspace
  const [openFolders, setOpenFolders] = useLocalStorage<number[]>(treeKey(project.id), [], isIdList)
  const review = useReviewChange(project.id)
  const marks = useDocumentMarks(project.id, (error) => mutations.setNotice(error.message))
  const organizing = useOrganizing(project.id)
  const favorites = useFavorites(project.id)
  const flat = !('folder' in place)
  const grid = layout === 'grid'
  const folderId = 'folder' in place ? place.folder : null
  // The grid shows the folder's own contents, its folders opened by going into them; a flat place has no folders.
  const openSet = flat || grid ? new Set<number>() : new Set(openFolders)
  // Each folder the tree shows open is listed by itself, a page at a time; a flat place is one listing.
  const listedPlaces: ListingPlace[] = flat ? [place] : foldersShown(tree, folderId, openSet).map((id) => ({ folder: id }))
  const listingResults = useListings(project.id, listedPlaces, arrangement)
  const listings = new Map(listedPlaces.map((listedPlace, index) => [placeKey(listedPlace), listingResults[index]]))
  const lines = flat ? flatLines(place, listings.get(placeKey(place))) : treeLines(tree, listings, folderId, openSet)
  const entries = lines.filter(isEntry)
  const items = entries.map((entry) => entry.item)
  const keys = items.map(itemKey)
  useEffect(() => {
    for (const item of items) if (item.kind === 'file') documentsParent.set(item.document.id, item.document.folder_id)
  }, [items])

  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [anchor, setAnchor] = useState<string | null>(null)
  const [cursor, setCursor] = useState<string | null>(null)
  const [renaming, setRenaming] = useState<string | null>(null)
  const [dropTarget, setDropTarget] = useState<string | null>(null)
  // What "Move to…" moves while its dialog asks where, and the documents whose tags are being chosen.
  const [moving, setMoving] = useState<Item[] | null>(null)
  const [tagging, setTagging] = useState<Document[] | null>(null)
  // How many tiles a row of the grid holds, from the listing's width.
  const [columns, setColumns] = useState(1)
  const menu = useContextMenu()
  const list = useRef<HTMLDivElement>(null)
  // A closed folder held under a drag opens after a moment, so the drag can go deeper.
  const expandTimer = useRef<{ id: number; timer: number } | null>(null)

  // Selection cannot outlive the items it points at.
  const present = new Set(keys)
  const liveSelected = new Set([...selected].filter((key) => present.has(key)))
  const selectedItems = items.filter((item) => liveSelected.has(itemKey(item)))

  useEffect(() => {
    if (cursor) list.current?.querySelector(`[data-key="${CSS.escape(cursor)}"]`)?.scrollIntoView({ block: 'nearest' })
  }, [cursor])

  useEffect(
    () => () => {
      if (expandTimer.current) window.clearTimeout(expandTimer.current.timer)
    },
    [],
  )

  // The grid's scroller, whose width less its padding the tiles share; there before the first page comes.
  const scroller = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const element = scroller.current
    if (!grid || !element) return
    const measure = () => setColumns(Math.max(1, Math.floor((element.clientWidth - 16 + 8) / TILE_WIDTH)))
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(element)
    return () => observer.disconnect()
  }, [grid])

  function select(key: string, event?: { ctrlKey: boolean; metaKey: boolean; shiftKey: boolean }) {
    setCursor(key)
    if (event?.shiftKey && anchor && present.has(anchor)) {
      const [from, to] = [keys.indexOf(anchor), keys.indexOf(key)].sort((a, b) => a - b)
      setSelected(new Set(keys.slice(from, to + 1)))
      return
    }
    setAnchor(key)
    if (event?.ctrlKey || event?.metaKey) {
      setSelected((current) => {
        const next = new Set(current)
        if (next.has(key)) next.delete(key)
        else next.add(key)
        return next
      })
    } else {
      setSelected(new Set([key]))
    }
  }

  function open(item: Item) {
    if (item.kind === 'folder') workspace.goToFolder(item.folder.id)
    else workspace.openDocument(item.document.id)
  }

  /** The folder a document of a flat place is in, by its path, to say where it is. */
  function whereOf(document: Document): string {
    const path = tree.pathTo(document.folder_id).map((folder) => folder.name)
    return path.length ? path.join(' / ') : project.name
  }

  function setExpanded(id: number, expanded: boolean) {
    // Closing a folder over the cursor moves the cursor up to it.
    const at = cursor ? items[keys.indexOf(cursor)] : undefined
    const parent = at && parentOf(at)
    if (!expanded && parent != null && tree.subtree(id).has(parent)) setCursor(`f:${id}`)
    setOpenFolders((current) => (expanded ? [...new Set([...current, id])] : current.filter((open) => open !== id)))
  }

  /** A folder opens in place in the tree, and is gone into in the grid; a file opens in the middle pane. */
  function activate(item: Item) {
    if (item.kind === 'folder' && !grid) setExpanded(item.folder.id, !openSet.has(item.folder.id))
    else open(item)
  }

  /** Put the items in the project's trash, from which the library offers to take them back. */
  function remove(chosen: Item[]) {
    const targets = outermost(chosen, tree)
    if (!targets.length) return
    const folderIds = targets.flatMap((item) => (item.kind === 'folder' ? [item.folder.id] : []))
    const below = new Set(folderIds.flatMap((id) => [...tree.subtree(id)]))
    mutations.trashItems.mutate({
      folderIds,
      documentIds: targets.flatMap((item) => (item.kind === 'file' ? [item.document.id] : [])),
      inFolders: items.flatMap((i) => (i.kind === 'file' && i.document.folder_id != null && below.has(i.document.folder_id) ? [i.document.id] : [])),
      names: targets.map(itemName),
    })
    setSelected(new Set())
    leaving(targets)
  }

  /**
   * The cursor goes to the row after the last one leaving the listing with the items, else before
   * the first, and the keyboard stays in the listing, wherever the change was asked from.
   */
  function leaving(targets: Item[]) {
    const below = new Set(targets.flatMap((item) => (item.kind === 'folder' ? [...tree.subtree(item.folder.id)] : [])))
    const inside = (item: Item) => {
      const parent = parentOf(item)
      return parent != null && below.has(parent)
    }
    setCursor(cursorAfter(keys, new Set(items.filter((item) => targets.includes(item) || inside(item)).map(itemKey))))
    list.current?.focus()
  }

  /** Move the items into a folder chosen from a list, as a drag does, which the library offers to undo. */
  function move(chosen: Item[], targetId: number | null) {
    const targets = outermost(chosen, tree)
    mutations.moveItems.mutate({ ...payloadOf(targets), targetId })
    setSelected(new Set())
    leaving(targets)
  }

  function commitRename(item: Item, value: string) {
    setRenaming(null)
    const name = value.trim()
    if (!name || name === itemName(item)) return
    if (name.includes('/')) {
      mutations.setNotice("A name cannot contain '/'.")
      return
    }
    if (item.kind === 'folder') mutations.renameFolder.mutate({ id: item.folder.id, name })
    else mutations.renameDocument.mutate({ id: item.document.id, filename: name })
  }

  /** A new chat about what was chosen, its first question asked at once when there is one. */
  function askAbout(scope: ScopeIn, question?: string) {
    if (question === undefined) {
      workspace.openChat('new', { about: scope })
      return
    }
    startChat(queryClient, project.id, scope, question).then(
      (chatId) => workspace.openChat(chatId),
      (error: Error) => mutations.setNotice(`Could not start a chat: ${error.message}`),
    )
  }

  /** What chats can be had about the items: about a folder or its summary, a document, or files chosen together, two compared. */
  function askingMenu(targets: Item[]): MenuItem[] {
    const files = targets.flatMap((item) => (item.kind === 'file' ? [item.document] : []))
    if (targets.length === 1 && targets[0].kind === 'folder') {
      const folder = { folder_id: targets[0].folder.id }
      return [
        { label: 'Ask about this folder', icon: MessageSquare, onSelect: () => askAbout(folder) },
        { label: 'Summarise this folder', icon: ScrollText, disabled: asking, onSelect: () => askAbout(folder, SUMMARY_QUESTION) },
      ]
    }
    if (files.length !== targets.length || files.length > SCOPE_DOCUMENTS) return []
    const chosen = { document_ids: files.map((document) => document.id) }
    if (files.length === 1) return [{ label: 'Ask about this document', icon: MessageSquare, onSelect: () => askAbout(chosen) }]
    return [
      { label: `Ask about these ${files.length} documents`, icon: MessageSquare, onSelect: () => askAbout(chosen) },
      ...(files.length === 2
        ? [
            {
              label: 'Compare the two documents',
              icon: GitCompareArrows,
              disabled: asking,
              onSelect: () => askAbout(chosen, comparisonQuestion(files[0].filename, files[1].filename)),
            },
          ]
        : []),
    ]
  }

  /**
   * What whoever may change the project does to several files at once (backend/app/selection.py), a page of them at
   * most: their tags, indexing them again or the rest of the partly indexed, and an export of them alone.
   */
  function chosenMenu(targets: Item[]): MenuItem[] {
    const files = targets.flatMap((item) => (item.kind === 'file' ? [item.document] : []))
    if (!changes || files.length !== targets.length) return []
    if (files.length > MAX_CHOSEN) {
      return [{ label: `Choose at most ${MAX_CHOSEN} files to tag, index or export them together`, disabled: true, onSelect: () => {} }]
    }
    const ids = files.map((document) => document.id)
    const partly = files.filter((document) => unfinishedStages(document).length > 0)
    const missing = [...new Set(partly.flatMap(unfinishedStages))]
    return [
      { label: `Tags of ${files.length} documents…`, icon: TagIcon, onSelect: () => setTagging(files) },
      {
        label: `Index ${files.length} documents again`,
        icon: RefreshCw,
        disabled: files.every((document) => document.status === 'processing'),
        onSelect: () => mutations.reprocessMany.mutate({ ids, rest: false }),
      },
      ...(partly.length
        ? [
            {
              label: `Index the rest of ${partly.length === 1 ? '1 document' : `${partly.length} documents`}`,
              icon: ListChecks,
              disabled: restHeld(missing, health.data) != null,
              onSelect: () => mutations.reprocessMany.mutate({ ids, rest: true }),
            },
          ]
        : []),
      { label: `Export ${files.length} documents`, icon: Download, onSelect: () => mutations.exportChosen.mutate(ids) },
    ]
  }

  /** Save the tags the dialog chose: a document's own as it now carries them, several's as what goes on and comes off. */
  function saveTags(documents: Document[], change: TagsChange) {
    if (!change.add.length && !change.remove.length) return
    const [one] = documents
    if (documents.length === 1) marks.tag.mutate({ id: one.id, tagIds: retagged(one.tag_ids, change) })
    else marks.tagMany.mutate({ ids: documents.map((document) => document.id), change })
  }

  /** What can be done to the items; for several at once, a chat about them and what the role may change. */
  function itemsMenu(targets: Item[]): MenuItem[] {
    // Moving is what a drag does, for the keyboard, and for a pointer that cannot drag.
    const moveTo: MenuItem = {
      label: targets.length > 1 ? `Move ${targets.length} items to…` : 'Move to…',
      icon: FolderInput,
      disabled: placesFor(tree, project.name, payloadOf(outermost(targets, tree))).length === 0,
      onSelect: () => setMoving(targets),
    }
    const asked = askingMenu(targets)
    if (targets.length > 1) {
      const changing: MenuItem[] = changes ? [moveTo, { label: `Delete ${targets.length} items`, icon: Trash2, danger: true, onSelect: () => remove(targets) }] : []
      const groups = [asked, chosenMenu(targets), changing].filter((group) => group.length > 0)
      return groups.flatMap((group, index): MenuItem[] => (index > 0 ? ['separator', ...group] : group))
    }
    const [item] = targets
    const rename: MenuItem = { label: 'Rename', icon: Pencil, shortcut: 'F2', onSelect: () => setRenaming(itemKey(item)) }
    const del: MenuItem = { label: 'Delete', icon: Trash2, shortcut: 'Del', danger: true, onSelect: () => remove([item]) }
    if (item.kind === 'folder') {
      return [{ label: 'Open', onSelect: () => open(item) }, ...asked, ...(changes ? ['separator' as const, rename, moveTo, 'separator' as const, del] : [])]
    }
    const starred = favorites.has(item.document.id)
    const reading: MenuItem[] = [
      { label: 'Open', onSelect: () => open(item) },
      originalFileItem(item.document),
      ...asked,
      // Each reader's own, a viewer's too.
      {
        label: starred ? 'Remove from favourites' : 'Add to favourites',
        icon: starred ? StarOff : Star,
        onSelect: () => marks.star.mutate({ id: item.document.id, starred: !starred }),
      },
    ]
    if (!changes) return reading
    // A document's review moves from its menu, while reviews are rolled out to the organisation.
    const moves: MenuItem[] = reviews
      ? stateMoves(item.document.review).map(({ state, label }) => ({
          label,
          icon: state ? MOVE_ICONS[state] : ClipboardX,
          onSelect: () =>
            review.mutate({ id: item.document.id, change: { state } }, { onError: (error) => mutations.setNotice(error.message) }),
        }))
      : []
    const unfinished = unfinishedStages(item.document)
    // A partly indexed document runs what it went without, once the services for it are back.
    const rest: MenuItem[] = unfinished.length
      ? [
          {
            label: 'Index the rest',
            icon: ListChecks,
            disabled: restHeld(unfinished, health.data) != null,
            onSelect: () => mutations.indexRest.mutate(item.document),
          },
        ]
      : []
    const version: MenuItem[] = onPickVersion
      ? [
          {
            label: 'Upload a new version…',
            icon: FileUp,
            disabled: item.document.status === 'processing',
            onSelect: () => onPickVersion(item.document),
          },
        ]
      : []
    return [
      ...reading,
      { label: 'Tags…', icon: TagIcon, onSelect: () => setTagging([item.document]) },
      rename,
      moveTo,
      ...version,
      ...rest,
      {
        // On a quarantined document this scans it again, for signatures that have changed.
        label: 'Index again',
        icon: RefreshCw,
        disabled: item.document.status === 'processing',
        onSelect: () => mutations.reprocess.mutate(item.document.id),
      },
      ...(moves.length ? ['separator' as const, ...moves] : []),
      'separator',
      del,
    ]
  }

  const backgroundMenu: MenuItem[] = [
    // A flat place holds no folder of its own to make one in, and what is uploaded there goes to the root.
    ...(changes && !flat
      ? ([
          { label: 'New folder', icon: FolderPlus, onSelect: () => onCreatingChange(true) },
          { label: 'Upload files', icon: Upload, onSelect: onPickFiles },
          { label: 'Upload a folder', icon: FolderUp, onSelect: onPickFolder },
          'separator',
        ] satisfies MenuItem[])
      : []),
    { label: 'Select all', shortcut: 'Ctrl+A', disabled: !items.length, onSelect: () => setSelected(new Set(keys)) },
    // Only the tree opens folders in place.
    ...(flat || grid
      ? []
      : ([
          'separator',
          {
            label: 'Expand all',
            icon: ChevronsUpDown,
            // Every folder below this one that has subfolders or files of its own.
            onSelect: () => setOpenFolders((current) => [...new Set([...current, ...expandable(tree, folderId)])]),
          },
          {
            label: 'Collapse all',
            icon: ChevronsDownUp,
            disabled: !entries.some((entry) => entry.depth > 0),
            onSelect: () => {
              const shown = folderId == null ? null : tree.subtree(folderId)
              setOpenFolders((current) => (shown ? current.filter((id) => id === folderId || !shown.has(id)) : []))
            },
          },
        ] satisfies MenuItem[])),
  ]

  function onContextMenu(event: MouseEvent, item?: Item) {
    if (!item) {
      menu.open(event, backgroundMenu)
      return
    }
    const key = itemKey(item)
    if (!liveSelected.has(key)) select(key)
    const targets = liveSelected.has(key) && liveSelected.size > 1 ? selectedItems : [item]
    const offered = itemsMenu(targets)
    if (offered.length) menu.open(event, offered)
  }

  function onKeyDown(event: KeyboardEvent) {
    // The listing's own keys, not a row's button's: Enter there runs the button.
    if (renaming || creating || event.target !== event.currentTarget) return
    const index = cursor ? keys.indexOf(cursor) : -1
    const item = items[index] as Item | undefined
    const moveTo = (next: number) => {
      if (!keys.length) return
      event.preventDefault()
      select(keys[Math.max(0, Math.min(keys.length - 1, next))], { ctrlKey: false, metaKey: false, shiftKey: event.shiftKey })
    }
    // The grid's arrows move by a tile and by a row; its folders are gone into with Enter.
    if (grid && ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key) && !event.altKey) {
      const next = gridMove(event.key, index, columns, keys.length, event.ctrlKey || event.metaKey)
      event.preventDefault()
      if (next !== null) moveTo(next)
      return
    }
    switch (event.key) {
      case 'ArrowDown':
        return moveTo(index < 0 ? 0 : index + 1)
      case 'ArrowUp':
        if (event.altKey) {
          event.preventDefault()
          return goUp()
        }
        return moveTo(index < 0 ? 0 : index - 1)
      case 'ArrowRight':
        // Open a closed folder, or step into an open one.
        if (item?.kind === 'folder') {
          event.preventDefault()
          if (!openSet.has(item.folder.id)) setExpanded(item.folder.id, true)
          else if ((entries[index + 1]?.depth ?? 0) > entries[index].depth) moveTo(index + 1)
        }
        return
      case 'ArrowLeft':
        // Close an open folder, or step out to the folder holding the item.
        if (item) {
          event.preventDefault()
          const parent = parentOf(item)
          if (item.kind === 'folder' && openSet.has(item.folder.id)) setExpanded(item.folder.id, false)
          else if (!flat && parent != null && parent !== folderId) moveTo(keys.indexOf(`f:${parent}`))
        }
        return
      case 'Home':
        return moveTo(0)
      case 'End':
        return moveTo(keys.length - 1)
      case 'Enter':
        if (item) {
          event.preventDefault()
          activate(item)
        }
        return
      case 'Backspace':
        event.preventDefault()
        return goUp()
      case 'F2':
        if (changes && liveSelected.size === 1) {
          event.preventDefault()
          setRenaming([...liveSelected][0])
        }
        return
      case 'Delete':
        if (!changes) return
        event.preventDefault()
        return remove(selectedItems)
      case 'Escape':
        setSelected(new Set())
        return
      case 'a':
        if (event.ctrlKey || event.metaKey) {
          event.preventDefault()
          setSelected(new Set(keys))
        }
        return
    }
  }

  function goUp() {
    const current = folderId == null ? null : tree.byId.get(folderId)
    if (current) workspace.goToFolder(current.parent_id)
  }

  function onDragStart(event: DragEvent, item: Item) {
    const key = itemKey(item)
    // A folder carries its contents, so anything selected inside a dragged folder stays where it is.
    const dragged = outermost(liveSelected.has(key) ? selectedItems : [item], tree)
    if (!liveSelected.has(key)) select(key)
    const payload = payloadOf(dragged)
    onDragChange(payload)
    event.dataTransfer.setData(ITEMS_TYPE, JSON.stringify(payload))
    event.dataTransfer.effectAllowed = 'move'
  }

  function cancelExpand() {
    if (expandTimer.current) window.clearTimeout(expandTimer.current.timer)
    expandTimer.current = null
  }

  function onFolderDragOver(event: DragEvent, folder: Folder) {
    if (hasItems(event)) {
      if (!canDropItems(tree, folder.id, dragging.current)) return
      event.dataTransfer.dropEffect = 'move'
    } else if (hasFiles(event)) {
      event.dataTransfer.dropEffect = 'copy'
    } else {
      return
    }
    event.preventDefault()
    event.stopPropagation()
    setDropTarget(`f:${folder.id}`)
    if (!openSet.has(folder.id) && expandTimer.current?.id !== folder.id) {
      cancelExpand()
      const timer = window.setTimeout(() => {
        expandTimer.current = null
        setOpenFolders((current) => [...new Set([...current, folder.id])])
      }, 600)
      expandTimer.current = { id: folder.id, timer }
    }
  }

  function onFolderDrop(event: DragEvent, folder: Folder) {
    setDropTarget(null)
    cancelExpand()
    if (hasItems(event)) {
      event.preventDefault()
      event.stopPropagation()
      const payload = JSON.parse(event.dataTransfer.getData(ITEMS_TYPE)) as DragPayload
      if (canDropItems(tree, folder.id, payload)) mutations.moveItems.mutate({ ...payload, targetId: folder.id })
    } else if (hasFiles(event)) {
      event.preventDefault()
      event.stopPropagation()
      onUploadDropped(event.dataTransfer, folder.id)
    }
  }

  const empty = lines.length === 0 && !creating
  const emptyState = empty &&
    (flat ? (
      <EmptyState icon={<FolderIcon />} title={emptyPlaceWords(place).title}>
        {emptyPlaceWords(place).body}
      </EmptyState>
    ) : (
      <EmptyState icon={<FolderIcon />} title={folderId == null ? 'This library is empty' : 'This folder is empty'}>
        {changes ? (
          <>
            Upload files or drop them here. Each one is parsed, outlined and indexed in the background.
            <br />
            {ACCEPTED_TYPES_LABEL}.
            <div className="mt-4 flex flex-wrap justify-center gap-2">
              <Button variant="primary" icon={<Upload className="size-4" />} onClick={onPickFiles}>
                Upload files
              </Button>
              <Button icon={<FolderUp className="size-4" />} onClick={onPickFolder}>
                Upload a folder
              </Button>
            </div>
          </>
        ) : (
          'Nothing has been uploaded here yet. Your role is viewer, so the files a member uploads show here for you to read and ask about.'
        )}
      </EmptyState>
    ))
  const ghost = creating && (
    <GhostFolderRow
      onCommit={(name) => {
        onCreatingChange(false)
        const trimmed = name.trim()
        if (!trimmed) return
        if (trimmed.includes('/')) {
          mutations.setNotice("A name cannot contain '/'.")
          return
        }
        mutations.createFolder.mutate({ name: trimmed, parentId: folderId })
      }}
      onCancel={() => onCreatingChange(false)}
    />
  )
  const gapOf = (line: Gap) => {
    const held = listings.get(placeKey(line.place))
    return (
      <GapRow
        key={`gap:${placeKey(line.place) ?? 'root'}`}
        gap={line}
        busy={growing(held?.data)}
        onMore={() => showMore(queryClient, project.id, line.place, arrangement)}
        onRetry={() => held?.refetch()}
      />
    )
  }
  const propsOf = (item: Item, depth: number): RowProps => {
    const key = itemKey(item)
    return {
      item,
      depth,
      expanded: item.kind === 'folder' && openSet.has(item.folder.id),
      selected: liveSelected.has(key),
      isCursor: cursor === key,
      renaming: renaming === key,
      // A flat place holds no folder to drag a file into.
      movable: changes && !flat,
      dropping: dropTarget === key,
      tags: item.kind === 'file' ? tagsOf(item.document, tags) : [],
      favorite: item.kind === 'file' && favorites.has(item.document.id),
      where: flat && item.kind === 'file' ? whereOf(item.document) : undefined,
      onSelect: (event) => select(key, event),
      onOpen: () => activate(item),
      onToggle: () => item.kind === 'folder' && setExpanded(item.folder.id, !openSet.has(item.folder.id)),
      onContextMenu: (event) => onContextMenu(event, item),
      onMenuButton: (element) => {
        select(key)
        menu.openAt(element, itemsMenu([item]))
      },
      onRename: (value) => commitRename(item, value),
      onCancelRename: () => setRenaming(null),
      onDragStart: (event) => onDragStart(event, item),
      onDragEnd: () => {
        onDragChange(null)
        setDropTarget(null)
        cancelExpand()
      },
      onDragOver: changes && item.kind === 'folder' ? (event) => onFolderDragOver(event, item.folder) : undefined,
      onDragLeave: (event) => {
        if (event.currentTarget.contains(event.relatedTarget as Node | null)) return
        if (dropTarget === key) setDropTarget(null)
        if (item.kind === 'folder' && expandTimer.current?.id === item.folder.id) cancelExpand()
      },
      onDrop: changes && item.kind === 'folder' ? (event) => onFolderDrop(event, item.folder) : undefined,
      folderCount: item.kind === 'folder' ? (tree.children.get(item.folder.id)?.length ?? 0) + item.folder.document_count : undefined,
    }
  }
  // The listing is one stop of the keyboard, its cursor the row or tile a screen reader reads out; busy while a page of
  // it comes, when its line says so in a row's place.
  const keyboard = {
    ref: list,
    'aria-label': 'Files',
    'aria-busy': lines.some((line) => !isEntry(line) && line.gap === 'loading') || undefined,
    'aria-multiselectable': true,
    'aria-activedescendant': cursor && present.has(cursor) ? rowId(cursor) : undefined,
    tabIndex: 0,
    // Tabbed into without a cursor, the cursor starts on the first row; a click puts it on its own.
    onFocus: (event: FocusEvent<HTMLDivElement>) => {
      const tabbed = event.target === event.currentTarget && event.currentTarget.matches(':focus-visible')
      if (tabbed && !(cursor && present.has(cursor)) && keys.length) setCursor(keys[0])
    },
    onKeyDown,
  }
  const background = {
    onContextMenu: (event: MouseEvent) => onContextMenu(event),
    onClick: (event: MouseEvent) => {
      if (event.target === event.currentTarget) setSelected(new Set())
    },
  }
  // The grid's rows, each `columns` tiles, a gap after a place's files on a row of its own.
  const gridRows: Line[][] = []
  for (const line of lines) {
    const last = gridRows.at(-1)
    if (isEntry(line) && last && last.length < columns && isEntry(last[0])) last.push(line)
    else gridRows.push([line])
  }
  return (
    <>
      {grid ? (
        <div ref={scroller} {...background} className="min-h-0 flex-1 overflow-y-auto p-2">
          {emptyState}
          {ghost}
          {lines.length > 0 && (
            <div {...keyboard} role="grid" className="group/list space-y-2 outline-none">
              {gridRows.map((row) =>
                isEntry(row[0]) ? (
                  <div
                    key={itemKey(row[0].item)}
                    role="row"
                    className="grid gap-2"
                    style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}
                  >
                    {row.flatMap((line) => (isEntry(line) ? [<Tile key={itemKey(line.item)} {...propsOf(line.item, 0)} />] : []))}
                  </div>
                ) : (
                  <div key={`gap:${placeKey((row[0] as Gap).place) ?? 'root'}`} role="row">
                    <div role="gridcell">{gapOf(row[0] as Gap)}</div>
                  </div>
                ),
              )}
            </div>
          )}
          {menu.element}
        </div>
      ) : (
        <div {...keyboard} {...background} role="tree" className="group/list min-h-0 flex-1 overflow-y-auto p-2 outline-none">
          {emptyState}
          {ghost}
          {lines.map((line) => (isEntry(line) ? <Row key={itemKey(line.item)} {...propsOf(line.item, line.depth)} /> : gapOf(line)))}
          {menu.element}
        </div>
      )}
      {moving && (
        <MoveDialog
          items={moving}
          places={placesFor(tree, project.name, payloadOf(outermost(moving, tree)))}
          onMove={(targetId) => move(moving, targetId)}
          onClose={() => setMoving(null)}
        />
      )}
      {tagging && (
        <DocumentTagsDialog
          documents={tagging}
          tags={tags}
          room={canTag}
          onCreate={(name, color) => organizing.createTag.mutateAsync({ name, color })}
          onSave={(change) => saveTags(tagging, change)}
          onClose={() => setTagging(null)}
        />
      )}
    </>
  )
}

interface MoveDialogProps {
  items: Item[]
  places: Place[]
  onMove: (targetId: number | null) => void
  onClose: () => void
}

/** Asks where items go, from the folders they may go into, and moves them there. */
function MoveDialog({ items, places, onMove, onClose }: MoveDialogProps) {
  const [target, setTarget] = useState(places[0]?.id ?? null)
  const one = items.length === 1
  return (
    <Confirm
      title={one ? `Move “${itemName(items[0])}”` : `Move ${items.length} items`}
      body={`Choose the folder ${one ? 'it goes' : 'they go'} into. The library then offers to undo the move.`}
      action="Move"
      danger={false}
      onConfirm={() => onMove(target)}
      onClose={onClose}
    >
      <SelectField
        label="Folder"
        data-autofocus
        value={target ?? ''}
        onChange={(event) => setTarget(event.target.value ? Number(event.target.value) : null)}
      >
        {places.map((place) => (
          <option key={place.id ?? ''} value={place.id ?? ''}>
            {place.label}
          </option>
        ))}
      </SelectField>
    </Confirm>
  )
}

/** A folder items may be moved into, or the top of the library (`id` null), named by its path. */
interface Place {
  id: number | null
  label: string
}

/**
 * Where the items may go, in the library's order: its top and every folder, but the one they are
 * all in already, and a folder among them or inside one.
 */
function placesFor(tree: FolderTree, top: string, payload: DragPayload): Place[] {
  const found: Place[] = [{ id: null, label: top }]
  const visit = (parentId: number | null, path: string[]) => {
    for (const folder of tree.children.get(parentId) ?? []) {
      found.push({ id: folder.id, label: [...path, folder.name].join(' / ') })
      visit(folder.id, [...path, folder.name])
    }
  }
  visit(null, [top])
  return found.filter((place) => canDropItems(tree, place.id, payload))
}

/** What a drag or a move carries: the folders and files, the folders taking what is in them. */
function payloadOf(items: Item[]): DragPayload {
  return {
    folderIds: items.flatMap((item) => (item.kind === 'folder' ? [item.folder.id] : [])),
    documentIds: items.flatMap((item) => (item.kind === 'file' ? [item.document.id] : [])),
  }
}

interface ListingResult {
  data: Listing | undefined
  isPending: boolean
  isError: boolean
}

/** The folders whose files the tree shows: the shown one, and each open one below it with files of its own. */
function foldersShown(tree: FolderTree, rootId: number | null, open: Set<number>): (number | null)[] {
  const shown: (number | null)[] = [rootId]
  const visit = (folderId: number | null) => {
    for (const folder of tree.children.get(folderId) ?? []) {
      if (!open.has(folder.id)) continue
      if (folder.document_count > 0) shown.push(folder.id)
      visit(folder.id)
    }
  }
  visit(rootId)
  return shown
}

/**
 * The lines under a folder as the tree shows them: each open folder followed by its contents,
 * the subfolders by name and then the files in the order the server lists them.
 */
function treeLines(
  tree: FolderTree,
  listings: Map<number | string | null, ListingResult>,
  rootId: number | null,
  open: Set<number>,
): Line[] {
  const lines: Line[] = []
  const visit = (folderId: number | null, depth: number) => {
    for (const folder of tree.children.get(folderId) ?? []) {
      lines.push({ item: { kind: 'folder', folder }, depth })
      if (open.has(folder.id)) visit(folder.id, depth + 1)
    }
    lines.push(...listingLines({ folder: folderId }, listings.get(folderId), depth))
  }
  visit(rootId, 0)
  return lines
}

/** The lines of a place that is not a folder: its documents, wherever they are, and the gap after them. */
function flatLines(place: ListingPlace, listing: ListingResult | undefined): Line[] {
  return listingLines(place, listing, 0)
}

function listingLines(place: ListingPlace, listing: ListingResult | undefined, depth: number): Line[] {
  if (!listing) return []
  const lines: Line[] = listed(listing.data).map((document) => ({ item: { kind: 'file', document }, depth }))
  if (listing.isPending) lines.push({ gap: 'loading', place, depth })
  else if (listing.isError && !listing.data) lines.push({ gap: 'failed', place, depth })
  else if (hasMore(listing.data)) lines.push({ gap: 'more', place, depth })
  return lines
}

/** The folders below this one that have anything in them to show when opened. */
function expandable(tree: FolderTree, rootId: number | null): number[] {
  return [...tree.byId.values()]
    .filter((folder) => folder.id !== rootId && (tree.children.has(folder.id) || folder.document_count > 0))
    .filter((folder) => rootId == null || tree.pathTo(folder.parent_id).some((above) => above.id === rootId))
    .map((folder) => folder.id)
}

/** The items not inside a folder that is itself among them; acting on a folder covers what it holds. */
function outermost(items: Item[], tree: FolderTree): Item[] {
  const folderIds = new Set(items.flatMap((item) => (item.kind === 'folder' ? [item.folder.id] : [])))
  return items.filter((item) => !tree.pathTo(parentOf(item)).some((folder) => folderIds.has(folder.id)))
}

/** A row's id, which the listing names as the one its cursor is on. */
function rowId(key: string): string {
  return `library-${key.replace(':', '-')}`
}

interface RowProps {
  item: Item
  /** How far below the shown folder the item sits, which is how far it is indented. */
  depth: number
  /** A file's tags, as its project holds them. */
  tags: Tag[]
  /** Whether the reader starred the file. */
  favorite: boolean
  /** In a place across the project's folders, the folder a file is in, by its path. */
  where?: string
  /** Whether a folder shows its contents. */
  expanded: boolean
  selected: boolean
  isCursor: boolean
  renaming: boolean
  /** Whether the item may be dragged into another folder. */
  movable: boolean
  dropping: boolean
  folderCount?: number
  onSelect: (event: MouseEvent) => void
  onOpen: () => void
  onToggle: () => void
  onContextMenu: (event: MouseEvent) => void
  onMenuButton: (element: HTMLElement) => void
  onRename: (value: string) => void
  onCancelRename: () => void
  onDragStart: (event: DragEvent) => void
  onDragEnd: () => void
  onDragOver?: (event: DragEvent) => void
  onDragLeave: (event: DragEvent) => void
  onDrop?: (event: DragEvent) => void
}

function ItemIcon({ item, className }: { item: Item; className: string }) {
  if (item.kind === 'folder') return <FolderIcon className={`${className} fill-accent/15 text-accent`} />
  const kind = fileKind(item.document)
  return createElement(kind.icon, { className: `${className} ${kind.color}`, 'aria-label': kind.label })
}

/**
 * What describes a file's row or tile, by the ids of what says it: its state as the status icon gives it, its review, its
 * tags, its star and where it is, rather than all it holds.
 */
function describedBy(id: string, props: RowProps): string | undefined {
  if (props.item.kind === 'folder') return undefined
  const parts = [`${id}-status`, props.item.document.review && `${id}-review`, props.tags.length > 0 && `${id}-tags`]
  return [...parts, props.favorite && `${id}-star`, props.where && `${id}-where`].filter(Boolean).join(' ')
}

/** A star on a file the reader made a favourite, read out as one in its description (a `hidden` word, as `TagChips`'). */
function FavoriteMark({ id }: { id: string }) {
  return (
    <span className="ml-1.5 flex shrink-0">
      <Star aria-hidden className="size-3.5 fill-warn text-warn" />
      <span id={id} hidden>
        Favourite
      </span>
    </span>
  )
}

/** Where a file of a place across the folders is, for the eye beside its name and for its description apart. */
function Where({ id, where, className = '' }: { id: string; where: string; className?: string }) {
  return (
    <>
      <Seen text={`in ${where}`} className={`text-[11px] text-muted ${className}`} />
      <span id={id} hidden>
        in {where}
      </span>
    </>
  )
}

function Row(props: RowProps) {
  const { item, depth, expanded, selected, isCursor, renaming, movable, dropping, folderCount } = props
  const name = itemName(item)
  const id = rowId(itemKey(item))

  const handlers = {
    role: 'treeitem' as const,
    id,
    // Its name, and for a file what describes it.
    'aria-label': name,
    'aria-describedby': describedBy(id, props),
    'aria-selected': selected,
    'data-key': itemKey(item),
    draggable: movable && !renaming,
    onClick: (event: MouseEvent) => {
      event.stopPropagation()
      if (!renaming) props.onSelect(event)
    },
    onDoubleClick: () => !renaming && props.onOpen(),
    onContextMenu: props.onContextMenu,
    onDragStart: props.onDragStart,
    onDragEnd: props.onDragEnd,
    onDragOver: props.onDragOver,
    onDragLeave: props.onDragLeave,
    onDrop: props.onDrop,
  }
  // The keyboard cursor is only marked while the keyboard is in the listing.
  const state = `${selected ? 'bg-accent-soft' : 'hover:bg-sunken'} ${isCursor ? 'group-focus/list:ring-1 group-focus/list:ring-accent/50' : ''} ${
    dropping ? 'bg-accent-soft ring-2 ring-accent' : ''
  }`
  const rename = (
    <RenameInput value={name} isFile={item.kind === 'file'} onCommit={props.onRename} onCancel={props.onCancelRename} />
  )
  const menuButton = (
    <IconButton
      label="More actions"
      // Tab goes from the listing to the actions of the row its cursor is on, not through every row's.
      tabIndex={isCursor ? 0 : -1}
      className={`size-6! shrink-0 ${selected ? '' : 'opacity-0 group-hover:opacity-100 focus-visible:opacity-100'}`}
      onClick={(event) => {
        event.stopPropagation()
        props.onMenuButton(event.currentTarget)
      }}
    >
      <MoreHorizontal className="size-4" />
    </IconButton>
  )

  const folder = item.kind === 'folder'
  return (
    <div
      {...handlers}
      aria-level={depth + 1}
      aria-expanded={folder ? expanded : undefined}
      className={`group flex h-7 items-center rounded-md pr-0.5 pl-1.5 text-[13px] transition-colors select-none ${state}`}
    >
      {/* A guide line for each level above, under the chevron of the folder it belongs to. */}
      {Array.from({ length: depth }, (_, level) => (
        <span
          key={level}
          aria-hidden
          className="relative w-4 shrink-0 self-stretch before:absolute before:inset-y-0 before:left-1/2 before:border-l before:border-line"
        />
      ))}
      {folder && (folderCount ?? 0) > 0 ? (
        <button
          type="button"
          tabIndex={-1}
          aria-label={expanded ? 'Collapse' : 'Expand'}
          onClick={(event) => {
            event.stopPropagation()
            props.onToggle()
          }}
          onDoubleClick={(event) => event.stopPropagation()}
          className="flex size-4 shrink-0 items-center justify-center rounded-sm text-muted transition hover:text-ink"
        >
          <ChevronRight className={`size-3.5 transition-transform ${expanded ? 'rotate-90' : ''}`} />
        </button>
      ) : (
        <span className="w-4 shrink-0" />
      )}
      <ItemIcon item={item} className="ml-1 size-4 shrink-0" />
      <div className="ml-2 min-w-0 flex-1">
        {renaming ? (
          rename
        ) : (
          <p className="truncate" title={item.kind === 'file' ? (item.document.title ?? name) : name}>
            {name}
            {props.where && <Where id={`${id}-where`} where={props.where} className="ml-1.5" />}
          </p>
        )}
      </div>
      {!renaming && <TagChips tags={props.tags} id={`${id}-tags`} />}
      {props.favorite && <FavoriteMark id={`${id}-star`} />}
      {item.kind === 'file' && item.document.review && (
        <span className="ml-2 flex">
          <ReviewMark id={`${id}-review`} document={item.document} />
        </span>
      )}
      {item.kind === 'file' && (
        <span className="ml-2 flex">
          <StatusIcon id={`${id}-status`} document={item.document} />
        </span>
      )}
      <span className="ml-1 flex">{menuButton}</span>
    </div>
  )
}

/**
 * A folder or a file as a tile of the grid: a file's thumbnail where the server draws one (a PDF, a scan, an image),
 * else its kind's icon, with its name, state and menu under it, and then its review, tags and star.
 */
function Tile(props: RowProps) {
  const { item, selected, isCursor, renaming, movable, dropping, folderCount } = props
  const name = itemName(item)
  const id = rowId(itemKey(item))
  // A thumbnail the server could not draw (an unreadable page, a missing file) leaves the kind's icon in its place.
  const [broken, setBroken] = useState(false)
  const picture = item.kind === 'file' && hasThumbnail(item.document) && !broken
  const state = `${selected ? 'border-accent bg-accent-soft' : 'border-line bg-surface hover:bg-sunken'} ${
    isCursor ? 'group-focus/list:ring-2 group-focus/list:ring-accent/50' : ''
  } ${dropping ? 'bg-accent-soft ring-2 ring-accent' : ''}`
  return (
    <div
      role="gridcell"
      id={id}
      aria-label={name}
      aria-describedby={describedBy(id, props)}
      aria-selected={selected}
      data-key={itemKey(item)}
      draggable={movable && !renaming}
      onClick={(event) => {
        event.stopPropagation()
        if (!renaming) props.onSelect(event)
      }}
      onDoubleClick={() => !renaming && props.onOpen()}
      onContextMenu={props.onContextMenu}
      onDragStart={props.onDragStart}
      onDragEnd={props.onDragEnd}
      onDragOver={props.onDragOver}
      onDragLeave={props.onDragLeave}
      onDrop={props.onDrop}
      className={`group flex min-w-0 flex-col rounded-lg border p-1.5 text-[13px] transition-colors select-none ${state}`}
    >
      <div className="flex aspect-[4/3] items-center justify-center overflow-hidden rounded-md bg-sunken">
        {picture ? (
          <img
            src={thumbnailUrl(item.document)}
            alt=""
            loading="lazy"
            draggable={false}
            onError={() => setBroken(true)}
            className="size-full object-cover object-top"
          />
        ) : (
          <ItemIcon item={item} className="size-8" />
        )}
      </div>
      <div className="mt-1.5 flex min-w-0 items-center gap-1">
        <div className="min-w-0 flex-1">
          {renaming ? (
            <RenameInput value={name} isFile={item.kind === 'file'} onCommit={props.onRename} onCancel={props.onCancelRename} />
          ) : (
            <p className="truncate" title={item.kind === 'file' ? (item.document.title ?? name) : name}>
              {name}
            </p>
          )}
        </div>
        {item.kind === 'file' && <StatusIcon id={`${id}-status`} document={item.document} />}
        <IconButton
          label="More actions"
          // Tab goes from the grid to the actions of the tile its cursor is on, not through every tile's.
          tabIndex={isCursor ? 0 : -1}
          className={`size-6! shrink-0 ${selected ? '' : 'opacity-0 group-hover:opacity-100 focus-visible:opacity-100'}`}
          onClick={(event) => {
            event.stopPropagation()
            props.onMenuButton(event.currentTarget)
          }}
        >
          <MoreHorizontal className="size-4" />
        </IconButton>
      </div>
      <div className="flex min-h-5 min-w-0 items-center text-[11px] text-muted">
        {item.kind === 'folder' && <Seen text={folderCount === 1 ? '1 item' : `${folderCount ?? 0} items`} />}
        {item.kind === 'file' && item.document.review && <ReviewMark id={`${id}-review`} document={item.document} />}
        <TagChips tags={props.tags} id={`${id}-tags`} shown={1} />
        {props.favorite && <FavoriteMark id={`${id}-star`} />}
        {props.where && <Where id={`${id}-where`} where={props.where} className="ml-1.5 min-w-0 truncate" />}
      </div>
    </div>
  )
}

interface GapRowProps {
  gap: Gap
  busy: boolean
  onMore: () => void
  onRetry: () => void
}

/** The line at the end of a folder's files: a spinner while they come, the way to the next page, or to try again. */
function GapRow({ gap, busy, onMore, onRetry }: GapRowProps) {
  const button = 'rounded px-1.5 py-0.5 text-accent transition hover:bg-accent-soft disabled:text-muted'
  return (
    <div className="flex h-7 items-center pl-1.5 text-[13px] text-muted">
      {Array.from({ length: gap.depth }, (_, level) => (
        <span
          key={level}
          aria-hidden
          className="relative w-4 shrink-0 self-stretch before:absolute before:inset-y-0 before:left-1/2 before:border-l before:border-line"
        />
      ))}
      <span className="w-4 shrink-0" />
      <span className="ml-1 flex items-center gap-2">
        {gap.gap === 'loading' ? (
          <>
            {/* The words say it: a named spinner would be an image among the tree's rows, which a tree may not hold. */}
            <span aria-hidden className="inline-flex">
              <Spinner />
            </span>
            Loading files…
          </>
        ) : gap.gap === 'failed' ? (
          <>
            These files could not be listed.
            <button type="button" className={button} onClick={onRetry}>
              Try again
            </button>
          </>
        ) : (
          <button type="button" className={button} onClick={onMore} disabled={busy}>
            {busy ? 'Loading more files…' : 'Show more files'}
          </button>
        )}
      </span>
    </div>
  )
}

interface RenameInputProps {
  value: string
  isFile: boolean
  onCommit: (value: string) => void
  onCancel: () => void
}

/** An inline name editor. Enter or leaving the field commits; Escape cancels and nothing else fires. */
function RenameInput({ value, isFile, onCommit, onCancel }: RenameInputProps) {
  // Once committed or cancelled, the blur that follows changes nothing.
  const done = useRef(false)
  function finish(name: string | null) {
    if (done.current) return
    done.current = true
    if (name === null) onCancel()
    else onCommit(name)
  }
  return (
    <input
      autoFocus
      defaultValue={value}
      aria-label="New name"
      maxLength={isFile ? 500 : 200}
      onFocus={(event) => {
        // Select the name but not the extension, which is kept anyway.
        const dot = isFile ? value.lastIndexOf('.') : -1
        event.target.setSelectionRange(0, dot > 0 ? dot : value.length)
      }}
      onClick={(event) => event.stopPropagation()}
      onDoubleClick={(event) => event.stopPropagation()}
      onKeyDown={(event) => {
        event.stopPropagation()
        if (event.key !== 'Enter' && event.key !== 'Escape') return
        finish(event.key === 'Enter' ? event.currentTarget.value : null)
        // The field goes, and the keyboard goes on in the listing it was in.
        event.currentTarget.closest<HTMLElement>('[role="tree"]')?.focus()
      }}
      onBlur={(event) => finish(event.target.value)}
      className="field rounded px-1.5 py-0.5 text-[13px]"
    />
  )
}

interface GhostFolderRowProps {
  onCommit: (name: string) => void
  onCancel: () => void
}

/** The folder being created, in the place it will appear once named. */
function GhostFolderRow({ onCommit, onCancel }: GhostFolderRowProps) {
  return (
    <div className="flex h-7 items-center rounded-md bg-accent-soft pr-0.5 pl-1.5 text-[13px]">
      <span className="w-4 shrink-0" />
      <FolderIcon className="ml-1 size-4 shrink-0 fill-accent/15 text-accent" />
      <div className="ml-2 min-w-0 flex-1">
        <RenameInput value="New folder" isFile={false} onCommit={onCommit} onCancel={onCancel} />
      </div>
    </div>
  )
}

interface SearchResultsProps {
  search: LibrarySearch
  tags: Tag[]
  folders: Folder[]
  /** Whether documents' reviews are rolled out to the organisation, which a search may then be narrowed by. */
  reviews: boolean
  /** The search box, which takes the focus once what held it goes. */
  searchBox: RefObject<HTMLInputElement | null>
}

function SearchResults({ search, tags, folders, reviews, searchBox }: SearchResultsProps) {
  const workspace = useWorkspace()
  const { q: query, filter } = search
  const [filtering, setFiltering] = useState(false)
  const filterButton = useRef<HTMLButtonElement>(null)
  const results = useQuery({
    queryKey: ['search', workspace.project.id, query, filter],
    queryFn: () => api.search(workspace.project.id, query, filter),
    enabled: query !== '',
  })
  const folder = folderChoices(folders).find((choice) => choice.id === filter.folder_id)?.label ?? null
  // The search's folder is the library's, which the path above shows.
  const parts = filterParts({ ...filter, folder_id: null }, tags, folder)
  const hits = results.data?.hits ?? []
  const scope = filter.folder_id == null ? null : (folder ?? 'its folder')
  const within = query !== '' && hits.length > 0 && filter.mentions.length < MAX_WITHIN

  return (
    <div className="min-h-0 flex-1 space-y-2 overflow-y-auto p-3">
      <div className="flex flex-wrap items-center gap-1.5">
        <Button ref={filterButton} icon={<SlidersHorizontal className="size-3.5" />} onClick={() => setFiltering(true)} className="h-7 px-2 text-xs">
          Filter
        </Button>
        {parts.map(({ part, words }) => (
          <span key={String(part)} className="inline-flex max-w-full min-w-0 items-center gap-0.5 rounded-md bg-accent-soft py-px pr-0.5 pl-2 text-xs text-accent">
            <span className="truncate">{words}</span>
            <IconButton
              label={`Take off: ${words}`}
              className="size-5! text-accent hover:text-ink"
              onClick={() => {
                workspace.showSearch({ q: query, filter: withoutPart(filter, part) })
                filterButton.current?.focus()
              }}
            >
              <X className="size-3" />
            </IconButton>
          </span>
        ))}
        <span className="ml-auto flex gap-1.5">
          {within && (
            <Button
              icon={<Search className="size-3.5" />}
              title="Search again only in the documents with a passage that holds every one of these words"
              className="h-7 px-2 text-xs"
              onClick={() => {
                workspace.showSearch({ q: '', filter: { ...filter, mentions: [...filter.mentions, query] } })
                searchBox.current?.focus()
              }}
            >
              Search within these
            </Button>
          )}
        </span>
      </div>
      {!query && (
        <EmptyState icon={<Search />} title="What should be searched for?">
          {filterWords({ ...filter, folder_id: null }, tags, null)}
          {scope ? `, in ${scope}` : ''}. Type words in the search box to search them.
        </EmptyState>
      )}
      {results.isPending && query !== '' && <Loading />}
      {results.isError && <Notice tone="bad">Search failed: {results.error.message}</Notice>}
      {results.data && !results.data.semantic && (
        <Notice>The embedding service is offline, so these are keyword matches only.</Notice>
      )}
      {results.data && hits.length === 0 && (
        <EmptyState title={`Nothing found for "${query}"${scope ? ` in ${scope}` : ''}`}>
          Results combine exact word matches with matches on meaning.
          {/* A narrowed search may miss what the rest of the project holds. */}
          {(scope || narrows({ ...filter, folder_id: null })) && (
            <div className="mt-4">
              <Button icon={<Search className="size-4" />} onClick={() => workspace.showSearch({ q: query, filter: EMPTY_FILTER })}>
                Search all of {workspace.project.name}
              </Button>
            </div>
          )}
        </EmptyState>
      )}
      {hits.map((hit) => (
        <Hit key={hit.passage_id} hit={hit} onOpen={() => workspace.openDocument(hit.document_id, { passage: hit.passage_id })} />
      ))}
      {filtering && (
        <SearchFilterEditor
          filter={filter}
          tags={tags}
          folders={folders}
          reviews={reviews}
          onApply={(chosen) => workspace.showSearch({ q: query, filter: chosen })}
          onClose={() => setFiltering(false)}
        />
      )}
    </div>
  )
}

/** The search's filter form, with the types the model gave the project's documents to choose from, asked for as it opens. */
function SearchFilterEditor(props: Omit<Parameters<typeof SearchFilterDialog>[0], 'types'>) {
  const { project } = useWorkspace()
  const types = useDocumentTypes(project.id)
  return <SearchFilterDialog {...props} types={types.data ?? []} />
}

/** A passage a search found, which opens the document at it; `project` names where it is, for a search of several projects. */
export function Hit({ hit, project, onOpen }: { hit: SearchHit; project?: string; onOpen: () => void }) {
  const location = [project, sectionPath(hit.context, hit.document_title), pageLabel(hit.page_start, hit.page_end)]
    .filter(Boolean)
    .join(' · ')
  // Which ranking found the passage: exact words, meaning, or both.
  const found = [hit.keyword_rank && 'words', hit.vector_rank && 'meaning'].filter(Boolean).join(' + ')

  return (
    <button
      type="button"
      onClick={onOpen}
      className="block w-full rounded-lg border border-line bg-surface px-4 py-3 text-left shadow-xs transition hover:border-ink/15 hover:shadow-sm"
    >
      <div className="flex items-baseline justify-between gap-3">
        <p className="min-w-0 truncate text-[13px] font-semibold">
          {hit.document_title}
          {hit.doc_type && (
            <span className="ml-2 align-middle">
              <Chip>{hit.doc_type}</Chip>
            </span>
          )}
        </p>
        <span className="shrink-0 text-[11px] text-muted" title="How this passage was found">
          {found}
        </span>
      </div>
      {location && <p className="mt-0.5 text-xs text-muted">{location}</p>}
      <p className="mt-2 text-[13px] leading-relaxed text-ink/90">
        {hit.snippet.map((segment, index) =>
          segment.match ? <mark key={index}>{segment.text}</mark> : <span key={index}>{segment.text}</span>,
        )}
      </p>
    </button>
  )
}
