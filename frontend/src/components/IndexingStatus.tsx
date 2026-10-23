import { CircleAlert, CircleCheck, ShieldAlert } from 'lucide-react'
import { STAGES, isIndexing } from '../api.ts'
import type { Document, Stage } from '../api.ts'
import { missingStages, missingWords, stageOf, statusLabel, statusTitle, unfinishedStages, waitingRetry } from '../indexing.ts'

// Each stage's arc in the ring while the document is indexed. Every arc but a pending one, the
// faint track the others fill, stands out from the row under it by 3:1 or more (WCAG's non-text
// contrast), so that a stage running or skipped is seen without the pulse or the tooltip.
const ARC_TONES: Record<Stage['status'], string> = {
  done: 'text-accent',
  running: 'text-accent/70 animate-pulse',
  failed: 'text-bad',
  skipped: 'text-muted',
  pending: 'text-line',
}
// A failed stage that waits to run again by itself, and a document partly indexed, which a click
// finishes: amber, since neither is a failure for good, and as plain against the row as the others.
const WAITING_TONE = 'text-warn'
// The ring is a circle of circumference 100 with an arc per stage, starting at the top: each
// arc takes its share less a gap on either side.
const ARC = 100 / STAGES.length
const GAP = 3

/**
 * The document's indexing state as one icon: while it is indexed, a ring with an arc per stage that
 * fills in as they finish, a stage that waits for a retry in amber, which keeps the ring on a
 * ready document until the retry; once ready, a check, green with every stage done, amber while
 * it is partly indexed and grey without a scan the deployment runs none for; an alert when it
 * failed, and a shield when the malware scan quarantined it.
 */
export function StatusIcon({ id, document, className = 'size-3.5' }: { id?: string; document: Document; className?: string }) {
  const title = statusTitle(document)
  const retry = waitingRetry(document)
  return (
    <span id={id} role="img" aria-label={title} title={title} className="inline-flex shrink-0">
      {document.status === 'quarantined' ? (
        <ShieldAlert className={`text-bad ${className}`} />
      ) : document.status === 'failed' ? (
        <CircleAlert className={`text-bad ${className}`} />
      ) : document.status === 'ready' && !retry ? (
        <CircleCheck className={`${checkTone(document)} ${className}`} />
      ) : (
        <svg viewBox="0 0 16 16" fill="none" strokeWidth={2} className={`-rotate-90 ${className}`}>
          {STAGES.map((name, index) => (
            <circle
              key={name}
              cx={8}
              cy={8}
              r={6}
              pathLength={100}
              strokeDasharray={`${ARC - 2 * GAP} ${100 - ARC + 2 * GAP}`}
              strokeDashoffset={-(index * ARC + GAP)}
              strokeLinecap="round"
              className={`stroke-current ${retry?.stage === name ? WAITING_TONE : ARC_TONES[stageOf(document, name).status]}`}
            />
          ))}
        </svg>
      )}
    </span>
  )
}

const checkTone = (document: Document) =>
  unfinishedStages(document).length ? WAITING_TONE : missingStages(document).length ? 'text-muted' : 'text-good'

/**
 * The status icon with the document's state beside it, and what went wrong, was left out, or
 * waits for a retry, and why: for a partly indexed document, what it lacks and why each stage
 * it went without failed (a skipped one's reason is the service, which "Index the rest" says
 * beside it).
 */
export function IndexingStatus({ document }: { document: Document }) {
  const missing = missingWords(document)
  const wrong = document.status === 'failed' || document.status === 'quarantined'
  const retry = waitingRetry(document)
  // A queued document's retry is its whole state; a ready one's is the line under it.
  const retrying = document.status === 'queued' && retry
  const failedStages = unfinishedStages(document).filter((name) => {
    const stage = stageOf(document, name)
    return stage.status === 'failed' && stage.detail
  })
  const tone = wrong ? 'text-bad' : retrying ? WAITING_TONE : isIndexing(document) ? 'text-accent' : 'text-muted'

  return (
    <div>
      <div className={`flex items-center gap-1.5 ${tone}`}>
        <StatusIcon document={document} />
        <span>{statusLabel(document)}</span>
      </div>
      {wrong && document.error && (
        <p className="mt-0.5 max-w-xs text-xs text-bad">{document.error}</p>
      )}
      {retrying && stageOf(document, retry.stage).detail && (
        <p className="mt-0.5 max-w-xs text-xs text-muted">{stageOf(document, retry.stage).detail}</p>
      )}
      {document.status === 'ready' && missing && <p className="mt-0.5 text-xs text-muted">{missing}</p>}
      {failedStages.map((name) => (
        <p key={name} className="mt-0.5 max-w-xs text-xs text-muted">
          {stageOf(document, name).detail}
        </p>
      ))}
    </div>
  )
}

/** A single dot for the document as a whole: shown where there is no room for the stages. */
export function StatusDot({ document, className = 'size-1.5' }: { document: Document; className?: string }) {
  const tone =
    document.status === 'failed' || document.status === 'quarantined'
      ? 'bg-bad'
      : waitingRetry(document) || unfinishedStages(document).length
        ? 'bg-warn'
        : isIndexing(document)
          ? 'bg-accent animate-pulse'
          : 'bg-good'
  const title = [statusLabel(document), document.status === 'ready' && missingWords(document)].filter(Boolean).join('. ')
  return <span title={title} className={`inline-block shrink-0 rounded-full ${tone} ${className}`} />
}
