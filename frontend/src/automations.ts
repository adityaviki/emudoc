// A project's automations as the Cross-checks view words and writes them
// (backend/app/automations.py, backend/app/api/automations.py): what an automation says, how it
// stands, the form's draft and what it sends, the folders it may watch, and what the view asks
// before deleting one.

import type { Automation, AutomationAudience, AutomationInput, Folder } from './api.ts'
import type { Question } from './components/Confirm.tsx'
import { formatDateTime } from './format.ts'

export const automationsKey = (projectId: number) => ['automations', projectId] as const

/** Whom an automation may tell, as the form offers it. */
export const AUDIENCES: { value: AutomationAudience; label: string }[] = [
  { value: 'maker', label: 'Whoever made it' },
  { value: 'project', label: 'Everyone who sees the project' },
  { value: 'nobody', label: 'Nobody: the run is listed with the others' },
]

/** An automation as its words need it: what it is, with its folder's path and its chosen cross-checks' names. */
export type WordedAutomation = AutomationInput & { folder: string | null; checks: string[] | null }

/** "an invoice", "a purchase order". */
const withArticle = (kind: string) => `${/^[aeiou]/i.test(kind) ? 'an' : 'a'} ${kind}`

/** The names of a few things in a sentence: "a", "a and b", "a, b and c". */
export function listWords(names: string[]): string {
  if (names.length <= 1) return names.join('')
  return `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`
}

/**
 * What an automation does, in a sentence: "When an invoice arrives in Inbox, run every
 * cross-check with a discrepancy report by the model, and tell Ada Lovelace." `maker` names whom
 * "whoever made it" is, as the reader knows them.
 */
export function automationSentence(automation: WordedAutomation, maker: string): string {
  const what = automation.kind ? withArticle(automation.kind) : 'a document'
  const where = automation.folder ? `in ${automation.folder}` : 'anywhere in the project'
  const checks = automation.checks
  const run =
    checks === null
      ? 'every cross-check'
      : checks.length === 0
        ? 'no cross-check'
        : checks.length <= 2
          ? listWords(checks.map((name) => `“${name}”`))
          : `${checks.length} cross-checks`
  const told = automation.audience === 'maker' ? maker : automation.audience === 'project' ? 'everyone who sees the project' : 'nobody'
  const again = automation.recheck ? ' Run them again when a document they compare it with arrives anywhere in the project.' : ''
  return `When ${what} arrives ${where}, run ${run}${automation.report ? ' with a discrepancy report by the model' : ''}, and tell ${told}.${again}`
}

/** How the automation stands: off, never run, or when it last ran. */
export function automationStatus(automation: Automation): string {
  if (!automation.enabled) return 'Off: nothing that arrives runs it.'
  return automation.last_run_at ? `On · Last ran ${formatDateTime(automation.last_run_at)}` : 'On · Not run yet'
}

/** The form's draft: a kind ('' for any document), a folder (null for the whole project), every cross-check or those chosen. */
export interface AutomationDraft {
  title: string
  kind: string
  folder_id: number | null
  every: boolean
  check_ids: number[]
  report: boolean
  audience: AutomationAudience
  recheck: boolean
  enabled: boolean
}

/** The draft of an automation being changed, or of a new one: every invoice that arrives, every cross-check, its maker told. */
export function draftOf(automation: Automation | null, kinds: string[]): AutomationDraft {
  if (automation) {
    return {
      title: automation.title,
      kind: automation.kind ?? '',
      folder_id: automation.folder_id,
      every: automation.check_ids === null,
      check_ids: automation.check_ids ?? [],
      report: automation.report,
      audience: automation.audience,
      recheck: automation.recheck,
      enabled: automation.enabled,
    }
  }
  return {
    title: '',
    kind: kinds.includes('invoice') ? 'invoice' : '',
    folder_id: null,
    every: true,
    check_ids: [],
    report: false,
    audience: 'maker',
    recheck: false,
    enabled: true,
  }
}

/** What the form sends for its draft. */
export function inputOf(draft: AutomationDraft): AutomationInput {
  return {
    title: draft.title.trim(),
    kind: draft.kind || null,
    folder_id: draft.folder_id,
    check_ids: draft.every ? null : draft.check_ids,
    report: draft.report,
    audience: draft.audience,
    recheck: draft.recheck,
    enabled: draft.enabled,
  }
}

/** What the form says before it sends a draft the server would refuse; null when it would not. */
export function draftProblem(draft: AutomationDraft): string | null {
  if (!draft.title.trim()) return 'Name the automation.'
  if (!draft.every && draft.check_ids.length === 0) return 'Choose a cross-check for it to run, or run every one.'
  return null
}

/** The draft in the words of its sentence, as the form shows it while it is written. */
export function wordedDraft(draft: AutomationDraft, folders: FolderChoice[], names: Map<number, string>): WordedAutomation {
  return {
    ...inputOf(draft),
    folder: folders.find((folder) => folder.id === draft.folder_id)?.label ?? null,
    checks: draft.every ? null : draft.check_ids.flatMap((id) => names.get(id) ?? []),
  }
}

/** A folder an automation may watch, named by its path from the project's top, as the server names it. */
export interface FolderChoice {
  id: number
  label: string
}

/** The project's folders in the library's order, each by its path: "Inbox", "Inbox/Invoices". */
export function folderChoices(folders: Folder[]): FolderChoice[] {
  const children = new Map<number | null, Folder[]>()
  for (const folder of folders) children.set(folder.parent_id, [...(children.get(folder.parent_id) ?? []), folder])
  const found: FolderChoice[] = []
  const visit = (parentId: number | null, path: string) => {
    const sorted = [...(children.get(parentId) ?? [])].sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: 'base' }))
    for (const folder of sorted) {
      const label = path ? `${path}/${folder.name}` : folder.name
      found.push({ id: folder.id, label })
      visit(folder.id, label)
    }
  }
  visit(null, '')
  return found
}

/** What the view asks before deleting an automation, whose runs stay. */
export function automationDeletionQuestion(automation: Automation): Question {
  return {
    title: 'Delete this automation?',
    body: `“${automation.title}” goes for good: nothing that arrives runs it any more. The runs it made stay, with what they found.`,
    action: 'Delete automation',
  }
}
