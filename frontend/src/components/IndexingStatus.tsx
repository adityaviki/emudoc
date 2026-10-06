import { STAGES, isIndexing } from '../api.ts'
import type { Document, Stage, StageName } from '../api.ts'

const STAGE_LABELS: Record<StageName, string> = {
  parse: 'Parse',
  structure: 'Outline',
  enrich: 'Summaries and facts',
  embed: 'Embeddings',
}

const DOT_TONES: Record<Stage['status'], string> = {
  done: 'bg-good',
  running: 'bg-accent animate-pulse',
  failed: 'bg-bad',
  skipped: 'border border-muted',
  pending: 'bg-line',
}

const STATUS_LABELS: Record<Document['status'], string> = {
  queued: 'Queued',
  processing: 'Indexing',
  ready: 'Ready',
  failed: 'Failed',
}

/** One dot per indexing stage, with the document's overall state beside them. */
export function IndexingStatus({ document }: { document: Document }) {
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
        <span className={document.status === 'failed' ? 'text-bad' : 'text-muted'}>
          {STATUS_LABELS[document.status]}
        </span>
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

/** A single dot for the document as a whole: shown where there is no room for the stages. */
export function StatusDot({ document, className = 'size-1.5' }: { document: Document; className?: string }) {
  const tone =
    document.status === 'failed' ? 'bg-bad' : isIndexing(document) ? 'bg-accent animate-pulse' : 'bg-good'
  return <span title={STATUS_LABELS[document.status]} className={`inline-block shrink-0 rounded-full ${tone} ${className}`} />
}
