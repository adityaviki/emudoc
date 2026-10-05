import { useQuery } from '@tanstack/react-query'
import { ArrowUp, BookOpen, FileText, ListTree, Search, Square, Table2 } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import type { KeyboardEvent } from 'react'
import { api, chat, pageLabel, sectionPath } from '../api.ts'
import type { ChatEvent, Source } from '../api.ts'
import { Markdown } from '../components/Markdown.tsx'
import { PassagePanel } from '../components/PassagePanel.tsx'
import { Button, Notice, Spinner } from '../components/ui.tsx'
import { useProject } from '../hooks.ts'

interface Step {
  id: string
  name: string
  input: Record<string, unknown>
  summary?: string
  isError?: boolean
}

interface Message {
  role: 'user' | 'assistant'
  content: string
  steps: Step[]
  sources: Record<number, Source>
  thinking?: boolean
  notice?: string
  error?: string
  // Length of `content` when the current model turn began, to discard a turn that is retried.
  turnStart: number
}

const SUGGESTIONS = [
  'What documents are in this project?',
  'Summarize each document in two sentences.',
  'List every date and deadline, with the document it comes from.',
]

const storageKey = (projectId: number) => `paperless.chat.${projectId}`

/** Mark an answer as finished: nothing in it is still running. */
function settle(message: Message): Message {
  return {
    ...message,
    thinking: false,
    steps: message.steps.map((step) => (step.summary === undefined ? { ...step, summary: '' } : step)),
  }
}

function loadMessages(projectId: number): Message[] {
  try {
    const saved: Message[] = JSON.parse(localStorage.getItem(storageKey(projectId)) ?? '[]')
    return saved.map(settle)
  } catch {
    return []
  }
}

function applyEvent(message: Message, event: ChatEvent): Message {
  switch (event.type) {
    case 'text':
      return { ...message, content: message.content + event.text, thinking: false }
    case 'thinking':
      return { ...message, thinking: true }
    case 'retry':
      return { ...message, content: message.content.slice(0, message.turnStart) }
    case 'tool_call':
      return {
        ...message,
        thinking: false,
        steps: [...message.steps, { id: event.id, name: event.name, input: event.input }],
      }
    case 'tool_result':
      return {
        ...message,
        turnStart: message.content.length,
        steps: message.steps.map((step) =>
          step.id === event.id ? { ...step, summary: event.summary, isError: event.is_error } : step,
        ),
      }
    case 'sources':
      return {
        ...message,
        sources: { ...message.sources, ...Object.fromEntries(event.sources.map((s) => [s.id, s])) },
      }
    case 'notice':
      return { ...message, notice: event.message }
    case 'error':
      return { ...message, thinking: false, error: event.message }
    case 'done':
      return { ...message, thinking: false }
  }
}

export function AskTab() {
  const project = useProject()
  const health = useQuery({ queryKey: ['health'], queryFn: api.health })
  const [messages, setMessages] = useState<Message[]>(() => loadMessages(project.id))
  const [draft, setDraft] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [openPassage, setOpenPassage] = useState<number | null>(null)
  const abort = useRef<AbortController | null>(null)
  const bottom = useRef<HTMLDivElement>(null)

  useEffect(() => {
    localStorage.setItem(storageKey(project.id), JSON.stringify(messages))
    bottom.current?.scrollIntoView({ block: 'end' })
  }, [messages, project.id])

  // Stop a running answer when leaving the tab.
  useEffect(() => () => abort.current?.abort(), [])

  async function send(text: string) {
    const question = text.trim()
    if (!question || streaming) return
    const history = [
      ...messages,
      { role: 'user', content: question, steps: [], sources: {}, turnStart: 0 } satisfies Message,
    ]
    const answer: Message = { role: 'assistant', content: '', steps: [], sources: {}, thinking: true, turnStart: 0 }
    setMessages([...history, answer])
    setDraft('')
    setStreaming(true)

    const update = (change: (message: Message) => Message) =>
      setMessages((current) => [...current.slice(0, -1), change(current[current.length - 1])])
    const controller = new AbortController()
    abort.current = controller
    try {
      const sent = history.map(({ role, content }) => ({ role, content }))
      for await (const event of chat(project.id, sent, controller.signal)) {
        update((message) => applyEvent(message, event))
      }
    } catch (error) {
      const stopped = controller.signal.aborted
      update((message) => ({
        ...message,
        ...(stopped ? { notice: 'Stopped.' } : { error: (error as Error).message }),
      }))
    } finally {
      update(settle)
      setStreaming(false)
    }
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      send(draft)
    }
  }

  const unavailable = health.data && !health.data.llm.available
  return (
    <div className="flex h-[calc(100vh-15rem)] min-h-96 gap-4">
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex-1 overflow-y-auto pr-1">
          <div className="mx-auto max-w-3xl space-y-5 pb-4">
            {unavailable && (
              <Notice>
                The AI model is not configured. {health.data?.llm.error} Then restart the backend
                to ask questions.
              </Notice>
            )}
            {messages.length === 0 && (
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
          <div className="flex items-end gap-2 rounded-xl border border-line bg-surface p-2 focus-within:border-accent">
            <textarea
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={onKeyDown}
              rows={Math.min(6, draft.split('\n').length)}
              placeholder="Ask a question about these documents"
              className="max-h-40 flex-1 resize-none bg-transparent px-2 py-1 outline-none"
            />
            {streaming ? (
              <Button icon={<Square className="size-3.5" />} onClick={() => abort.current?.abort()}>
                Stop
              </Button>
            ) : (
              <Button
                variant="primary"
                aria-label="Send"
                disabled={!draft.trim() || unavailable}
                onClick={() => send(draft)}
                icon={<ArrowUp className="size-4" />}
              />
            )}
          </div>
          {messages.length > 0 && !streaming && (
            <button
              className="mt-1.5 text-xs text-muted hover:text-ink"
              onClick={() => {
                setMessages([])
                setOpenPassage(null)
              }}
            >
              Clear conversation
            </button>
          )}
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
          <span className={step.isError ? 'text-bad' : 'opacity-70'}> · {step.summary}</span>
        )}
      </span>
    </li>
  )
}
