import { CircleAlert, CircleCheck, CircleHelp, X } from 'lucide-react'
import { ApiError } from '../api.ts'
import type { Fact, Finding, FindingOutcome } from '../api.ts'
import { PlanLimitNotice } from '../components/PlanLimit.tsx'
import { IconButton, Notice } from '../components/ui.tsx'
import { OUTCOMES, findingWords } from '../crossChecks.ts'
import type { Worded } from '../crossChecks.ts'
import { fieldWords, quoteWords } from '../factTable.ts'

// What a cross-check found, as the view of a rule (CrossChecks.tsx) and the view of a run
// (CheckRuns.tsx) both draw it, and how either says a request was refused.

const MARKS: Record<FindingOutcome, { icon: typeof CircleAlert; tone: string }> = {
  differs: { icon: CircleAlert, tone: 'text-bad' },
  missing: { icon: CircleHelp, tone: 'text-warn' },
  holds: { icon: CircleCheck, tone: 'text-good' },
}

type Open = (documentId: number, fact: Fact | null) => void

/** A finding: its document, its outcome and why, and the values on both sides, each opening its document where it is written, unless the document is `gone` since. */
export function FindingItem({ finding, check, onOpen, gone }: { finding: Finding; check: Worded; onOpen: Open; gone?: ReadonlySet<number> }) {
  const mark = MARKS[finding.outcome]
  return (
    <li className="rounded-md border border-line bg-surface px-3 py-2">
      <div className="flex flex-wrap items-baseline justify-between gap-x-2">
        {gone?.has(finding.document_id) ? (
          <span className="min-w-0 truncate text-[13px] font-medium">
            {finding.title} <span className="font-normal text-muted">(deleted since)</span>
          </span>
        ) : (
          <button type="button" onClick={() => onOpen(finding.document_id, null)} className="min-w-0 truncate text-left text-[13px] font-medium hover:underline">
            {finding.title}
          </button>
        )}
        <span className="flex shrink-0 items-center gap-1 text-xs text-ink">
          <mark.icon aria-hidden className={`size-3.5 ${mark.tone}`} />
          {OUTCOMES[finding.outcome]}
        </span>
      </div>
      <p className="text-xs text-ink">{findingWords(finding, check)}</p>
      {(finding.left || finding.right.length > 0) && (
        <dl className="mt-1.5 space-y-1.5">
          {finding.left && <Side label={fieldWords(check.left_field)} fact={finding.left} onOpen={onOpen} gone={gone} />}
          {finding.right.map((fact) => (
            <Side
              key={fact.id}
              label={check.pairing === 'same' ? fieldWords(check.right_field) : `${fieldWords(check.right_field)}, ${fact.document_title}`}
              fact={fact}
              onOpen={onOpen}
              gone={gone}
            />
          ))}
        </dl>
      )}
    </li>
  )
}

/** A value compared, opening its document where it is written, and where that is. */
function Side({ label, fact, onOpen, gone }: { label: string; fact: Fact; onOpen: Open; gone?: ReadonlySet<number> }) {
  return (
    <div className="text-xs leading-snug">
      <dt className="text-muted">{label}</dt>
      <dd>
        {gone?.has(fact.document_id) ? (
          <span className="font-medium break-words text-ink">{fact.value}</span>
        ) : (
          <button
            type="button"
            onClick={() => onOpen(fact.document_id, fact)}
            title={`Open ${fact.document_title} where it says so`}
            className="text-left font-medium break-words text-ink underline decoration-dotted underline-offset-2 hover:decoration-solid"
          >
            {fact.value}
          </button>
        )}
        {fact.check && <span className="text-muted"> · {fact.check.corrected_from != null ? 'corrected' : 'checked'}</span>}
      </dd>
      <dd className="break-words text-muted">{quoteWords(fact)}</dd>
    </div>
  )
}

/** A refusal as the views show it: a plan's limit with what lifts it, anything else in its words. */
export function Refused({ error, onDismiss }: { error: Error; onDismiss: () => void }) {
  if (error instanceof ApiError && error.status === 402) {
    return <PlanLimitNotice refusal={{ message: error.message, limit: error.limit }} onDismiss={onDismiss} />
  }
  return (
    <Notice tone="bad">
      <div className="flex items-start justify-between gap-3">
        <span>{error.message}</span>
        <IconButton label="Dismiss" onClick={onDismiss} className="p-0.5!">
          <X className="size-3.5" />
        </IconButton>
      </div>
    </Notice>
  )
}
