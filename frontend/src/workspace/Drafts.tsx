import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, FileText, LayoutTemplate, ListChecks, PenLine, Pencil, RotateCcw, Trash2 } from 'lucide-react'
import { useEffect, useId, useRef, useState } from 'react'
import { ApiError, api, mayChange } from '../api.ts'
import type { Document, DraftKind, DraftOffer, DraftVersion, Source } from '../api.ts'
import { useMe } from '../auth.ts'
import { writeOffered } from '../chat.ts'
import { Confirm } from '../components/Confirm.tsx'
import { DownloadText } from '../components/DownloadMenu.tsx'
import { Button, Checkbox, EmptyState, FIELD, IconButton, Loading, Notice, Segmented, SelectField, Spinner } from '../components/ui.tsx'
import { draftTemplatesKey, writtenToWords } from '../draftTemplates.ts'
import {
  DRAFT_KINDS,
  DRAFT_NOTE,
  DRAFT_POLL,
  disputeLetter,
  draftByline,
  draftDeletionQuestion,
  draftFileUrl,
  draftKey,
  draftProblem,
  draftStatusWords,
  draftVersionKey,
  draftsKey,
  offerWords,
  versionWords,
  writing,
} from '../drafts.ts'
import { useStatus } from '../hooks.ts'
import { describeAnswerUsage } from '../usage.ts'
import { CitedText } from './CitedText.tsx'
import { Refused } from './Findings.tsx'
import { useWorkspace } from './state.ts'

/**
 * The project's drafts, in the library's place: a new one asked of the model, of a kind, about the documents open
 * beside the library or what the member says, and the drafts before, newest first, each opening in the same place.
 */
export function DraftsView() {
  const workspace = useWorkspace()
  const { project } = workspace
  const changes = mayChange(useMe().role)
  const heading = useRef<HTMLHeadingElement>(null)
  // Opened from a button that went with the view before it: the keyboard goes on from here.
  useEffect(() => {
    if (window.document.activeElement === window.document.body) heading.current?.focus()
  }, [])
  const drafts = useInfiniteQuery({
    queryKey: draftsKey(project.id),
    queryFn: ({ pageParam }) => api.drafts(project.id, pageParam),
    initialPageParam: null as number | null,
    getNextPageParam: (page) => page.next_before,
    refetchInterval: (query) => (query.state.data?.pages[0]?.drafts.some(writing) ? DRAFT_POLL : false),
  })
  const listed = drafts.data?.pages.flatMap((page) => page.drafts) ?? []

  return (
    <div className="flex h-full min-h-0 flex-col bg-page">
      <div className="flex h-11 shrink-0 items-center gap-1.5 border-b border-line bg-surface px-2">
        <IconButton label="Back to the library" onClick={() => workspace.goToFolder(null)}>
          <ArrowLeft className="size-4" />
        </IconButton>
        <h2 ref={heading} tabIndex={-1} className="flex-1 truncate text-[13px] font-semibold">
          Drafts
        </h2>
        <Button icon={<LayoutTemplate className="size-4" />} onClick={workspace.showDraftTemplates}>
          Templates
        </Button>
      </div>
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3">
        <p className="text-xs text-muted">
          Letters, emails, memos, tables and reports the model writes from the project’s documents, citing where each figure
          is written, for the members to edit and send.
        </p>
        {changes && <NewDraft />}
        <section aria-labelledby="drafts-list" className="space-y-2">
          <h3 id="drafts-list" className="text-xs font-semibold text-muted">
            The project’s drafts
          </h3>
          {drafts.isPending && <Loading />}
          {drafts.isError && <Notice tone="bad">Could not load the drafts: {drafts.error.message}</Notice>}
          {drafts.data && listed.length === 0 && (
            <EmptyState icon={<PenLine />} title="No drafts yet">
              {changes ? 'Ask the model for one above.' : 'The members who may change the project ask the model for them.'}
            </EmptyState>
          )}
          {listed.length > 0 && (
            <ul aria-label="Drafts" className="space-y-1.5">
              {listed.map((draft) => (
                <li key={draft.id}>
                  <button
                    type="button"
                    onClick={() => workspace.showDraft(draft.id)}
                    className="w-full rounded-md border border-line bg-surface px-3 py-2 text-left transition hover:bg-sunken"
                  >
                    <span className="block text-[13px] font-medium break-words">{draft.title}</span>
                    <span className="block text-xs text-muted">{draftByline(draft)}</span>
                    <span className="flex items-center gap-1.5 text-xs text-ink">
                      {writing(draft) && <Spinner className="size-3" />}
                      {draftStatusWords(draft)}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
          {drafts.hasNextPage && (
            <Button disabled={drafts.isFetchingNextPage} onClick={() => void drafts.fetchNextPage()}>
              {drafts.isFetchingNextPage ? 'Loading…' : 'Show more drafts'}
            </Button>
          )}
        </section>
      </div>
    </div>
  )
}

/** The form that asks the model for a draft: its kind, what it should say, and which of the open documents it is about. */
function NewDraft() {
  const workspace = useWorkspace()
  const queryClient = useQueryClient()
  const id = useId()
  const [kind, setKind] = useState<DraftKind>('summary_memo')
  const [instructions, setInstructions] = useState('')
  // The documents open beside the library, the one shown chosen to begin with.
  const open = workspace.openTabs.map((tab) => workspace.openDocuments.get(tab.id)).filter((document): document is Document => document != null)
  const [chosen, setChosen] = useState(() => new Set(workspace.activeDocumentId != null ? [workspace.activeDocumentId] : []))
  const [refused, setRefused] = useState<Error | null>(null)
  // Whether the kind is written to the project's own template, which the hint says.
  const templates = useQuery({ queryKey: draftTemplatesKey(workspace.project.id), queryFn: () => api.draftTemplates(workspace.project.id) })
  const own = writtenToWords(templates.data?.templates.find((template) => template.kind === kind))
  const input = { kind, instructions, document_ids: open.filter((document) => chosen.has(document.id)).map((document) => document.id) }
  const problem = draftProblem(input)
  const ask = useMutation({
    mutationFn: () => api.askDraft(workspace.project.id, input),
    onMutate: () => setRefused(null),
    onSuccess: (draft) => {
      void queryClient.invalidateQueries({ queryKey: draftsKey(workspace.project.id) })
      queryClient.setQueryData(draftKey(draft.id), draft)
      workspace.showDraft(draft.id)
    },
    onError: (error) => setRefused(error),
  })
  const choose = (documentId: number, on: boolean) =>
    setChosen((was) => {
      const next = new Set(was)
      if (on) next.add(documentId)
      else next.delete(documentId)
      return next
    })

  return (
    <section aria-labelledby={`${id}-new`} className="space-y-2.5 rounded-md border border-line bg-surface px-3 py-2.5">
      <h3 id={`${id}-new`} className="flex items-center gap-1.5 text-[13px] font-semibold">
        <PenLine aria-hidden className="size-4 text-muted" />
        New draft
      </h3>
      <SelectField
        label="Kind"
        value={kind}
        hint={[DRAFT_KINDS.find((option) => option.value === kind)?.hint, own].filter(Boolean).join(' ')}
        onChange={(event) => setKind(event.target.value as DraftKind)}
      >
        {DRAFT_KINDS.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </SelectField>
      <div>
        <label htmlFor={`${id}-instructions`} className="mb-1 block text-xs font-medium text-muted">
          What it should say
        </label>
        <textarea
          id={`${id}-instructions`}
          className={`${FIELD} min-h-18 resize-y placeholder:text-muted`}
          rows={3}
          maxLength={4000}
          placeholder="Ask for a credit note of the difference within 14 days."
          value={instructions}
          onChange={(event) => setInstructions(event.target.value)}
        />
      </div>
      <fieldset>
        <legend className="mb-1 text-xs font-medium text-muted">About</legend>
        {open.length === 0 ? (
          <p className="text-xs text-muted">
            Open the documents it is about beside the library to choose them here, or say what it is about and the model finds
            them.
          </p>
        ) : (
          // Apart enough for each box to be a target of its own (WCAG 2.5.8).
          <div className="space-y-2">
            {open.map((document) => (
              <Checkbox
                key={document.id}
                label={document.title || document.filename}
                checked={chosen.has(document.id)}
                onChange={(event) => choose(document.id, event.target.checked)}
              />
            ))}
          </div>
        )}
      </fieldset>
      <p className="text-xs text-muted">{DRAFT_NOTE}</p>
      {refused && <Refused error={refused} onDismiss={() => setRefused(null)} />}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <Button variant="primary" icon={<PenLine className="size-4" />} disabled={problem != null || ask.isPending} onClick={() => ask.mutate()}>
          {ask.isPending ? 'Asking…' : 'Write the draft'}
        </Button>
        {problem && <span className="text-xs text-muted">{problem}</span>}
      </div>
    </section>
  )
}

/**
 * A draft in the library's place: what it was asked about, how far the model's writing is, and a version of it, the
 * newest unless an older one is chosen, its citations opening where each figure is written; edited into a new version,
 * or written again by the model as one.
 */
export function DraftView({ draftId }: { draftId: number }) {
  const workspace = useWorkspace()
  const me = useMe()
  const changes = mayChange(me.role)
  const queryClient = useQueryClient()
  const currency = useStatus().data?.currency
  const id = useId()
  const heading = useRef<HTMLHeadingElement>(null)
  const versionHeading = useRef<HTMLHeadingElement>(null)
  const [deleting, setDeleting] = useState(false)
  const [refused, setRefused] = useState<Error | null>(null)
  // The version shown, null for the newest; and while the member edits, the newest version they edit after.
  const [chosen, setChosen] = useState<number | null>(null)
  const [editing, setEditing] = useState<{ base: number; from: DraftVersion } | null>(null)
  // What the model is asked now, while the form that writes it again is open.
  const [again, setAgain] = useState<string | null>(null)
  useEffect(() => {
    if (window.document.activeElement === window.document.body) heading.current?.focus()
  }, [])
  const draft = useQuery({
    queryKey: draftKey(draftId),
    queryFn: () => api.draft(draftId),
    refetchInterval: (query) => (query.state.data && writing(query.state.data) ? DRAFT_POLL : false),
  })
  const data = draft.data
  const settled = data != null && !writing(data)
  // Once the writing settles, the list of drafts says so too.
  useEffect(() => {
    if (settled && data) void queryClient.invalidateQueries({ queryKey: draftsKey(data.project_id) })
  }, [settled, data, queryClient])
  const newest = data?.latest?.number ?? 0
  const older = chosen != null && chosen !== newest ? chosen : null
  const olderVersion = useQuery({
    queryKey: draftVersionKey(draftId, older ?? 0),
    queryFn: () => api.draftVersion(draftId, older!),
    enabled: older != null,
    // A version is never changed.
    staleTime: Infinity,
  })
  const version = older != null ? olderVersion.data : data?.latest
  // The keyboard goes on from the version's heading once what held it goes.
  const toVersion = () => requestAnimationFrame(() => versionHeading.current?.focus())

  const save = useMutation({
    mutationFn: (text: string) => api.saveDraft(draftId, { text, base: editing?.base ?? newest }),
    onMutate: () => setRefused(null),
    onSuccess: (kept) => {
      queryClient.setQueryData(draftKey(draftId), kept)
      void queryClient.invalidateQueries({ queryKey: draftsKey(kept.project_id) })
      setEditing(null)
      setChosen(null)
      toVersion()
    },
    onError: async (error) => {
      setRefused(error)
      // Saved meanwhile, or being written: the text stays in the editor, and saving again keeps it after the newest.
      if (error instanceof ApiError && error.status === 409) {
        const fresh = await queryClient.fetchQuery({ queryKey: draftKey(draftId), queryFn: () => api.draft(draftId) })
        setEditing((was) => was && { ...was, base: fresh.latest?.number ?? 0 })
      }
    },
  })
  const rewrite = useMutation({
    mutationFn: (instructions: string) => api.rewriteDraft(draftId, { instructions }),
    onMutate: () => setRefused(null),
    onSuccess: (asked) => {
      queryClient.setQueryData(draftKey(draftId), asked)
      void queryClient.invalidateQueries({ queryKey: draftsKey(asked.project_id) })
      setAgain(null)
      setChosen(null)
      toVersion()
    },
    onError: (error) => setRefused(error),
  })
  const remove = useMutation({
    mutationFn: () => api.deleteDraft(draftId),
    onSuccess: () => {
      if (data) void queryClient.invalidateQueries({ queryKey: draftsKey(data.project_id) })
      workspace.showDrafts()
    },
    onError: (error) => setRefused(error),
  })
  const open = (source: Source) => workspace.openDocument(source.document_id, { passage: source.id })
  const idle = data != null && !writing(data) && editing == null && again == null

  return (
    <div className="flex h-full min-h-0 flex-col bg-page">
      <div className="flex h-11 shrink-0 items-center gap-1.5 border-b border-line bg-surface px-2">
        <IconButton label="Back to the drafts" onClick={workspace.showDrafts}>
          <ArrowLeft className="size-4" />
        </IconButton>
        <h2 ref={heading} tabIndex={-1} className="flex-1 truncate text-[13px] font-semibold">
          {data?.title ?? 'Draft'}
        </h2>
        {changes && data && (
          <IconButton label="Delete this draft" danger disabled={remove.isPending} onClick={() => setDeleting(true)}>
            <Trash2 className="size-4" />
          </IconButton>
        )}
      </div>
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3">
        {draft.isPending && <Loading />}
        {draft.isError && <Notice tone="bad">Could not load the draft: {draft.error.message}</Notice>}
        {data && (
          <>
            <div className="space-y-0.5 text-xs text-muted">
              <p>{draftByline(data)}</p>
              {data.documents.length > 0 && (
                <p>
                  About{' '}
                  {data.documents.map((named, index) => (
                    <span key={named.id}>
                      {index > 0 && ', '}
                      {named.gone ? (
                        <span>{named.title} (deleted since)</span>
                      ) : (
                        <button type="button" onClick={() => workspace.openDocument(named.id)} className="rounded text-ink underline-offset-2 hover:underline">
                          {named.title}
                        </button>
                      )}
                    </span>
                  ))}
                </p>
              )}
              {data.instructions && <p className="break-words">Asked: {data.instructions}</p>}
              {data.run_id != null && (
                <button type="button" onClick={() => workspace.showRun(data.run_id!)} className="inline-flex items-center gap-1 rounded text-ink underline-offset-2 hover:underline">
                  <ListChecks aria-hidden className="size-3.5" />
                  The run of the cross-checks it was written with
                </button>
              )}
              <p className="flex items-center gap-1.5 text-ink">
                {writing(data) && <Spinner className="size-3" />}
                {draftStatusWords(data)}
              </p>
            </div>
            {data.status === 'failed' && data.error && <Notice tone="bad">{data.error}</Notice>}
            {refused && <Refused error={refused} onDismiss={() => setRefused(null)} />}
            <section aria-labelledby={`${id}-version`} className="space-y-2.5 rounded-md border border-line bg-surface px-3 py-2.5">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h3 id={`${id}-version`} ref={versionHeading} tabIndex={-1} className="text-[13px] font-semibold">
                  {editing ? `Editing version ${editing.from.number}` : version ? `Version ${version.number}` : 'The draft'}
                </h3>
                {data.history.length > 1 && editing == null && (
                  <span className="flex items-center gap-1.5 text-xs">
                    <label htmlFor={`${id}-versions`} className="text-muted">
                      Version
                    </label>
                    <select
                      id={`${id}-versions`}
                      className="field h-6.5 py-0 pr-7 pl-2 text-xs"
                      value={chosen ?? newest}
                      onChange={(event) => setChosen(Number(event.target.value))}
                    >
                      {data.history.map((summary) => (
                        <option key={summary.number} value={summary.number}>
                          {versionWords(summary)}
                        </option>
                      ))}
                    </select>
                  </span>
                )}
              </div>
              {editing ? (
                <Editor
                  from={editing.from}
                  saving={save.isPending}
                  onSave={(text) => save.mutate(text)}
                  onCancel={() => {
                    setEditing(null)
                    setRefused(null)
                    toVersion()
                  }}
                  onOpen={open}
                />
              ) : version ? (
                <>
                  <p className="text-xs text-muted">{versionWords(version)}</p>
                  <CitedText text={version.text} sources={version.sources} onOpen={open} lines />
                  {version.number === newest && data.notice && <p className="text-xs text-muted">{data.notice}</p>}
                </>
              ) : older != null && olderVersion.isPending ? (
                <Loading />
              ) : (
                <p className="text-xs text-muted">{writing(data) ? 'The model is writing it.' : 'There is no version of it yet.'}</p>
              )}
              {((changes && idle) || (version && editing == null)) && (
                <div className="flex flex-wrap gap-2">
                  {changes && idle && version && (
                    <Button icon={<Pencil className="size-4" />} onClick={() => setEditing({ base: newest, from: version })}>
                      {version.number === newest ? 'Edit' : 'Edit from this version'}
                    </Button>
                  )}
                  {changes && idle && (
                    <Button icon={<RotateCcw className="size-4" />} onClick={() => setAgain(data.instructions)}>
                      {newest ? 'Write it again' : 'Try again'}
                    </Button>
                  )}
                  {version && editing == null && (
                    <DownloadText urlOf={(format) => draftFileUrl(draftId, version.number, format)} />
                  )}
                </div>
              )}
              {again != null && (
                <WriteAgain
                  instructions={again}
                  onChange={setAgain}
                  asking={rewrite.isPending}
                  onAsk={() => rewrite.mutate(again)}
                  onCancel={() => {
                    setAgain(null)
                    toVersion()
                  }}
                />
              )}
              {data.usage.draft?.calls ? (
                <p className="text-[11px] text-muted">
                  Written by the model from the project’s documents: check each figure at its source before sending it.{' '}
                  {describeAnswerUsage(data.usage.draft, currency)}
                </p>
              ) : null}
            </section>
          </>
        )}
      </div>
      {deleting && data && <Confirm {...draftDeletionQuestion(data)} onConfirm={() => remove.mutate()} onClose={() => setDeleting(false)} />}
    </div>
  )
}

/** The member's text of a draft, in Markdown, with its preview: the citations the version made open as they do there. */
function Editor({
  from,
  saving,
  onSave,
  onCancel,
  onOpen,
}: {
  from: DraftVersion
  saving: boolean
  onSave: (text: string) => void
  onCancel: () => void
  onOpen: (source: Source) => void
}) {
  const id = useId()
  const field = useRef<HTMLTextAreaElement>(null)
  const [text, setText] = useState(from.text)
  const [shown, setShown] = useState<'write' | 'preview'>('write')
  useEffect(() => {
    field.current?.focus()
  }, [])
  return (
    <div className="space-y-2">
      <Segmented
        value={shown}
        onChange={setShown}
        options={[
          { value: 'write', label: 'Write' },
          { value: 'preview', label: 'Preview' },
        ]}
      />
      {shown === 'write' ? (
        <div>
          <label htmlFor={id} className="mb-1 block text-xs font-medium text-muted">
            Text of the draft
          </label>
          <textarea
            id={id}
            ref={field}
            className={`${FIELD} min-h-72 resize-y font-mono text-[13px] leading-relaxed`}
            maxLength={100_000}
            value={text}
            onChange={(event) => setText(event.target.value)}
          />
          <p className="mt-1 text-xs text-muted">Markdown. A reference such as [P12] cites a passage this version cites.</p>
        </div>
      ) : (
        <CitedText text={text} sources={from.sources} onOpen={onOpen} lines />
      )}
      <div className="flex flex-wrap gap-2">
        <Button variant="primary" disabled={!text.trim() || saving} onClick={() => onSave(text)}>
          {saving ? 'Saving…' : 'Save as a new version'}
        </Button>
        <Button onClick={onCancel}>Cancel</Button>
      </div>
    </div>
  )
}

/** What the model is asked when it writes the draft again, as a new version. */
function WriteAgain({
  instructions,
  onChange,
  asking,
  onAsk,
  onCancel,
}: {
  instructions: string
  onChange: (instructions: string) => void
  asking: boolean
  onAsk: () => void
  onCancel: () => void
}) {
  const id = useId()
  const field = useRef<HTMLTextAreaElement>(null)
  useEffect(() => {
    field.current?.focus()
  }, [])
  return (
    <div className="space-y-2 border-t border-line pt-2.5">
      <div>
        <label htmlFor={id} className="mb-1 block text-xs font-medium text-muted">
          What it should say now
        </label>
        <textarea
          id={id}
          ref={field}
          className={`${FIELD} min-h-18 resize-y`}
          rows={3}
          maxLength={4000}
          value={instructions}
          onChange={(event) => onChange(event.target.value)}
        />
      </div>
      <p className="text-xs text-muted">{DRAFT_NOTE}</p>
      <div className="flex flex-wrap gap-2">
        <Button variant="primary" icon={<RotateCcw className="size-4" />} disabled={asking} onClick={onAsk}>
          {asking ? 'Asking…' : 'Write it again'}
        </Button>
        <Button onClick={onCancel}>Cancel</Button>
      </div>
    </div>
  )
}

/** Open the draft in the library's place, whose view takes the focus to its heading from the button that opened it. */
function useOpenDraft() {
  const workspace = useWorkspace()
  return (draftId: number) => {
    // The button stays where it is while the draft opens in the library's place: let go of the focus, which the
    // draft's view then takes to its heading, so the keyboard goes on from there.
    if (window.document.activeElement instanceof HTMLElement) window.document.activeElement.blur()
    workspace.showDraft(draftId)
  }
}

/** A disputed document's way to the letter that disputes it: a draft asked of the model about it, opened as it is written. */
export function WriteDisputeLetter({ document }: { document: Document }) {
  const workspace = useWorkspace()
  const queryClient = useQueryClient()
  const open = useOpenDraft()
  const ask = useMutation({
    mutationFn: () => api.askDraft(workspace.project.id, disputeLetter(document)),
    onSuccess: (draft) => {
      queryClient.setQueryData(draftKey(draft.id), draft)
      void queryClient.invalidateQueries({ queryKey: draftsKey(workspace.project.id) })
      open(draft.id)
    },
  })
  return (
    <>
      <Button icon={<PenLine className="size-3.5" />} disabled={ask.isPending} onClick={() => ask.mutate()} className="h-6.5 px-2 text-xs">
        {ask.isPending ? 'Asking…' : 'Write a dispute letter'}
      </Button>
      {ask.isError && (
        <span role="alert" className="text-bad">
          {ask.error.message}
        </span>
      )}
    </>
  )
}

/**
 * The draft an answer offers, under it: what it is about and should say, written into the project's drafts at its
 * member's press and opened there as it is written; once written, the way to it. A shared chat's reader is shown it,
 * and the draft once there, but presses nothing, the chat being its starter's.
 */
export function OfferedDraft({ chatId, messageId, offer, own }: { chatId: number; messageId: number; offer: DraftOffer; own: boolean }) {
  const workspace = useWorkspace()
  const queryClient = useQueryClient()
  const open = useOpenDraft()
  const write = useMutation({
    mutationFn: () => writeOffered(queryClient, chatId, messageId),
    onSuccess: (draft) => {
      queryClient.setQueryData(draftKey(draft.id), draft)
      void queryClient.invalidateQueries({ queryKey: draftsKey(workspace.project.id) })
      open(draft.id)
    },
  })
  const name = offerWords(offer)
  return (
    <section aria-label={`Draft offered: ${name}`} className="space-y-2 rounded-lg border border-line bg-surface px-3.5 py-3 text-[13px] shadow-xs">
      <p className="flex items-center gap-2 font-medium">
        <PenLine className="size-3.5 shrink-0 text-accent" />
        {name}
      </p>
      {offer.instructions && <p className="whitespace-pre-line text-muted">{offer.instructions}</p>}
      <div className="flex flex-wrap items-center gap-2">
        {offer.draft_id != null ? (
          <Button icon={<FileText className="size-3.5" />} onClick={() => open(offer.draft_id!)}>
            Open the draft
          </Button>
        ) : offer.written ? (
          <p className="text-muted">The draft written from it was deleted.</p>
        ) : (
          own && (
            <>
              <Button variant="primary" icon={<PenLine className="size-3.5" />} disabled={write.isPending} onClick={() => write.mutate()}>
                {write.isPending ? 'Asking…' : 'Write the draft'}
              </Button>
              <span className="text-xs text-muted">Into the project’s drafts, for everyone in it to edit.</span>
            </>
          )
        )}
        {write.isError && (
          <span role="alert" className="text-bad">
            {write.error.message}
          </span>
        )}
      </div>
    </section>
  )
}
