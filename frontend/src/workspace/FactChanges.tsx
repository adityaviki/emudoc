import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, FileDiff, RotateCcw, Table2, TriangleAlert } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { api, mayChange } from '../api.ts'
import type { DocumentChanges } from '../api.ts'
import { ME_KEY, useMe } from '../auth.ts'
import { Confirm } from '../components/Confirm.tsx'
import { Button, EmptyState, IconButton, Loading, Notice } from '../components/ui.tsx'
import {
  CHECK_DISAGREES,
  READING_POLL,
  changesKey,
  factChangesKey,
  fieldChangeWords,
  kindReadAgain,
  queuedWords,
  readAgainQuestion,
  readingsWords,
  templateChangeWords,
} from '../factChanges.ts'
import { factTablesKey, fieldWords } from '../factTable.ts'
import { formatDateTime } from '../format.ts'
import { useStatus } from '../hooks.ts'
import { MODEL_AWAY, templatesKey } from '../templates.ts'
import { listingsKey } from './listing.ts'
import { useWorkspace } from './state.ts'

/**
 * What the last reading of each of the project's documents of a kind changed, in the library's
 * place: once a template is changed and its documents are read again, the fields each reading
 * found, no longer found or read otherwise, those against a member's check first. While its
 * documents are being read it follows them; whoever may change the project reads them again.
 */
export function ChangesView({ kind }: { kind: string }) {
  const workspace = useWorkspace()
  const { project } = workspace
  const changes = mayChange(useMe().role)
  const health = useStatus()
  const queryClient = useQueryClient()
  const heading = useRef<HTMLHeadingElement>(null)
  const [asking, setAsking] = useState(false)
  // Opened from a button that went with the view before it: the keyboard goes on from here.
  useEffect(() => {
    if (document.activeElement === document.body) heading.current?.focus()
  }, [])

  const readings = useInfiniteQuery({
    queryKey: changesKey(project.id, kind),
    queryFn: ({ pageParam }) => api.factChanges(project.id, kind, pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_after,
    refetchInterval: (query) => (query.state.data?.pages[0]?.reading ? READING_POLL : false),
  })
  const first = readings.data?.pages[0]
  const documents = readings.data?.pages.flatMap((page) => page.documents) ?? []
  const template = first?.template ?? null
  const read = template ? kindReadAgain(template) : null
  const modelAway = health.data?.llm.available === false

  // Once the last of them is read, what else counts them is stale.
  const reading = first?.reading ?? 0
  const wasReading = useRef(reading)
  useEffect(() => {
    if (wasReading.current > 0 && reading === 0) {
      for (const queryKey of [templatesKey(project.id), factTablesKey(project.id), listingsKey(project.id)]) {
        void queryClient.invalidateQueries({ queryKey: [...queryKey] })
      }
    }
    wasReading.current = reading
  }, [reading, project.id, queryClient])

  const readAgain = useMutation({
    mutationFn: () => api.readAgain(project.id, read?.ask ?? { kind }),
    onSettled: () => {
      for (const queryKey of [factChangesKey(project.id), templatesKey(project.id), listingsKey(project.id), ['project', project.id], ME_KEY]) {
        void queryClient.invalidateQueries({ queryKey: [...queryKey] })
      }
    },
  })

  const title = template?.title ?? kind
  return (
    <div className="flex h-full min-h-0 flex-col bg-page">
      <div className="flex h-11 shrink-0 items-center gap-1.5 border-b border-line bg-surface px-2">
        <IconButton label="Back to the templates" onClick={workspace.showTemplates}>
          <ArrowLeft className="size-4" />
        </IconButton>
        <h2 ref={heading} tabIndex={-1} className="flex-1 truncate text-[13px] font-semibold">
          What changed in the {title} facts
        </h2>
        {template && template.documents > 0 && (
          <IconButton label={`Facts table of ${title}`} onClick={() => workspace.showFacts(kind)}>
            <Table2 className="size-4" />
          </IconButton>
        )}
        {changes && read && read.documents > 0 && (
          <Button
            icon={<RotateCcw className="size-4" />}
            disabled={readAgain.isPending || reading > 0 || modelAway}
            title={modelAway ? MODEL_AWAY : undefined}
            onClick={() => setAsking(true)}
          >
            Read again
          </Button>
        )}
      </div>
      <div className="min-h-0 flex-1 space-y-2 overflow-y-auto p-3">
        {readAgain.isError && <Notice tone="bad">{readAgain.error.message}</Notice>}
        {readAgain.isSuccess && reading === 0 && readAgain.data.queued === 0 && <Notice tone="info">{queuedWords(0)}</Notice>}
        {readings.isPending && <Loading />}
        {readings.isError && <Notice tone="bad">Could not load what changed: {readings.error.message}</Notice>}
        {first && (
          <p role="status" className="text-xs text-muted">
            {readingsWords(first)}
          </p>
        )}
        {first && documents.length === 0 && first.read > 0 && (
          <EmptyState icon={<FileDiff />} title="Nothing changed">
            Each document of this kind read again found the same values of its template's fields.
          </EmptyState>
        )}
        {documents.length > 0 && (
          <ul aria-label="Documents read differently" className="space-y-2">
            {documents.map((changed) => (
              <ChangedDocument key={changed.document_id} changed={changed} onOpen={() => workspace.openDocument(changed.document_id)} />
            ))}
          </ul>
        )}
        {readings.hasNextPage && (
          <Button disabled={readings.isFetchingNextPage} onClick={() => void readings.fetchNextPage()}>
            {readings.isFetchingNextPage ? 'Loading…' : 'Show more'}
          </Button>
        )}
      </div>
      {asking && read && (
        <Confirm {...readAgainQuestion(read)} danger={false} onConfirm={() => readAgain.mutate()} onClose={() => setAsking(false)} />
      )}
    </div>
  )
}

function ChangedDocument({ changed, onOpen }: { changed: DocumentChanges; onOpen: () => void }) {
  const moved = templateChangeWords(changed)
  return (
    <li className="rounded-md border border-line bg-surface px-3 py-2">
      <div className="flex flex-wrap items-baseline justify-between gap-x-2">
        <button type="button" onClick={onOpen} className="min-w-0 truncate text-left text-[13px] font-medium hover:underline">
          {changed.title}
        </button>
        <span className="shrink-0 text-xs text-muted">Read {formatDateTime(changed.read_at)}</span>
      </div>
      {moved && <p className="text-xs text-muted">{moved}</p>}
      {changed.fields.length > 0 && (
        <dl className="mt-1.5 space-y-1.5">
          {changed.fields.map((field) => (
            <div key={field.name} className="text-xs leading-snug">
              <dt className="font-medium text-ink">{fieldWords(field.name)}</dt>
              <dd className="break-words text-ink">{fieldChangeWords(field)}</dd>
              {field.checked && (
                <dd className="mt-0.5 flex items-start gap-1 text-muted">
                  <TriangleAlert aria-hidden className="mt-px size-3.5 shrink-0 text-warn" />
                  {CHECK_DISAGREES}
                </dd>
              )}
            </div>
          ))}
        </dl>
      )}
    </li>
  )
}
