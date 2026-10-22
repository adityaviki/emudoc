import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ArrowUp,
  BookOpen,
  ChevronDown,
  ExternalLink,
  FileText,
  ListTree,
  Lock,
  MessagesSquare,
  Pencil,
  Plus,
  Search,
  Square,
  Table2,
  Trash2,
  Users,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import type { KeyboardEvent } from 'react'
import { api, organizations, pageLabel, sectionPath } from '../api.ts'
import type { Chat, Source } from '../api.ts'
import { useMe } from '../auth.ts'
import { ask, chatKey, isStreaming, loadChat, resume, splitChats, stop, useStreaming } from '../chat.ts'
import type { ChatData, Message, Step } from '../chat.ts'
import { ErrorBoundary } from '../components/ErrorBoundary.tsx'
import { Markdown, SourceText } from '../components/Markdown.tsx'
import { Floating } from '../components/Menu.tsx'
import { PlanLimitNotice } from '../components/PlanLimit.tsx'
import { Button, IconButton, Loading, Notice, Spinner } from '../components/ui.tsx'
import { formatDate } from '../format.ts'
import { useStatus } from '../hooks.ts'
import { inviteNudgeOf, mayInvite } from '../onboarding.ts'
import type { InviteNudge } from '../onboarding.ts'
import { trashKey } from '../trash.ts'
import { describeAnswerUsage } from '../usage.ts'
import { InviteTeam } from './InviteTeam.tsx'
import { useWorkspace } from './state.ts'

const SUGGESTIONS = [
  'What documents are in this project?',
  'Summarize each document in two sentences.',
  'List every date and deadline, with the document it comes from.',
]

const NO_MESSAGES: Message[] = []
// How many of the chats other members shared a new chat shows; the chat list holds them all.
const SHARED_SHOWN = 3

/** The agent: the project's chats, one of them open, with answers that cite the documents. */
export function ChatPane() {
  const workspace = useWorkspace()
  const project = workspace.project
  const me = useMe()
  const chats = useQuery({ queryKey: ['chats', project.id], queryFn: () => api.chats(project.id) })

  if (chats.isPending) return <Loading />
  if (chats.isError) {
    return (
      <div className="p-4">
        <Notice tone="bad">Could not load chats: {chats.error.message}</Notice>
      </div>
    )
  }
  const { own, shared } = splitChats(chats.data, me.id)
  // Without a chat in the URL the member's latest opens, never one another member shared. "new"
  // is an empty chat until its first question.
  const requested = workspace.url.chatId
  const chatId = requested === 'new' ? null : (requested ?? own[0]?.id ?? null)

  return (
    <div className="flex h-full min-h-0 flex-col bg-page">
      <ChatHeader own={own} shared={shared} activeId={chatId} />
      {/* A chat that breaks leaves the list, from which another opens. */}
      <ErrorBoundary part="chat" resetKey={chatId ?? 'new'}>
        <Conversation key={chatId ?? 'new'} chatId={chatId} shared={shared} />
      </ErrorBoundary>
    </div>
  )
}

interface ChatHeaderProps {
  // The member's own chats, and the ones other members shared with the project.
  own: Chat[]
  shared: Chat[]
  activeId: number | null
}

function ChatHeader({ own, shared, activeId }: ChatHeaderProps) {
  const workspace = useWorkspace()
  // How long the organisation keeps a chat after its last question, by its plan or its own choice
  // (backend/app/retention.py).
  const keptFor = useMe().organization.chat_retention_days
  // Where the list opens: set from the button's position when it is clicked.
  const [anchor, setAnchor] = useState<DOMRect | null>(null)
  const button = useRef<HTMLButtonElement>(null)
  const mine = own.find((chat) => chat.id === activeId)
  const active = mine ?? shared.find((chat) => chat.id === activeId)
  const title = activeId == null ? 'New chat' : active?.title || 'New chat'
  const ChatIcon = active?.visibility === 'project' ? Users : MessagesSquare
  const close = () => setAnchor(null)

  return (
    <div className="flex h-11 shrink-0 items-center gap-2 border-b border-line bg-surface px-3">
      <button
        ref={button}
        type="button"
        aria-haspopup="menu"
        aria-expanded={anchor !== null}
        onClick={(event) => setAnchor(anchor ? null : event.currentTarget.getBoundingClientRect())}
        className="flex h-7 min-w-0 max-w-full items-center gap-2 rounded-md px-2 text-left text-[13px] transition hover:bg-sunken"
      >
        <ChatIcon className="size-4 shrink-0 text-muted" />
        <span className="min-w-0 truncate font-medium">{title}</span>
        {active?.visibility === 'project' && <span className="sr-only">, shared with the project</span>}
        <ChevronDown className="size-3.5 shrink-0 text-muted" />
      </button>
      <span className="flex-1" />
      {mine && <ShareButton chat={mine} />}
      <Button
        variant="ghost"
        icon={<Plus className="size-4" />}
        disabled={activeId === null}
        onClick={() => workspace.openChat('new')}
      >
        New chat
      </Button>
      {anchor && (
        <Floating anchor={anchor} onClose={close} ignore={button} className="w-80 max-w-[90vw]">
          <div className="max-h-80 overflow-y-auto p-1">
            {shared.length > 0 && <ListHeading>Your chats</ListHeading>}
            {own.length === 0 ? (
              <p className="px-2.5 py-1.5 text-[13px] text-muted">No chats yet. Ask a question to start one.</p>
            ) : (
              <ul className="space-y-0.5">
                {own.map((chat) => (
                  <ChatRow key={chat.id} chat={chat} active={chat.id === activeId} onOpen={close} />
                ))}
              </ul>
            )}
            {shared.length > 0 && (
              <>
                <ListHeading>Shared with the project</ListHeading>
                <ul className="space-y-0.5">
                  {shared.map((chat) => (
                    <SharedChatRow key={chat.id} chat={chat} active={chat.id === activeId} onOpen={close} />
                  ))}
                </ul>
              </>
            )}
          </div>
          <div className="flex items-start justify-between gap-3 border-t border-line px-3 py-2 text-xs text-muted">
            <p>{keptFor !== null && `A chat is deleted ${keptFor} days after its last question.`}</p>
            <button
              type="button"
              className="shrink-0 font-medium hover:text-ink"
              onClick={() => {
                close()
                workspace.showTrash()
              }}
            >
              Deleted chats
            </button>
          </div>
        </Floating>
      )}
    </div>
  )
}

function ListHeading({ children }: { children: string }) {
  return <p className="px-2.5 pt-2 pb-1 text-[11px] font-medium tracking-wide text-muted uppercase">{children}</p>
}

/**
 * Share the member's own chat with the project, or take it private again. Whoever sees the project
 * reads a shared chat; only its starter asks in it.
 */
function ShareButton({ chat }: { chat: Chat }) {
  const queryClient = useQueryClient()
  const shared = chat.visibility === 'project'
  const share = useMutation({
    mutationFn: () => api.shareChat(chat.id, shared ? 'private' : 'project'),
    onSuccess: (changed) => {
      queryClient.setQueryData<Chat[]>(['chats', chat.project_id], (chats) =>
        chats?.map((item) => (item.id === chat.id ? changed : item)),
      )
      queryClient.setQueryData<ChatData>(chatKey(chat.id), (data) => data && { ...data, visibility: changed.visibility })
    },
  })

  return (
    <>
      {share.isError && (
        <span role="alert" title={share.error.message} className="min-w-0 truncate text-xs text-bad">
          {share.error.message}
        </span>
      )}
      <Button
        variant="ghost"
        icon={shared ? <Lock className="size-4" /> : <Users className="size-4" />}
        disabled={share.isPending}
        title={
          shared
            ? 'Everyone who sees the project can read this chat. Make it yours alone again.'
            : 'Let everyone who sees the project read this chat. Only you can ask in it.'
        }
        onClick={() => share.mutate()}
      >
        {shared ? 'Make private' : 'Share'}
      </Button>
    </>
  )
}

/** Who shared a chat and when its last question was asked, under its title. */
function sharedBy(chat: Chat): string {
  return `${chat.started_by} · ${formatDate(chat.updated_at)}`
}

/** A chat another member shared with the project: theirs to rename and delete, so only opened here. */
function SharedChatRow({ chat, active, onOpen }: { chat: Chat; active: boolean; onOpen: () => void }) {
  const workspace = useWorkspace()
  const streaming = useStreaming(chat.id)
  const title = chat.title || 'New chat'
  return (
    <li className={`flex items-center rounded-md ${active ? 'bg-sunken' : 'hover:bg-sunken'}`}>
      <button
        type="button"
        onClick={() => {
          workspace.openChat(chat.id)
          onOpen()
        }}
        title={title}
        className="min-w-0 flex-1 px-2.5 py-1.5 text-left"
      >
        <span className="block truncate text-[13px]">{title}</span>
        <span className="block truncate text-xs text-muted">{sharedBy(chat)}</span>
      </button>
      {streaming && <Spinner className="mr-2.5 size-3.5 shrink-0" />}
    </li>
  )
}

function ChatRow({ chat, active, onOpen }: { chat: Chat; active: boolean; onOpen: () => void }) {
  const workspace = useWorkspace()
  const project = workspace.project
  const queryClient = useQueryClient()
  const streaming = useStreaming(chat.id)
  const [renaming, setRenaming] = useState(false)
  const rename = useMutation({
    mutationFn: (title: string) => api.renameChat(chat.id, title),
    onSuccess: (renamed) => {
      queryClient.setQueryData<Chat[]>(['chats', project.id], (chats) =>
        chats?.map((item) => (item.id === chat.id ? renamed : item)),
      )
      queryClient.setQueryData<ChatData>(chatKey(chat.id), (data) => data && { ...data, title: renamed.title })
    },
  })
  const remove = useMutation({
    mutationFn: () => {
      stop(chat.id)
      return api.deleteChat(chat.id)
    },
    onSuccess: () => {
      queryClient.setQueryData<Chat[]>(['chats', project.id], (chats) => chats?.filter((item) => item.id !== chat.id))
      queryClient.removeQueries({ queryKey: chatKey(chat.id) })
      // Into the trash, which the chat list leads to under "Deleted chats".
      void queryClient.invalidateQueries({ queryKey: trashKey(project.id) })
      if (active) workspace.openChat(null, { replace: true })
    },
  })

  function commitTitle(value: string) {
    const title = value.trim()
    if (title && title !== chat.title) rename.mutate(title)
    setRenaming(false)
  }

  const title = chat.title || 'New chat'
  return (
    <li className={`group flex items-center rounded-md ${active ? 'bg-sunken' : 'hover:bg-sunken'}`}>
      {renaming ? (
        <input
          autoFocus
          defaultValue={chat.title}
          maxLength={200}
          aria-label="Chat title"
          className="field min-w-0 flex-1 px-2 py-1 text-[13px]"
          onBlur={(event) => commitTitle(event.target.value)}
          onKeyDown={(event) => {
            event.stopPropagation()
            if (event.key === 'Enter') commitTitle(event.currentTarget.value)
            if (event.key === 'Escape') setRenaming(false)
          }}
        />
      ) : (
        <button
          type="button"
          onClick={() => {
            workspace.openChat(chat.id)
            onOpen()
          }}
          title={title}
          className="flex min-w-0 flex-1 items-center gap-1.5 px-2.5 py-1.5 text-left text-[13px]"
        >
          {chat.visibility === 'project' && <Users className="size-3.5 shrink-0 text-muted" />}
          <span className="min-w-0 truncate">{title}</span>
          {chat.visibility === 'project' && <span className="sr-only">, shared with the project</span>}
        </button>
      )}
      {streaming ? (
        <Spinner className="mr-2.5 size-3.5 shrink-0" />
      ) : (
        !renaming && (
          <span className={`flex shrink-0 pr-0.5 ${active ? '' : 'opacity-0 group-hover:opacity-100 focus-within:opacity-100'}`}>
            <IconButton label="Rename chat" onClick={() => setRenaming(true)}>
              <Pencil className="size-3.5" />
            </IconButton>
            <IconButton label="Delete chat" danger disabled={remove.isPending} onClick={() => remove.mutate()}>
              <Trash2 className="size-3.5" />
            </IconButton>
          </span>
        )
      )}
    </li>
  )
}

function Conversation({ chatId, shared }: { chatId: number | null; shared: Chat[] }) {
  const workspace = useWorkspace()
  const project = workspace.project
  const me = useMe()
  const queryClient = useQueryClient()
  const health = useStatus()
  const chat = useQuery({
    queryKey: chatKey(chatId),
    queryFn: () => api.chat(chatId!).then(loadChat),
    enabled: chatId != null,
    // Answers stream straight into this data; the saved copy is only read when a chat is opened.
    staleTime: Infinity,
  })
  // An answer still being written when the chat is opened, after a reload or from another
  // tab, is followed from where the server has it.
  useEffect(() => {
    const last = chat.data?.messages[chat.data.messages.length - 1]
    if (chatId != null && isStreaming(chat.data) && last?.id != null) resume(queryClient, chatId, last.id)
  }, [chat.data, chatId, queryClient])
  const streaming = useStreaming(chatId) || isStreaming(chat.data)
  const [draft, setDraft] = useState('')
  const bottom = useRef<HTMLDivElement>(null)
  // A chat is created with its first question, and the URL then moves to it.
  const create = useMutation({
    mutationFn: (question: string) => api.createChat(project.id).then((created) => ({ created, question })),
    onSuccess: ({ created, question }) => {
      queryClient.setQueryData<ChatData>(chatKey(created.id), { ...created, messages: [] })
      queryClient.setQueryData<Chat[]>(['chats', project.id], (chats) => [
        { ...created, title: question },
        ...(chats ?? []),
      ])
      workspace.openChat(created.id, { replace: true })
      ask(queryClient, created.id, question)
    },
  })

  const loading = chatId != null && chat.isPending
  const messages = chat.data?.messages ?? NO_MESSAGES
  // Another member's chat, shared with the project: read here, asked in by its starter alone.
  const starter = chat.data && chat.data.user_id !== me.id ? chat.data.started_by : null
  // The organisation's first steps, for the suggestion to invite its team under an answer, asked
  // each time a chat opens and read only once answered then, since the team may have been invited
  // meanwhile, on the organisation page or by another member.
  const onboarding = useQuery({
    queryKey: ['onboarding', me.organization.id],
    queryFn: organizations.onboarding,
    enabled: !starter && mayInvite(me, project),
  })
  const steps = onboarding.isFetchedAfterMount ? onboarding.data?.steps : undefined
  // Kept once the member invited someone from it, which takes the step, to read where it went,
  // until hidden.
  const [invitedFrom, setInvitedFrom] = useState<InviteNudge | null>(null)
  const nudge = me.invite_hidden ? null : (invitedFrom ?? (starter ? null : inviteNudgeOf(me, project, messages, steps)))
  const nudged = nudge !== null
  useEffect(() => {
    bottom.current?.scrollIntoView({ block: 'end' })
  }, [messages, nudged])

  function send(text: string) {
    const question = text.trim()
    if (!question || streaming || loading || chat.isError || create.isPending || starter) return
    if (chatId == null) {
      create.mutate(question)
      return
    }
    setDraft('')
    ask(queryClient, chatId, question)
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      send(draft)
    }
  }

  function openSource(source: Source) {
    workspace.openDocument(source.document_id, { passage: source.id })
  }

  const unavailable = health.data && !health.data.llm.available
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto px-5">
        <div className="mx-auto max-w-3xl space-y-6 py-5">
          {unavailable && <Notice>{health.data?.llm.error ?? 'The AI model is unavailable.'}</Notice>}
          {loading && <Loading />}
          {chat.isError && (
            <Notice tone="bad">
              {chat.error.message}.{' '}
              <button type="button" onClick={() => workspace.openChat(null, { replace: true })} className="underline">
                Back to your chats
              </button>
            </Notice>
          )}
          {starter && messages.length === 0 && (
            <p className="pt-12 text-center text-[13px] text-muted">{starter} has not asked anything in this chat yet.</p>
          )}
          {!loading && !chat.isError && !starter && messages.length === 0 && (
            <div className="mx-auto max-w-md pt-12 text-center">
              <div className="mx-auto mb-4 flex size-11 items-center justify-center rounded-xl bg-accent-soft text-accent">
                <MessagesSquare className="size-5" />
              </div>
              <p className="text-base font-semibold tracking-tight">Ask about the documents in {project.name}</p>
              <p className="mt-1.5 text-[13px] leading-relaxed text-muted">
                Answers are written by an AI model from the documents, and cite the passages they rely on;
                citations open the document in the library. A model can misread a document or miss a passage,
                so check the cited passages before you rely on an answer.
              </p>
              <div className="mt-6 flex flex-col gap-2 text-left">
                {(project.suggestions.length ? project.suggestions : SUGGESTIONS).map((suggestion) => (
                  <button
                    key={suggestion}
                    onClick={() => send(suggestion)}
                    disabled={unavailable}
                    className="flex items-center justify-between gap-3 rounded-lg border border-line bg-surface px-3.5 py-2.5 text-[13px] shadow-xs transition hover:border-ink/15 hover:bg-sunken/50 disabled:opacity-50"
                  >
                    {suggestion}
                    <ArrowUp className="size-3.5 shrink-0 rotate-45 text-muted" />
                  </button>
                ))}
              </div>
              {chatId == null && shared.length > 0 && <RecentlyShared chats={shared} />}
            </div>
          )}
          {messages.map((message, index) =>
            message.role === 'user' ? (
              <div key={index} className="flex justify-end">
                <p className="max-w-[85%] rounded-2xl rounded-br-md bg-accent-soft px-4 py-2.5 leading-relaxed whitespace-pre-wrap">
                  {message.content}
                </p>
              </div>
            ) : (
              <Answer key={index} message={message} currency={health.data?.currency} onOpen={openSource} />
            ),
          )}
          {nudge && <InviteTeam room={nudge.room} onInvited={() => setInvitedFrom(nudge)} />}
          <div ref={bottom} />
        </div>
      </div>

      <div className="shrink-0 px-5 pb-4">
        <div className="mx-auto w-full max-w-3xl">
          {create.isError && (
            <div className="mb-2">
              <Notice tone="bad">Could not start a chat: {create.error.message}</Notice>
            </div>
          )}
          {starter ? (
            <p className="rounded-xl border border-line bg-surface px-4 py-3 text-center text-[13px] text-muted">
              {starter} shared this chat with the project. You can read it; to ask a question of your own, start a new
              chat.
            </p>
          ) : (
            <div className="flex min-w-0 items-end gap-2 rounded-xl border border-line bg-surface p-2 shadow-xs transition focus-within:border-accent focus-within:ring-3 focus-within:ring-accent/15">
              <textarea
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                onKeyDown={onKeyDown}
                rows={Math.min(6, draft.split('\n').length)}
                placeholder="Ask a question about these documents"
                disabled={chat.isError}
                className="max-h-40 min-w-0 flex-1 resize-none bg-transparent px-2 py-1.5 outline-none placeholder:text-muted/70"
              />
              {streaming ? (
                <Button icon={<Square className="size-3.5" />} onClick={() => chatId != null && stop(chatId)}>
                  Stop
                </Button>
              ) : (
                <button
                  type="button"
                  aria-label="Send"
                  disabled={!draft.trim() || unavailable || loading || chat.isError || create.isPending}
                  onClick={() => send(draft)}
                  className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-accent text-accent-ink shadow-xs transition hover:bg-accent-hover disabled:cursor-not-allowed disabled:opacity-40"
                >
                  <ArrowUp className="size-4" strokeWidth={2.25} />
                </button>
              )}
            </div>
          )}
          <p className="mt-2 text-center text-[11px] text-muted/80">
            Answers are written by AI and can be wrong: check the cited passages
            {!starter && ' · Enter to send · Shift+Enter for a new line'}
          </p>
        </div>
      </div>
    </div>
  )
}

/** The latest chats other members shared with the project, offered beside a new chat's suggestions. */
function RecentlyShared({ chats }: { chats: Chat[] }) {
  const workspace = useWorkspace()
  return (
    <div className="mt-8 text-left">
      <p className="mb-1.5 text-[11px] font-medium tracking-wide text-muted uppercase">Shared with the project</p>
      <ul className="space-y-0.5">
        {chats.slice(0, SHARED_SHOWN).map((chat) => (
          <li key={chat.id}>
            <button
              type="button"
              onClick={() => workspace.openChat(chat.id)}
              className="w-full rounded-lg px-3 py-2 text-left transition hover:bg-sunken"
            >
              <span className="block truncate text-[13px]">{chat.title || 'New chat'}</span>
              <span className="block truncate text-xs text-muted">{sharedBy(chat)}</span>
            </button>
          </li>
        ))}
      </ul>
      {chats.length > SHARED_SHOWN && (
        <p className="mt-1.5 px-3 text-xs text-muted">The chat list holds {chats.length - SHARED_SHOWN} more.</p>
      )}
    </div>
  )
}

interface AnswerProps {
  message: Message
  // The currency the cost is in; unknown until the status has loaded, and the cost waits.
  currency: string | undefined
  onOpen: (source: Source) => void
}

function Answer({ message, currency, onOpen }: AnswerProps) {
  // Number the cited passages in the order the answer first mentions them.
  const cited = [...new Set([...message.content.matchAll(/\[P(\d+)\]/g)].map((match) => Number(match[1])))]
    .filter((id) => message.sources[id])
  const numberOf = (id: number) => cited.indexOf(id) + 1

  return (
    <div className="space-y-2.5">
      {message.steps.length > 0 && (
        <ul className="space-y-1">
          {message.steps.map((step) => (
            <StepLine key={step.id} step={step} />
          ))}
        </ul>
      )}
      {message.thinking && (
        <p className="flex items-center gap-2 text-sm text-muted">
          <Spinner /> Thinking
        </p>
      )}
      {message.content && (
        <Markdown
          text={message.content}
          citation={(id) =>
            message.sources[id] ? (
              <CitationChip number={numberOf(id)} source={message.sources[id]} onOpen={() => onOpen(message.sources[id])} />
            ) : (
              // A reference the agent was never shown: left as plain text, never linked.
              <span className="text-muted">[P{id}]</span>
            )
          }
        />
      )}
      {message.notice && <p className="text-sm text-muted">{message.notice}</p>}
      {message.error &&
        (message.refusedByPlan ? <PlanLimitNotice refusal={message.refusedByPlan} /> : <Notice tone="bad">{message.error}</Notice>)}
      {cited.length > 0 && (
        <div className="border-t border-line pt-2.5">
          <p className="mb-1 text-[11px] font-medium tracking-wide text-muted uppercase">Sources</p>
          <ol className="space-y-0.5 text-xs text-muted">
            {cited.map((id) => (
              <li key={id}>
                <button
                  onClick={() => onOpen(message.sources[id])}
                  className="flex gap-2 rounded px-1 py-0.5 -mx-1 text-left transition hover:bg-sunken hover:text-ink"
                >
                  <span className="w-3 shrink-0 font-semibold text-accent tabular-nums">{numberOf(id)}</span>
                  {sourceLabel(message.sources[id])}
                </button>
              </li>
            ))}
          </ol>
        </div>
      )}
      {message.usage?.calls ? (
        <p className="text-[11px] text-muted/80" title="What this answer used">
          {describeAnswerUsage(message.usage, currency)}
        </p>
      ) : null}
    </div>
  )
}

/** A numbered citation. Hovering shows the cited text; clicking opens it in the document. */
function CitationChip({ number, source, onOpen }: { number: number; source: Source; onOpen: () => void }) {
  const chip = useRef<HTMLButtonElement>(null)
  // The popover's position, set from the chip when it opens.
  const [anchor, setAnchor] = useState<DOMRect | null>(null)
  const timer = useRef<number | undefined>(undefined)
  const show = () => {
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => setAnchor(chip.current?.getBoundingClientRect() ?? null), 250)
  }
  const hide = () => {
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => setAnchor(null), 150)
  }
  useEffect(() => () => window.clearTimeout(timer.current), [])

  return (
    <>
      <button
        ref={chip}
        onClick={onOpen}
        onMouseEnter={show}
        onMouseLeave={hide}
        onFocus={show}
        onBlur={hide}
        title={sourceLabel(source)}
        className="ml-0.5 inline-flex h-[18px] min-w-[18px] items-center justify-center rounded bg-accent-soft px-1 align-text-top text-[11px] font-semibold text-accent transition hover:bg-accent hover:text-accent-ink"
      >
        {number}
      </button>
      {anchor && (
        <Floating anchor={anchor} onClose={() => setAnchor(null)} ignore={chip} className="w-96 max-w-[90vw]">
          <div onMouseEnter={show} onMouseLeave={hide} className="text-[13px]">
            <div className="border-b border-line bg-sunken/50 px-3 py-2">
              <p className="truncate font-semibold">{source.document_title}</p>
              <p className="text-xs text-muted">
                {[sectionPath(source.context, source.document_title), pageLabel(source.page_start, source.page_end)]
                  .filter(Boolean)
                  .join(' · ')}
              </p>
            </div>
            <div className="max-h-56 overflow-y-auto px-3 py-2 text-muted">
              <SourceText text={source.excerpt} />
            </div>
            <div className="border-t border-line px-3 py-1.5">
              <button
                type="button"
                onClick={onOpen}
                className="inline-flex items-center gap-1.5 text-sm font-medium text-accent hover:underline"
              >
                <ExternalLink className="size-4" />
                Open in the document
              </button>
            </div>
          </div>
        </Floating>
      )}
    </>
  )
}

function sourceLabel(source: Source): string {
  return [
    source.document_title,
    sectionPath(source.context, source.document_title),
    pageLabel(source.page_start, source.page_end),
  ]
    .filter(Boolean)
    .join(' · ')
}

const STEP_ICONS: Record<string, LucideIcon> = {
  list_documents: FileText,
  get_outline: ListTree,
  search: Search,
  query_facts: Table2,
  read: BookOpen,
}

function describeStep({ name, input }: Step): string {
  const within = typeof input.folder === 'string' && input.folder ? ` in ${input.folder}` : ''
  switch (name) {
    case 'search':
      return `Searched for "${input.query}"${within}`
    case 'list_documents':
      return input.query || input.doc_type
        ? `Listed documents matching "${input.query ?? input.doc_type}"${within}`
        : `Listed the documents${within}`
    case 'get_outline':
      return `Opened the outline of document ${input.document_id}`
    case 'query_facts':
      return Array.isArray(input.names) && input.names.length
        ? `Looked up facts: ${input.names.join(', ')}${within}`
        : `Looked through the fact table${within}`
    case 'read':
      if (input.section_id != null) return 'Read a section'
      if (input.passage_id != null) return 'Read around a passage'
      return input.page_from != null ? `Read from page ${input.page_from}` : 'Read a document'
    default:
      return name
  }
}

function StepLine({ step }: { step: Step }) {
  const Icon = STEP_ICONS[step.name] ?? Search
  const running = step.summary === undefined
  return (
    <li className="flex items-start gap-2 text-[13px] text-muted">
      {running ? <Spinner className="mt-0.5 size-3.5 shrink-0" /> : <Icon className="mt-0.5 size-3.5 shrink-0" />}
      <span className="min-w-0">
        {describeStep(step)}
        {step.summary && (
          <span className={step.is_error ? 'text-bad' : 'opacity-70'}> · {step.summary}</span>
        )}
      </span>
    </li>
  )
}
