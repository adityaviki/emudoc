import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, MessageSquarePlus, RotateCcw, X } from 'lucide-react'
import { useEffect, useId, useRef, useState } from 'react'
import { api } from '../api.ts'
import type { Comment, Document, Mention, Mentionable, Thread, ThreadInput } from '../api.ts'
import { useMe } from '../auth.ts'
import {
  COMMENT_LENGTH,
  activityKey,
  deleteQuestion,
  matching,
  mentionAt,
  mentionWords,
  mentionableKey,
  openWords,
  pieces,
  quoteWhere,
  stored,
  threadCounts,
  threadsKey,
  typed,
  withMention,
} from '../comments.ts'
import type { ThreadAbout } from '../comments.ts'
import { Confirm } from '../components/Confirm.tsx'
import { Button, IconButton, Loading, Notice } from '../components/ui.tsx'
import { formatDateTime } from '../format.ts'
import { useWorkspace } from './state.ts'

interface CommentsPanelProps {
  document: Document
  /** The new thread's form, open while set, and what it is about. */
  about: ThreadAbout | null
  onAbout: (about: ThreadAbout | null) => void
  /** What a new thread asked for now would be about: the words chosen in the document, or the passage shown. */
  aboutNow: () => ThreadAbout
  /** The thread the document was opened at, shown and marked. */
  marked: number | null
  onClose: () => void
}

/**
 * The comments beside a document: its open threads, oldest first, then those resolved, folded; each thread's words
 * quoted, which open the passage while the reading holds it; and a new thread's form. Everyone who reads the
 * project writes here, viewers too.
 */
export function CommentsPanel({ document, about, onAbout, aboutNow, marked, onClose }: CommentsPanelProps) {
  const threads = useQuery({ queryKey: threadsKey(document.id), queryFn: () => api.threads(document.id) })
  const [showResolved, setShowResolved] = useState(false)
  const heading = useRef<HTMLHeadingElement>(null)
  const all = threads.data ?? []
  const counts = threadCounts(all)
  const open = all.filter((thread) => thread.resolved_at == null)
  const resolved = all.filter((thread) => thread.resolved_at != null)
  const markedResolved = resolved.some((thread) => thread.id === marked)
  // Where the keyboard goes on from once a new thread's form is gone: the thread it made, once drawn, or the heading.
  const [focusing, setFocusing] = useState<number | 'heading' | null>(null)
  useEffect(() => {
    if (focusing == null) return
    const target = focusing === 'heading' ? heading.current : window.document.getElementById(`thread-${focusing}`)
    if (target) {
      target.focus()
      setFocusing(null)
    }
  }, [focusing, threads.data])

  // The thread a link led to is shown, resolved or not, once the threads are here.
  useEffect(() => {
    if (marked == null || !threads.isSuccess) return
    const target = window.document.getElementById(`thread-${marked}`)
    const behavior = window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'
    target?.scrollIntoView({ block: 'nearest', behavior })
  }, [marked, threads.isSuccess, showResolved])

  return (
    <aside
      aria-labelledby={`comments-${document.id}`}
      className="absolute inset-y-0 right-0 z-10 flex w-80 max-w-full shrink-0 flex-col border-l border-line bg-surface shadow-lg shadow-ink/8 @4xl:static @4xl:shadow-none print:hidden"
    >
      <div className="flex h-11 shrink-0 items-center gap-1.5 border-b border-line px-3">
        <h3 ref={heading} id={`comments-${document.id}`} tabIndex={-1} className="flex-1 truncate text-[13px] font-semibold">
          Comments
        </h3>
        {threads.isSuccess && <span className="text-xs text-muted">{openWords(counts.open)}</span>}
        <IconButton
          label="New comment"
          // Read at the mouse down, which is kept from moving the focus, so that the words chosen in the document stay
          // chosen and the new thread's box keeps the focus it takes; a key's press has no mouse down before its click.
          onMouseDown={(event) => {
            event.preventDefault()
            onAbout(aboutNow())
          }}
          onClick={(event) => {
            if (event.detail === 0) onAbout(aboutNow())
          }}
        >
          <MessageSquarePlus className="size-4" />
        </IconButton>
        <IconButton label="Close comments" onClick={onClose}>
          <X className="size-4" />
        </IconButton>
      </div>
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3">
        {about && (
          <NewThread
            document={document}
            about={about}
            onAbout={onAbout}
            onDone={(thread) => {
              onAbout(null)
              setFocusing(thread ? thread.id : 'heading')
            }}
          />
        )}
        {threads.isPending && <Loading />}
        {threads.isError && <Notice tone="bad">Could not load the comments: {threads.error.message}</Notice>}
        {threads.isSuccess && all.length === 0 && !about && (
          <p className="text-xs leading-relaxed text-muted">
            No comments yet. Choose words in the document and press “New comment” to start a thread about them, or
            press it with nothing chosen to comment on the whole document.
          </p>
        )}
        {open.map((thread) => (
          <ThreadItem key={thread.id} document={document} thread={thread} marked={thread.id === marked} onGone={() => heading.current?.focus()} />
        ))}
        {resolved.length > 0 && (
          <div className="space-y-3">
            <Button aria-expanded={showResolved || markedResolved} onClick={() => setShowResolved((shown) => !shown)}>
              {showResolved || markedResolved ? 'Hide' : 'Show'} {resolved.length} resolved
            </Button>
            {(showResolved || markedResolved) &&
              resolved.map((thread) => (
                <ThreadItem
                  key={thread.id}
                  document={document}
                  thread={thread}
                  marked={thread.id === marked}
                  onGone={() => heading.current?.focus()}
                />
              ))}
          </div>
        )}
      </div>
    </aside>
  )
}

/** Keeps a thread the server answered with in the document's list, and the project's activity stale. */
function useKept(document: Document) {
  const queryClient = useQueryClient()
  return (thread: Thread) => {
    queryClient.setQueryData<Thread[]>(threadsKey(document.id), (threads = []) =>
      threads.some((kept) => kept.id === thread.id) ? threads.map((kept) => (kept.id === thread.id ? thread : kept)) : [...threads, thread],
    )
    void queryClient.invalidateQueries({ queryKey: activityKey(document.project_id) })
  }
}

function NewThread({
  document,
  about,
  onAbout,
  onDone,
}: {
  document: Document
  about: ThreadAbout
  onAbout: (about: ThreadAbout | null) => void
  onDone: (thread: Thread | null) => void
}) {
  const kept = useKept(document)
  const start = useMutation({
    mutationFn: (text: string) => {
      const body: ThreadInput = { text }
      if (about.passage_id != null) body.passage_id = about.passage_id
      if (about.block != null) body.block = about.block
      if (about.quote) body.quote = about.quote
      return api.startThread(document.id, body)
    },
    onSuccess: (thread) => {
      kept(thread)
      onDone(thread)
    },
  })
  const anchored = about.passage_id != null || about.block != null || about.quote
  return (
    <div className="rounded-lg border border-accent/40 bg-page p-2.5">
      {anchored ? (
        <div className="mb-2 flex items-start gap-1">
          <blockquote className="line-clamp-3 flex-1 border-l-2 border-accent/60 pl-2 text-xs text-muted">
            {about.shown ?? 'The passage shown'}
          </blockquote>
          <IconButton label="Comment on the whole document instead" onClick={() => onAbout({})} className="size-6!">
            <X className="size-3.5" />
          </IconButton>
        </div>
      ) : (
        <p className="mb-2 text-xs text-muted">About the whole document</p>
      )}
      <CommentBox
        projectId={document.project_id}
        label="New comment"
        action="Comment"
        pending={start.isPending}
        error={start.isError ? start.error.message : null}
        onSubmit={(text) => start.mutate(text)}
        onCancel={() => onDone(null)}
        autoFocus
      />
    </div>
  )
}

function ThreadItem({ document, thread, marked, onGone }: { document: Document; thread: Thread; marked: boolean; onGone: () => void }) {
  const workspace = useWorkspace()
  const kept = useKept(document)
  const [replying, setReplying] = useState(false)
  // The thread takes the keyboard from a box in it that is going: the thread stays as the box goes.
  const article = useRef<HTMLElement>(null)
  const takeBack = () => article.current?.focus()
  const resolved = thread.resolved_at != null
  const where = quoteWhere(thread, document.version)
  const reply = useMutation({
    mutationFn: (text: string) => api.reply(thread.id, text),
    onSuccess: (answer) => {
      kept(answer)
      takeBack()
      setReplying(false)
    },
  })
  // Resolved or opened again, the thread moves between the lists: the keyboard goes on from the heading.
  const resolve = useMutation({
    mutationFn: () => api.resolveThread(thread.id, !resolved),
    onSuccess: (answer) => {
      kept(answer)
      onGone()
    },
  })
  const first = thread.comments[0]
  const label = thread.quote ? `Thread on “${thread.quote.slice(0, 60)}”` : `Thread on the document${first?.author ? `, started by ${first.author}` : ''}`
  return (
    <article
      ref={article}
      id={`thread-${thread.id}`}
      tabIndex={-1}
      aria-label={label}
      className={`rounded-lg border bg-page p-2.5 focus-visible:-outline-offset-2! ${marked ? 'border-accent ring-2 ring-accent/30' : 'border-line'}`}
    >
      {thread.quote && (
        <div className="mb-2">
          {thread.passage_id != null ? (
            <button
              type="button"
              onClick={() => workspace.openDocument(document.id, { passage: thread.passage_id! }, { replace: true })}
              title="Show the passage in the document"
              className="block w-full rounded border-l-2 border-accent/60 pl-2 text-left text-xs text-muted hover:text-ink"
            >
              <span className="line-clamp-3">{thread.quote}</span>
            </button>
          ) : (
            <blockquote className="line-clamp-3 border-l-2 border-line pl-2 text-xs text-muted">{thread.quote}</blockquote>
          )}
          {where && <p className="mt-0.5 pl-2.5 text-[11px] text-muted">{where}</p>}
        </div>
      )}
      <ol className="space-y-2.5">
        {thread.comments.map((comment, index) => (
          <CommentItem
            key={comment.id}
            document={document}
            comment={comment}
            last={thread.comments.length === 1}
            first={index === 0}
            onBack={takeBack}
            onGone={onGone}
          />
        ))}
      </ol>
      {resolved && (
        <p className="mt-2 flex items-center gap-1 text-[11px] text-muted">
          <CheckCircle2 className="size-3.5 text-good" aria-hidden />
          Resolved{thread.resolver ? ` by ${thread.resolver}` : ''} on {formatDateTime(thread.resolved_at!)}
        </p>
      )}
      {replying ? (
        <div className="mt-2">
          <CommentBox
            projectId={document.project_id}
            label="Reply"
            action="Reply"
            pending={reply.isPending}
            error={reply.isError ? reply.error.message : null}
            onSubmit={(text) => reply.mutate(text)}
            onCancel={() => {
              takeBack()
              setReplying(false)
            }}
            autoFocus
          />
        </div>
      ) : (
        <div className="mt-2 flex flex-wrap gap-1.5">
          <Button className="h-6.5 px-2 text-xs" onClick={() => setReplying(true)}>
            Reply
          </Button>
          <Button
            className="h-6.5 px-2 text-xs"
            icon={resolved ? <RotateCcw className="size-3.5" /> : <CheckCircle2 className="size-3.5" />}
            disabled={resolve.isPending}
            onClick={() => resolve.mutate()}
          >
            {resolved ? 'Open again' : 'Resolve'}
          </Button>
        </div>
      )}
      {resolve.isError && (
        <p role="alert" className="mt-1 text-xs text-bad">
          {resolve.error.message}
        </p>
      )}
    </article>
  )
}

function CommentItem({
  document,
  comment,
  first,
  last,
  onBack,
  onGone,
}: {
  document: Document
  comment: Comment
  first: boolean
  last: boolean
  /** Gives the thread the keyboard back once the comment's box is gone. */
  onBack: () => void
  onGone: () => void
}) {
  const me = useMe()
  const queryClient = useQueryClient()
  const kept = useKept(document)
  const [editing, setEditing] = useState(false)
  const [asking, setAsking] = useState(false)
  const own = comment.author_id === me.id
  const manages = me.role === 'owner' || me.role === 'admin'
  const change = useMutation({
    mutationFn: (text: string) => api.changeComment(comment.id, text),
    onSuccess: (thread) => {
      kept(thread)
      onBack()
      setEditing(false)
    },
  })
  const remove = useMutation({
    mutationFn: () => api.deleteComment(comment.id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: threadsKey(document.id) })
      void queryClient.invalidateQueries({ queryKey: activityKey(document.project_id) })
      onGone()
    },
  })
  const shown = typed(comment.text, comment.mentions)
  return (
    <li className={first ? '' : 'border-t border-line pt-2.5'}>
      <p className="flex flex-wrap items-baseline gap-x-1.5 text-xs">
        <span className="font-semibold">{comment.author ?? 'A former member'}</span>
        <time dateTime={comment.created_at} className="text-muted">
          {formatDateTime(comment.created_at)}
        </time>
        {comment.edited_at && <span className="text-muted">· edited</span>}
      </p>
      {editing ? (
        <div className="mt-1.5">
          <CommentBox
            projectId={document.project_id}
            label="Change the comment"
            action="Save"
            initial={shown}
            pending={change.isPending}
            error={change.isError ? change.error.message : null}
            onSubmit={(text) => change.mutate(text)}
            onCancel={() => {
              onBack()
              setEditing(false)
            }}
            autoFocus
          />
        </div>
      ) : (
        <p className="mt-0.5 text-[13px] leading-relaxed break-words whitespace-pre-wrap">
          <CommentText text={comment.text} mentions={comment.mentions} />
        </p>
      )}
      {!editing && (own || manages) && (
        <div className="mt-1 flex gap-2 text-xs">
          {own && (
            <button type="button" className="rounded text-muted hover:text-ink hover:underline" onClick={() => setEditing(true)}>
              Edit
            </button>
          )}
          <button type="button" className="rounded text-muted hover:text-bad hover:underline" onClick={() => setAsking(true)}>
            Delete
          </button>
        </div>
      )}
      {remove.isError && (
        <p role="alert" className="mt-1 text-xs text-bad">
          {remove.error.message}
        </p>
      )}
      {asking && <Confirm {...deleteQuestion(last)} onConfirm={() => remove.mutate()} onClose={() => setAsking(false)} />}
    </li>
  )
}

/** A comment's words, each member it mentions as their name. */
export function CommentText({ text, mentions }: { text: string; mentions: Mention[] }) {
  return pieces(text, mentions).map((piece, index) =>
    'mention' in piece ? (
      <span key={index} className="font-medium text-accent">
        {mentionWords(piece.name)}
      </span>
    ) : (
      <span key={index}>{piece.text}</span>
    ),
  )
}

interface CommentBoxProps {
  projectId: number
  label: string
  action: string
  initial?: { text: string; chosen: Mentionable[] }
  pending: boolean
  error: string | null
  onSubmit: (text: string) => void
  onCancel: () => void
  autoFocus?: boolean
}

/**
 * A comment's box: after "@", the members who see the project whose names start with what follows, chosen by the
 * arrows and Enter or Tab, or a click; Escape closes the list, or leaves the box once it is closed; Ctrl+Enter sends.
 */
function CommentBox({ projectId, label, action, initial, pending, error, onSubmit, onCancel, autoFocus }: CommentBoxProps) {
  const id = useId()
  const box = useRef<HTMLTextAreaElement>(null)
  const [text, setText] = useState(initial?.text ?? '')
  const [chosen, setChosen] = useState<Mentionable[]>(initial?.chosen ?? [])
  const [caret, setCaret] = useState(initial?.text.length ?? 0)
  const [active, setActive] = useState(0)
  // The "@" whose list Escape closed, which stays closed until another is typed.
  const [dismissed, setDismissed] = useState<number | null>(null)
  // Where the caret goes once a chosen name is in the box.
  const placed = useRef<number | null>(null)
  const members = useQuery({ queryKey: mentionableKey(projectId), queryFn: () => api.mentionable(projectId), staleTime: 60_000 })
  const at = mentionAt(text, caret)
  const options = at && at.start !== dismissed ? matching(members.data ?? [], at.query) : []
  const listed = options.length > 0
  const current = Math.min(active, options.length - 1)
  const words = stored(text.trim(), chosen)
  const tooLong = words.length > COMMENT_LENGTH

  useEffect(() => {
    if (autoFocus) box.current?.focus()
  }, [autoFocus])
  useEffect(() => {
    if (placed.current == null || !box.current) return
    box.current.setSelectionRange(placed.current, placed.current)
    placed.current = null
  }, [text])

  function choose(member: Mentionable) {
    if (!at) return
    const next = withMention(text, at.start, caret, member)
    setText(next.text)
    setCaret(next.caret)
    placed.current = next.caret
    setChosen((was) => (was.some((kept) => kept.id === member.id) ? was : [...was, member]))
    setActive(0)
    box.current?.focus()
  }

  function send() {
    if (!words || tooLong || pending) return
    onSubmit(words)
  }

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault()
        send()
      }}
    >
      <label htmlFor={`${id}-text`} className="sr-only">
        {label}
      </label>
      <div className="relative">
        <textarea
          ref={box}
          id={`${id}-text`}
          rows={3}
          className="field block min-h-16 w-full px-2.5 py-1.5 text-[13px]"
          placeholder="Write a comment; @ mentions a member"
          aria-describedby={`${id}-hint`}
          aria-autocomplete="list"
          aria-controls={listed ? `${id}-members` : undefined}
          aria-activedescendant={listed ? `${id}-member-${options[current].id}` : undefined}
          value={text}
          onChange={(event) => {
            setText(event.target.value)
            setCaret(event.target.selectionStart)
            setActive(0)
            if (!mentionAt(event.target.value, event.target.selectionStart)) setDismissed(null)
          }}
          onSelect={(event) => setCaret(event.currentTarget.selectionStart)}
          onKeyDown={(event) => {
            if (listed && (event.key === 'ArrowDown' || event.key === 'ArrowUp')) {
              event.preventDefault()
              const step = event.key === 'ArrowDown' ? 1 : -1
              setActive((current + step + options.length) % options.length)
            } else if (listed && (event.key === 'Enter' || event.key === 'Tab') && !event.shiftKey) {
              event.preventDefault()
              choose(options[current])
            } else if (event.key === 'Escape') {
              // Escape is the box's, never the workspace's.
              event.stopPropagation()
              if (listed && at) setDismissed(at.start)
              else onCancel()
            } else if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
              event.preventDefault()
              send()
            }
          }}
        />
        {listed && (
          <ul
            id={`${id}-members`}
            role="listbox"
            aria-label="Members to mention"
            className="absolute inset-x-0 top-full z-10 mt-1 max-h-48 overflow-y-auto rounded-md border border-line bg-surface py-1 shadow-lg shadow-ink/8"
          >
            {options.map((member, index) => (
              <li
                key={member.id}
                id={`${id}-member-${member.id}`}
                role="option"
                aria-selected={index === current}
                // Chosen at the press, before the box loses the focus.
                onMouseDown={(event) => {
                  event.preventDefault()
                  choose(member)
                }}
                className={`cursor-pointer px-2.5 py-1 text-[13px] ${index === current ? 'bg-accent-soft text-accent' : ''}`}
              >
                {member.name}
              </li>
            ))}
          </ul>
        )}
      </div>
      <p id={`${id}-hint`} className="mt-1 text-[11px] text-muted">
        {tooLong ? `A comment holds up to ${COMMENT_LENGTH.toLocaleString()} characters.` : 'Members you mention are told, with the document’s name.'}
      </p>
      {error && (
        <p role="alert" className="mt-1 text-xs text-bad">
          {error}
        </p>
      )}
      <div className="mt-1.5 flex gap-1.5">
        <Button type="submit" variant="primary" className="h-6.5 px-2 text-xs" disabled={!words || tooLong || pending}>
          {action}
        </Button>
        <Button className="h-6.5 px-2 text-xs" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  )
}
