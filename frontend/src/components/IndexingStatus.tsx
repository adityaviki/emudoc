import { CircleAlert, CircleCheck } from 'lucide-react'
import { STAGES, isIndexing } from '../api.ts'
import type { Document, Stage } from '../api.ts'
import { STAGE_LABELS, STATUS_LABELS, missingStages, stageOf, statusLabel, statusTitle } from '../indexing.ts'

// Each stage's arc in the ring while the document is indexed.
const ARC_TONES: Record<Stage['status'], string> = {
  done: 'text-accent',
  running: 'text-accent/50 animate-pulse',
  failed: 'text-bad',
  skipped: 'text-muted/30',
  pending: 'text-line',
}

/**
 * The document's indexing state as one icon: while it is indexed, a ring with an arc per stage that
 * fills in as they finish; once ready, a check, green with every stage done and grey without one
 * of them; an alert when it failed.
 */
export function StatusIcon({ document, className = 'size-3.5' }: { document: Document; className?: string }) {
  const title = statusTitle(document)
  return (
    <span role="img" aria-label={title} title={title} className="inline-flex shrink-0">
      {document.status === 'failed' ? (
        <CircleAlert className={`text-bad ${className}`} />
      ) : document.status === 'ready' ? (
        <CircleCheck className={`${missingStages(document).length ? 'text-muted' : 'text-good'} ${className}`} />
      ) : (
        // Four arcs on a circle of circumference 100, each a quarter less a gap, starting at the top.
        <svg viewBox="0 0 16 16" fill="none" strokeWidth={2} className={`-rotate-90 ${className}`}>
          {STAGES.map((name, index) => (
            <circle
              key={name}
              cx={8}
              cy={8}
              r={6}
              pathLength={100}
              strokeDasharray="19 81"
              strokeDashoffset={-(index * 25 + 3)}
              strokeLinecap="round"
              className={`stroke-current ${ARC_TONES[stageOf(document, name).status]}`}
            />
          ))}
        </svg>
      )}
    </span>
  )
}

/** The status icon with the document's state beside it, and what went wrong or was left out. */
export function IndexingStatus({ document }: { document: Document }) {
  const missing = missingStages(document)
  const tone = document.status === 'failed' ? 'text-bad' : isIndexing(document) ? 'text-accent' : 'text-muted'

  return (
    <div>
      <div className={`flex items-center gap-1.5 ${tone}`}>
        <StatusIcon document={document} />
        <span>{statusLabel(document)}</span>
      </div>
      {document.status === 'failed' && document.error && (
        <p className="mt-0.5 max-w-xs text-xs text-bad">{document.error}</p>
      )}
      {document.status === 'ready' && missing.length > 0 && (
        <p className="mt-0.5 text-xs text-muted">
          Without {missing.map((name) => STAGE_LABELS[name].toLowerCase()).join(', ')}
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
