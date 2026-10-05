import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Files, RefreshCw, Sparkles, Trash2, Upload } from 'lucide-react'
import { useRef, useState } from 'react'
import type { DragEvent } from 'react'
import { Link } from 'react-router'
import { STAGES, api } from '../api.ts'
import type { Document, Stage, StageName, UploadResult } from '../api.ts'
import { Button, Chip, EmptyState, IconButton, Loading, Notice, Spinner } from '../components/ui.tsx'
import { formatDate } from '../format.ts'
import { useProject } from '../hooks.ts'

const STAGE_LABELS: Record<StageName, string> = {
  parse: 'Parse',
  structure: 'Outline',
  enrich: 'Summaries and facts',
  embed: 'Embeddings',
}

const isIndexing = (document: Document) =>
  document.status === 'queued' || document.status === 'processing'

export function DocumentsTab() {
  const project = useProject()
  const queryClient = useQueryClient()
  const documents = useQuery({
    queryKey: ['documents', project.id],
    queryFn: () => api.documents(project.id),
    // Poll while anything is still being indexed.
    refetchInterval: (query) => (query.state.data?.some(isIndexing) ? 1500 : false),
  })
  const health = useQuery({ queryKey: ['health'], queryFn: api.health })
  const [rejected, setRejected] = useState<UploadResult['rejected']>([])

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['documents', project.id] })
    queryClient.invalidateQueries({ queryKey: ['project', project.id] })
  }
  const upload = useMutation({
    mutationFn: (files: File[]) => api.upload(project.id, files),
    onSuccess: (result) => {
      setRejected(result.rejected)
      refresh()
    },
  })
  const enrichAll = useMutation({
    mutationFn: () => api.reprocessProject(project.id, 'enrich'),
    onSuccess: refresh,
  })

  // Documents indexed before a model was configured can catch up in one go.
  const waitingForModel = documents.data?.filter((d) => d.stages.enrich?.status === 'skipped') ?? []

  return (
    <div className="space-y-4">
      <Dropzone busy={upload.isPending} onFiles={(files) => upload.mutate(files)} />

      {upload.isError && <Notice tone="bad">Upload failed: {upload.error.message}</Notice>}
      {rejected.length > 0 && (
        <Notice>
          <div className="flex items-start justify-between gap-4">
            <ul>
              {rejected.map((file) => (
                <li key={file.filename}>
                  <span className="font-medium">{file.filename}</span>: {file.reason}
                </li>
              ))}
            </ul>
            <button className="text-xs underline" onClick={() => setRejected([])}>
              Dismiss
            </button>
          </div>
        </Notice>
      )}
      {health.data?.llm.available && waitingForModel.length > 0 && (
        <Notice tone="info">
          <div className="flex items-center justify-between gap-4">
            <span>
              {waitingForModel.length} document{waitingForModel.length === 1 ? ' has' : 's have'} no
              summaries or facts yet.
            </span>
            <Button
              icon={<Sparkles className="size-4" />}
              onClick={() => enrichAll.mutate()}
              disabled={enrichAll.isPending}
            >
              Run the model on them
            </Button>
          </div>
        </Notice>
      )}

      {documents.isPending && <Loading />}
      {documents.isError && <Notice tone="bad">Could not load documents: {documents.error.message}</Notice>}
      {documents.data?.length === 0 && (
        <EmptyState icon={<Files className="size-8" />} title="No documents yet">
          Upload files above. Each one is parsed, outlined and indexed in the background.
        </EmptyState>
      )}
      {documents.data && documents.data.length > 0 && (
        <div className="overflow-hidden rounded-lg border border-line bg-surface">
          <table className="w-full table-fixed text-sm">
            <thead className="border-b border-line text-left text-xs text-muted">
              <tr>
                <th className="px-4 py-2 font-medium">Document</th>
                <th className="hidden w-32 px-3 py-2 font-medium md:table-cell">Type</th>
                <th className="hidden w-16 px-3 py-2 font-medium md:table-cell">Pages</th>
                <th className="hidden w-28 px-3 py-2 font-medium md:table-cell">Added</th>
                <th className="w-36 px-3 py-2 font-medium sm:w-48">Indexing</th>
                <th className="w-20" />
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {documents.data.map((document) => (
                <DocumentRow key={document.id} document={document} onChange={refresh} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function Dropzone({ busy, onFiles }: { busy: boolean; onFiles: (files: File[]) => void }) {
  const input = useRef<HTMLInputElement>(null)
  const [over, setOver] = useState(false)

  function drop(event: DragEvent) {
    event.preventDefault()
    setOver(false)
    const files = [...event.dataTransfer.files]
    if (files.length) onFiles(files)
  }

  return (
    <div
      onDragOver={(event) => {
        event.preventDefault()
        setOver(true)
      }}
      onDragLeave={() => setOver(false)}
      onDrop={drop}
      className={`flex flex-col items-center gap-2 rounded-lg border-2 border-dashed px-6 py-7 text-center transition ${
        over ? 'border-accent bg-accent-soft' : 'border-line bg-surface'
      }`}
    >
      {busy ? <Spinner className="size-6" /> : <Upload className="size-6 text-muted" />}
      <p className="text-sm">
        Drop files here or{' '}
        <button className="font-medium text-accent underline-offset-2 hover:underline" onClick={() => input.current?.click()}>
          choose files
        </button>
      </p>
      <p className="text-xs text-muted">PDF, Word (.docx), text, Markdown and images</p>
      <input
        ref={input}
        type="file"
        multiple
        hidden
        accept=".pdf,.docx,.txt,.md,.png,.jpg,.jpeg,.webp"
        onChange={(event) => {
          const files = [...(event.target.files ?? [])]
          if (files.length) onFiles(files)
          event.target.value = ''
        }}
      />
    </div>
  )
}

function DocumentRow({ document, onChange }: { document: Document; onChange: () => void }) {
  const reprocess = useMutation({
    mutationFn: () => api.reprocessDocument(document.id, 'parse'),
    onSuccess: onChange,
  })
  const remove = useMutation({ mutationFn: () => api.deleteDocument(document.id), onSuccess: onChange })

  return (
    <tr className="group align-top">
      <td className="px-4 py-2.5">
        <Link to={`documents/${document.id}`} className="block truncate font-medium hover:text-accent">
          {document.title ?? document.filename}
        </Link>
        <p className="truncate text-xs text-muted">
          {document.title && `${document.filename} · `}
          {document.summary ?? (document.status === 'ready' ? 'No summary' : '')}
        </p>
      </td>
      <td className="hidden px-3 py-2.5 md:table-cell">
        {document.doc_type && <Chip>{document.doc_type}</Chip>}
      </td>
      <td className="hidden px-3 py-2.5 text-muted md:table-cell">{document.page_count ?? ''}</td>
      <td className="hidden px-3 py-2.5 whitespace-nowrap text-muted md:table-cell">
        {formatDate(document.created_at)}
      </td>
      <td className="px-3 py-2.5">
        <IndexingStatus document={document} />
      </td>
      <td className="px-2 py-1.5 text-right whitespace-nowrap">
        <span className="transition focus-within:opacity-100 md:opacity-0 md:group-hover:opacity-100">
          <IconButton
            label="Index again from the start"
            onClick={() => reprocess.mutate()}
            disabled={document.status === 'processing' || reprocess.isPending}
          >
            <RefreshCw className="size-4" />
          </IconButton>
          <IconButton
            label="Delete document"
            danger
            disabled={remove.isPending}
            onClick={() => {
              if (window.confirm(`Delete "${document.filename}"?`)) remove.mutate()
            }}
          >
            <Trash2 className="size-4" />
          </IconButton>
        </span>
      </td>
    </tr>
  )
}

const DOT_TONES: Record<Stage['status'], string> = {
  done: 'bg-good',
  running: 'bg-accent animate-pulse',
  failed: 'bg-bad',
  skipped: 'border border-muted',
  pending: 'bg-line',
}

export function IndexingStatus({ document }: { document: Document }) {
  const label = {
    queued: 'Queued',
    processing: 'Indexing',
    ready: 'Ready',
    failed: 'Failed',
  }[document.status]
  // A ready document may still be missing an optional stage; say which.
  const partial = STAGES.filter((name) => ['failed', 'skipped'].includes(document.stages[name]?.status ?? ''))

  return (
    <div>
      <div className="flex items-center gap-2">
        <span className="flex gap-1">
          {STAGES.map((name) => {
            const stage = document.stages[name] ?? { status: 'pending', detail: '' }
            return (
              <span
                key={name}
                title={`${STAGE_LABELS[name]}: ${stage.status}${stage.detail ? ` (${stage.detail})` : ''}`}
                className={`size-2 rounded-full ${DOT_TONES[stage.status]}`}
              />
            )
          })}
        </span>
        <span className={document.status === 'failed' ? 'text-bad' : 'text-muted'}>{label}</span>
      </div>
      {document.status === 'failed' && document.error && (
        <p className="mt-0.5 max-w-xs text-xs text-bad">{document.error}</p>
      )}
      {document.status === 'ready' && partial.length > 0 && (
        <p className="mt-0.5 text-xs text-muted">
          Without {partial.map((name) => STAGE_LABELS[name].toLowerCase()).join(', ')}
        </p>
      )}
    </div>
  )
}
