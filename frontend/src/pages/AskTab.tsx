import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ArrowUp,
  BookOpen,
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
import { Link, Navigate, useNavigate, useParams } from 'react-router'
import { api, pageLabel, sectionPath } from '../api.ts'
import type { Chat, Source } from '../api.ts'
import { ask, chatKey, loadChat, stop, useStreaming } from '../chat.ts'
import type { ChatData, Message, Step } from '../chat.ts'
import { Markdown } from '../components/Markdown.tsx'
import { PassagePanel } from '../components/PassagePanel.tsx'
import { Button, IconButton, Loading, Notice, Spinner } from '../components/ui.tsx'
import { useProject } from '../hooks.ts'

const SUGGESTIONS = [
  'What documents are in this project?',
  'Summarize each document in two sentences.',
  'List every date and deadline, with the document it comes from.',
]

const NO_MESSAGES: Message[] = []

export function AskTab() {
  const project = useProject()
  const { chatId: param } = useParams()
  const chats = useQuery({ queryKey: ['chats', project.id], queryFn: () => api.chats(project.id) })
  const [listOpen, setListOpen] = useState(false)

  if (chats.isPending) return <Loading />
  if (chats.isError) return <Notice tone="bad">Could not load chats: {chats.error.message}</Notice>
  // The tab opens on the latest chat. "new" is an empty chat until its first question.
  if (param === undefined && chats.data.length > 0) {
    return <Navigate to={`/projects/${project.id}/ask/${chats.data[0].id}`} replace />
  }
  const chatId = param === undefined || param === 'new' ? null : Number(param)

  return (
    <div className="flex h-[calc(100vh-15rem)] min-h-96 gap-4">
      <ChatList chats={chats.data} activeId={chatId} open={listOpen} onClose={() => setListOpen(false)} />
      <Conversation key={chatId ?? 'new'} chatId={chatId} onShowList={() => setListOpen(true)} />
    </div>
  )
}

interface ChatListProps {
  chats: Chat[]
  activeId: number | null
  open: boolean
  onClose: () => void
}

function ChatList({ chats, activeId, open, onClose }: ChatListProps) {
  const project = useProject()
  const navigate = useNavigate()
  return (
    // On small screens the list is an overlay, opened from the conversation.
    <div
      onClick={onClose}
      className={`${open ? 'fixed inset-0 z-10 bg-page/80 p-4' : 'hidden'} md:static md:block md:w-56 md:shrink-0 md:bg-transparent md:p-0`}
    >
      <aside
        onClick={(event) => event.stopPropagation()}
        className="flex h-full w-64 max-w-full flex-col gap-2 rounded-lg border border-line bg-surface p-2 md:w-auto md:border-0 md:bg-transparent md:p-0"
      >
        <Button
          icon={<Plus className="size-4" />}
          className="justify-center"
          disabled={activeId === null}
          onClick={() => {
            navigate(`/projects/${project.id}/ask/new`)
            onClose()
          }}
        >
          New chat
        </Button>
        <ul className="flex-1 space-y-0.5 overflow-y-auto">
          {chats.map((chat) => (
            <ChatRow key={chat.id} chat={chat} active={chat.id === activeId} onOpen={onClose} />
          ))}
        </ul>
      </aside>
    </div>
  )
}

function ChatRow({ chat, active, onOpen }: { chat: Chat; active: boolean; onOpen: () => void }) {
  const project = useProject()
  const navigate = useNavigate()
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
      if (active) navigate(`/projects/${project.id}/ask`)
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
          className="min-w-0 flex-1 rounded-md border border-accent bg-surface px-2 py-1 text-sm outline-none"
          onBlur={(event) => commitTitle(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') commitTitle(event.currentTarget.value)
            if (event.key === 'Escape') setRenaming(false)
          }}
        />
      ) : (
        <Link
          to={`/projects/${project.id}/ask/${chat.id}`}
          onClick={onOpen}
          title={title}
          className="min-w-0 flex-1 truncate px-2.5 py-1.5 text-sm"
        >
          {title}
        </Link>
      )}
      {streaming ? (
        <Spinner className="mr-2.5 size-3.5 shrink-0" />
      ) : (
        active &&
        !renaming && (
          <span className="flex shrink-0 pr-0.5">
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

function Conversation({ chatId, onShowList }: { chatId: number | null; onShowList: () => void }) {
  const project = useProject()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const health = useQuery({ queryKey: ['health'], queryFn: api.health })
  const chat = useQuery({
    queryKey: chatKey(chatId),
    queryFn: () => api.chat(chatId!).then(loadChat),
    enabled: chatId != null,
    // Answers stream straight into this data; the saved copy is only read when a chat is opened.
    staleTime: Infinity,
  })
  const streaming = useStreaming(chatId)
  const [draft, setDraft] = useState('')
  const [openPassage, setOpenPassage] = useState<number | null>(null)
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
      navigate(`/projects/${project.id}/ask/${created.id}`, { replace: true })
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

  const unavailable = health.data && !health.data.llm.available
  return (
    <div className="flex min-w-0 flex-1 gap-4">
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="mb-2 flex items-center gap-2 md:hidden">
          <Button variant="ghost" icon={<MessagesSquare className="size-4" />} onClick={onShowList}>
            Chats
          </Button>
          <p className="truncate text-sm text-muted">{chat.data?.title || 'New chat'}</p>
        </div>
        <div className="flex-1 overflow-y-auto pr-1">
          <div className="mx-auto max-w-3xl space-y-5 pb-4">
            {unavailable && (
              <Notice>
                The AI model is not configured. {health.data?.llm.error} Then restart the backend
                to ask questions.
              </Notice>
            )}
            {loading && <Loading />}
            {chat.isError && (
              <Notice tone="bad">
                {chat.error.message}.{' '}
                <Link to={`/projects/${project.id}/ask`} className="underline">
                  Back to your chats
                </Link>
              </Notice>
            )}
            {!loading && !chat.isError && messages.length === 0 && (
              <div className="pt-10 text-center">
                <p className="font-medium">Ask about the documents in {project.name}</p>
                <p className="mt-1 text-sm text-muted">
                  Answers are read from the documents and cite the passages they rely on.
                </p>
                <div className="mt-5 flex flex-col items-center gap-2">
                  {SUGGESTIONS.map((suggestion) => (
                    <button
                      key={suggestion}
                      onClick={() => send(suggestion)}
                      disabled={unavailable}
                      className="rounded-full border border-line bg-surface px-3.5 py-1.5 text-sm transition hover:border-accent disabled:opacity-50"
                    >
                      {suggestion}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {messages.map((message, index) =>
              message.role === 'user' ? (
                <div key={index} className="flex justify-end">
                  <p className="max-w-[85%] rounded-2xl rounded-br-sm bg-accent-soft px-4 py-2 whitespace-pre-wrap">
                    {message.content}
                  </p>
                </div>
              ) : (
                <Answer key={index} message={message} onOpen={setOpenPassage} />
              ),
            )}
            <div ref={bottom} />
          </div>
        </div>

        <div className="mx-auto w-full max-w-3xl">
          {create.isError && (
            <div className="mb-2">
              <Notice tone="bad">Could not start a chat: {create.error.message}</Notice>
            </div>
          )}
          <div className="flex items-end gap-2 rounded-xl border border-line bg-surface p-2 focus-within:border-accent">
            <textarea
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={onKeyDown}
              rows={Math.min(6, draft.split('\n').length)}
              placeholder="Ask a question about these documents"
              disabled={chat.isError}
              className="max-h-40 flex-1 resize-none bg-transparent px-2 py-1 outline-none"
            />
            {streaming ? (
              <Button icon={<Square className="size-3.5" />} onClick={() => chatId != null && stop(chatId)}>
                Stop
              </Button>
            ) : (
              <Button
                variant="primary"
                aria-label="Send"
                disabled={!draft.trim() || unavailable || loading || chat.isError || create.isPending}
                onClick={() => send(draft)}
                icon={<ArrowUp className="size-4" />}
              />
            )}
          </div>
        </div>
      </div>

      {openPassage != null && (
        <div className="fixed inset-0 z-10 bg-page/80 p-4 md:static md:w-96 md:shrink-0 md:bg-transparent md:p-0">
          <PassagePanel projectId={project.id} passageId={openPassage} onClose={() => setOpenPassage(null)} />
        </div>
      )}
    </div>
  )
}

function Answer({ message, onOpen }: { message: Message; onOpen: (passageId: number) => void }) {
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
              <button
                onClick={() => onOpen(id)}
                title={sourceLabel(message.sources[id])}
                className="ml-0.5 inline-flex size-5 items-center justify-center rounded bg-accent-soft align-text-top text-xs font-semibold text-accent hover:bg-accent hover:text-white"
              >
                {numberOf(id)}
              </button>
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
        <ol className="space-y-0.5 border-t border-line pt-2 text-xs text-muted">
          {cited.map((id) => (
            <li key={id}>
              <button onClick={() => onOpen(id)} className="flex gap-2 text-left hover:text-ink">
                <span className="font-semibold text-accent">{numberOf(id)}</span>
                {sourceLabel(message.sources[id])}
              </button>
            </li>
          ))}
        </ol>
      )}
    </div>
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
  switch (name) {
    case 'search':
      return `Searched for "${input.query}"`
    case 'list_documents':
      return input.query || input.doc_type
        ? `Listed documents matching "${input.query ?? input.doc_type}"`
        : 'Listed the documents'
    case 'get_outline':
      return `Opened the outline of document ${input.document_id}`
    case 'query_facts':
      return Array.isArray(input.names) && input.names.length
        ? `Looked up facts: ${input.names.join(', ')}`
        : 'Looked through the fact table'
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
    <li className="flex items-start gap-2 text-sm text-muted">
      {running ? <Spinner className="mt-0.5 size-4 shrink-0" /> : <Icon className="mt-0.5 size-4 shrink-0" />}
      <span className="min-w-0">
        {describeStep(step)}
        {step.summary && (
          <span className={step.is_error ? 'text-bad' : 'opacity-70'}> · {step.summary}</span>
        )}
      </span>
    </li>
  )
}
