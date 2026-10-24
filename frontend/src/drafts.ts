// Drafts as the drafts' views word them (backend/app/drafts.py, backend/app/api/drafts.py): the kinds the model
// writes, a draft and its versions in a line, how far its writing is, whether the page should ask again, what a new
// draft still needs, the files a version downloads as, and what the view asks before deleting one.

import type { Document, DraftFormat, DraftInput, DraftKind, DraftSummary, DraftVersionSummary } from './api.ts'
import type { Question } from './components/Confirm.tsx'
import { formatDateTime } from './format.ts'

export const draftsKey = (projectId: number) => ['drafts', projectId] as const
export const draftKey = (draftId: number) => ['draft', draftId] as const
export const draftVersionKey = (draftId: number, number: number) => ['draft-version', draftId, number] as const

/** How often the page asks for a draft while the model writes it. */
export const DRAFT_POLL = 1500

/** Each kind, in the order the form offers them, with what it is. */
export const DRAFT_KINDS: { value: DraftKind; label: string; hint: string }[] = [
  { value: 'reply_email', label: 'Reply email', hint: 'An answer to an email the project holds, point by point.' },
  {
    value: 'dispute_letter',
    label: 'Dispute letter',
    hint: 'A letter to whoever issued a document, each figure disputed beside what it should be.',
  },
  { value: 'summary_memo', label: 'Summary memo', hint: 'The documents summed up for a colleague, and what to do next.' },
  { value: 'comparison_table', label: 'Comparison table', hint: 'The documents side by side, a column for each field that matters.' },
  { value: 'report', label: 'Report', hint: 'What the documents show, under headings, with conclusions.' },
]

export const KIND_LABELS = Object.fromEntries(DRAFT_KINDS.map((kind) => [kind.value, kind.label])) as Record<DraftKind, string>

/** What the form says a draft is and costs, beside the button that asks for it. */
export const DRAFT_NOTE =
  'The model writes it from the documents’ facts and passages, citing where each is written, and from what the newest run of the cross-checks found about them. It counts as one of the month’s questions.'

/** Whether the model has yet to settle the draft's writing, and the page should ask again. */
export const writing = (draft: Pick<DraftSummary, 'status'>) => draft.status === 'queued' || draft.status === 'writing'

/** How far the draft is, in a line: its writing under way or failed, or how many versions it has. */
export function draftStatusWords(draft: DraftSummary): string {
  if (draft.status === 'queued') return draft.versions ? 'Waiting to write it again…' : 'Waiting to write it…'
  if (draft.status === 'writing') return draft.versions ? 'Writing it again…' : 'Writing it…'
  if (draft.status === 'failed' && !draft.versions) return 'It could not be written.'
  const versions = `${draft.versions} version${draft.versions === 1 ? '' : 's'}`
  return draft.status === 'failed' ? `${versions} · The last writing failed.` : versions
}

/** Who asked for the draft and when: "Dispute letter · By Ada Lovelace · 24 Oct 2026, 10:41". */
export function draftByline(draft: DraftSummary): string {
  return [KIND_LABELS[draft.kind], draft.created_by ? `By ${draft.created_by}` : 'By a former member', formatDateTime(draft.created_at)].join(' · ')
}

/** A version in a line: "Version 2, saved by Ada Lovelace, 24 Oct 2026, 10:45", or the model's, written for whoever asked. */
export function versionWords(version: DraftVersionSummary): string {
  const who = version.by_model
    ? `written by the model${version.author ? ` for ${version.author}` : ''}`
    : `saved by ${version.author ?? 'a former member'}`
  return `Version ${version.number}, ${who}, ${formatDateTime(version.created_at)}`
}

/** What a new draft still needs before it is asked for: something to go by, its documents or the member's words. */
export function draftProblem(input: DraftInput): string | null {
  if (input.document_ids.length === 0 && !input.instructions.trim()) return 'Say what it should say, or choose the documents it is about.'
  return null
}

/** The dispute letter a disputed document's review asks for, about it alone. */
export function disputeLetter(document: Pick<Document, 'id'>): DraftInput {
  return { kind: 'dispute_letter', instructions: '', document_ids: [document.id] }
}

/** The files a version downloads as, in the order the menu offers them, and what each is for. */
export const DRAFT_FILES: { format: DraftFormat; label: string; words: string }[] = [
  { format: 'docx', label: 'Word document, to edit', words: 'a Word document' },
  { format: 'pdf', label: 'PDF, to send as it is', words: 'a PDF' },
  { format: 'md', label: 'Markdown, as it is written', words: 'Markdown' },
]

/** Where a version downloads as a file, its citations numbered notes naming the passages. */
export function draftFileUrl(draftId: number, number: number, format: DraftFormat): string {
  return `/api/drafts/${draftId}/versions/${number}/export?format=${format}`
}

/** What the view asks before deleting a draft, which takes every version with it. */
export function draftDeletionQuestion(draft: DraftSummary): Question {
  const versions = draft.versions === 1 ? 'its version' : draft.versions ? `its ${draft.versions} versions` : 'what was asked of it'
  return {
    title: 'Delete this draft?',
    body: `“${draft.title}” goes for good, with ${versions}. The documents it is about stay.`,
    action: 'Delete draft',
  }
}
