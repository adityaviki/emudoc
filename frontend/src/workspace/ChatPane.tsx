import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ArrowUp,
  BookOpen,
  ChevronDown,
  ExternalLink,
  FileText,
  ListTree,
  MessagesSquare,
  Pencil,
  Plus,
  Search,
  Square,
  Table2,
  Trash2,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import type { KeyboardEvent } from 'react'
import { api, pageLabel, sectionPath } from '../api.ts'
import type { Chat, Source } from '../api.ts'
import { ask, chatKey, isStreaming, loadChat, resume, stop, useStreaming } from '../chat.ts'
import type { ChatData, Message, Step } from '../chat.ts'
import { Markdown, SourceText } from '../components/Markdown.tsx'
import { Floating } from '../components/Menu.tsx'
import { Button, IconButton, Loading, Notice, Spinner } from '../components/ui.tsx'
import { useWorkspace } from './state.ts'

const SUGGESTIONS = [
  'What documents are in this project?',
  'Summarize each document in two sentences.',
  'List every date and deadline, with the document it comes from.',
]

const NO_MESSAGES: Message[] = []

/** The agent: the project's chats, one of them open, with answers that cite the documents. */
export function ChatPane() {
  const workspace = useWorkspace()
  const project = workspace.project
  const chats = useQuery({ queryKey: ['chats', project.id], queryFn: () => api.chats(project.id) })

  if (chats.isPending) return <Loading />
  if (chats.isError) {
    return (
      <div className="p-4">
        <Notice tone="bad">Could not load chats: {chats.error.message}</Notice>
      </div>
    )
  }
  // Without a chat in the URL the latest one opens. "new" is an empty chat until its first question.
  const requested = workspace.url.chatId
  const chatId = requested === 'new' ? null : (requested ?? chats.data[0]?.id ?? null)

  return (
    <div className="flex h-full min-h-0 flex-col bg-page">
      <ChatHeader chats={chats.data} activeId={chatId} />
      <Conversation key={chatId ?? 'new'} chatId={chatId} />
    </div>
  )
}

function ChatHeader({ chats, activeId }: { chats: Chat[]; activeId: number | null }) {
  const workspace = useWorkspace()
  // Where the list opens: set from the button's position when it is clicked.
  const [anchor, setAnchor] = useState<DOMRect | null>(null)
  const button = useRef<HTMLButtonElement>(null)
  const active = chats.find((chat) => chat.id === activeId)
  const title = activeId == null ? 'New chat' : active?.title || 'New chat'
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
        <MessagesSquare className="size-4 shrink-0 text-muted" />
        <span className="min-w-0 truncate font-medium">{title}</span>
        <ChevronDown className="size-3.5 shrink-0 text-muted" />
      </button>
      <span className="flex-1" />
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
          {chats.length === 0 ? (
            <p className="px-3 py-2.5 text-[13px] text-muted">No chats yet. Ask a question to start one.</p>
          ) : (
            <ul className="max-h-80 space-y-0.5 overflow-y-auto p-1">
              {chats.map((chat) => (
                <ChatRow key={chat.id} chat={chat} active={chat.id === activeId} onOpen={close} />
              ))}
            </ul>
          )}
        </Floating>
      )}
    </div>
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
          className="min-w-0 flex-1 truncate px-2.5 py-1.5 text-left text-[13px]"
        >
          {title}
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
            <IconButton
              label="Delete chat"
              danger
              disabled={remove.isPending}
              onClick={() => {
                if (window.confirm(`Delete "${title}"?`)) remove.mutate()
              }}
            >
              <Trash2 className="size-3.5" />
            </IconButton>
          </span>
        )
      )}
    </li>
  )
}

function Conversation({ chatId }: { chatId: number | null }) {
  const workspace = useWorkspace()
  const project = workspace.project
  const queryClient = useQueryClient()
  const health = useQuery({ queryKey: ['status'], queryFn: api.status })
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
  useEffect(() => {
    bottom.current?.scrollIntoView({ block: 'end' })
  }, [messages])

  function send(text: string) {
    const question = text.trim()
    if (!question || streaming || loading || chat.isError || create.isPending) return
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
          {unavailable && (
            <Notice>
              The AI model is not configured. {health.data?.llm.error} Then restart the backend to ask
              questions.
            </Notice>
          )}
          {loading && <Loading />}
          {chat.isError && (
            <Notice tone="bad">
              {chat.error.message}.{' '}
              <button type="button" onClick={() => workspace.openChat(null, { replace: true })} className="underline">
                Back to your chats
              </button>
            </Notice>
          )}
          {!loading && !chat.isError && messages.length === 0 && (
            <div className="mx-auto max-w-md pt-12 text-center">
              <div className="mx-auto mb-4 flex size-11 items-center justify-center rounded-xl bg-accent-soft text-accent">
                <MessagesSquare className="size-5" />
              </div>
              <p className="text-base font-semibold tracking-tight">Ask about the documents in {project.name}</p>
              <p className="mt-1.5 text-[13px] leading-relaxed text-muted">
                Answers are read from the documents and cite the passages they rely on. Citations open
                the document in the library.
              </p>
              <div className="mt-6 flex flex-col gap-2 text-left">
                {SUGGESTIONS.map((suggestion) => (
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
              <Answer key={index} message={message} onOpen={openSource} />
            ),
          )}
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
          <p className="mt-2 text-center text-[11px] text-muted/80">
            Enter to send · Shift+Enter for a new line
          </p>
        </div>
      </div>
    </div>
  )
}

function Answer({ message, onOpen }: { message: Message; onOpen: (source: Source) => void }) {
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
      {message.error && <Notice tone="bad">{message.error}</Notice>}
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
