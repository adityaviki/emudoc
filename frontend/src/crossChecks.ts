// A project's cross-checks as the library's Cross-checks view words and writes them
// (backend/app/cross_checks.py, backend/app/api/cross_checks.py): what a rule says, the form's
// draft fitted to the project's templates and what it sends, what each finding says, and what the
// view asks before deleting a rule.

import type {
  CrossCheck,
  CrossCheckComparison,
  CrossCheckInput,
  CrossCheckPairing,
  ExtractionTemplate,
  FactType,
  Finding,
  FindingCounts,
  FindingOutcome,
} from './api.ts'
import type { Question } from './components/Confirm.tsx'
import { fieldWords } from './factTable.ts'

export const crossChecksKey = (projectId: number) => ['cross-checks', projectId] as const
export const findingsKey = (checkId: number, outcome: FindingOutcome | null) => ['findings', checkId, outcome] as const

/** A rule as its words need it: what it compares, and the types of its fields (null for one gone). */
export type Worded = CrossCheckInput & { left_type: FactType | null; right_type: FactType | null }

const ORDERED: CrossCheckComparison[] = ['equal', 'at_most', 'at_least']
const WORDED: CrossCheckComparison[] = ['same_words', 'same_numbers']
const numeric = (type: FactType | null) => type === 'number' || type === 'money'

/** What the form offers to compare two fields by, by their types: numbers, amounts and dates by size, texts by their words. */
export function comparisonsFor(left: FactType | null, right: FactType | null): { comparison: CrossCheckComparison; label: string }[] {
  const dates = left === 'date' && right === 'date'
  if (dates || (numeric(left) && numeric(right))) {
    return ORDERED.map((comparison) => ({ comparison, label: comparisonWords(comparison, dates, 0) }))
  }
  if (left === 'text' && right === 'text') {
    return WORDED.map((comparison) => ({ comparison, label: comparisonWords(comparison, false, 0) }))
  }
  return []
}

/** A comparison in the words of the rule's sentence: "is at most 30 days after", "equals". */
export function comparisonWords(comparison: CrossCheckComparison, dates: boolean, margin: number): string {
  const by = dates ? `${margin} day${margin === 1 ? '' : 's'}` : formatNumber(margin)
  if (comparison === 'same_words') return 'says the same as'
  if (comparison === 'same_numbers') return 'states the same numbers as'
  if (comparison === 'at_most') {
    if (!margin) return dates ? 'is no later than' : 'is at most'
    return dates ? `is at most ${by} after` : `is at most ${by} more than`
  }
  if (comparison === 'at_least') {
    if (!margin) return dates ? 'is no earlier than' : 'is at least'
    return dates ? `is at most ${by} before` : `is at most ${by} less than`
  }
  if (!margin) return dates ? 'is the same day as' : 'equals'
  return `is within ${by} of`
}

/** What a margin is counted in, as the form's field says it. */
export const marginUnit = (type: FactType | null) => (type === 'date' ? 'days' : type === 'money' ? 'the amounts’ currency' : 'the values’ units')

/** The rule in a sentence: "Each invoice's unit price equals the unit price of every price list, line by line." */
export function checkSentence(check: Worded): string {
  const dates = check.left_type === 'date'
  const left = `Each ${check.left_kind}'s ${fieldWords(check.left_field)}`
  const right = fieldWords(check.right_field)
  let other: string
  if (check.pairing === 'same') other = `its ${right}`
  else if (check.pairing === 'key') {
    const [own, theirs] = [fieldWords(check.left_key ?? ''), fieldWords(check.right_key ?? '')]
    const documents = `the ${right} of the ${check.right_kind} documents`
    other = own === theirs ? `${documents} with the same ${own}` : `${documents} whose ${theirs} is its ${own}`
  } else other = `the ${right} of every ${check.right_kind}`
  const ways = [check.summed && check.pairing !== 'same' ? 'added up' : '', check.by_item ? 'line by line' : ''].filter(Boolean)
  return `${left} ${comparisonWords(check.comparison, dates, check.margin)} ${other}${ways.length ? `, ${ways.join(', ')}` : ''}.`
}

const count = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`

/** What a rule finds, in a line: how many differ, miss a value and hold. */
export function countsWords(counts: FindingCounts): string {
  const total = counts.differs + counts.missing + counts.holds
  if (total === 0) return 'Nothing to check yet: no document of its kind has been read for its template.'
  return [
    count(counts.differs, 'differs', 'differ'),
    count(counts.missing, 'misses a value', 'miss a value'),
    count(counts.holds, 'holds', 'hold'),
  ].join(' · ')
}

/** Each outcome as the findings' filter and chips name it. */
export const OUTCOMES: Record<FindingOutcome, string> = { differs: 'Differs', missing: 'Missing', holds: 'Holds' }

/** The outcomes a list of findings is filtered by, all first. */
export const OUTCOME_CHOICES: { value: FindingOutcome | 'all'; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'differs', label: OUTCOMES.differs },
  { value: 'missing', label: OUTCOMES.missing },
  { value: 'holds', label: OUTCOMES.holds },
]

/** The documents a value was compared against, by name: one, two, or the first two and how many more. */
export function partnersWords(partners: { title: string }[]): string {
  const titles = partners.map((partner) => partner.title)
  if (titles.length <= 2) return titles.join(' and ')
  return `${titles.slice(0, 2).join(', ')} and ${count(titles.length - 2, 'other', 'others')}`
}

/** Why a finding is what it is, in a sentence, without repeating the values the finding shows beside it. */
export function findingWords(finding: Finding, check: Worded): string {
  const right = fieldWords(check.right_field)
  const partners = partnersWords(finding.partners)
  const theirs = check.pairing === 'same' ? `its ${right}` : `${partners}${check.summed && finding.right.length > 1 ? ', added up' : ''}`
  const allowed = check.margin ? ` (${check.left_type === 'date' ? count(check.margin, 'day', 'days') : formatNumber(check.margin)} allowed)` : ''
  switch (finding.reason) {
    case 'holds':
      return `Agrees with ${theirs}.`
    case 'differs': {
      if (check.comparison === 'same_words') return `Says otherwise than ${theirs}.`
      if (check.comparison === 'same_numbers') return `States other numbers than ${theirs}.`
      const difference = finding.difference ?? 0
      const size = Math.abs(difference)
      if (check.left_type === 'date') return `${count(size, 'day', 'days')} ${difference > 0 ? 'after' : 'before'} ${theirs}${allowed}.`
      const currency = check.left_type === 'money' && finding.left?.currency ? ` ${finding.left.currency}` : ''
      return `${formatNumber(size)}${currency} ${difference > 0 ? 'more' : 'less'} than ${theirs}${allowed}.`
    }
    case 'currency':
      return `In another currency than ${theirs}: amounts in two currencies are not compared.`
    case 'no_value':
      return `States no ${fieldWords(check.left_field)}.`
    case 'no_key':
      return `States no ${fieldWords(check.left_key ?? '')}, which pairs it with a ${check.right_kind}.`
    case 'no_partner':
      return check.pairing === 'key'
        ? `No ${check.right_kind} in the project states its ${fieldWords(check.left_key ?? '')}.`
        : `The project has no ${check.right_kind} read for its template.`
    case 'no_partner_value':
      return check.pairing === 'same' ? `States no ${right}.` : `${partners} ${finding.partners.length === 1 ? 'states' : 'state'} no ${right}.`
    case 'no_item':
      return `No ${right} of ${partners} names this line's item.`
    case 'untyped':
      return `A value has no ${check.left_type === 'date' ? 'date' : 'number'} to compare: correct it in the facts table.`
  }
}

/** A number as a finding says it: grouped, at most two decimals. */
export function formatNumber(value: number): string {
  return value.toLocaleString('en', { maximumFractionDigits: 2 })
}

/** A rule as the form holds it, its margin as typed. */
export interface CheckDraft {
  title: string
  left_kind: string
  left_field: string
  right_kind: string
  right_field: string
  pairing: CrossCheckPairing
  left_key: string
  right_key: string
  by_item: boolean
  comparison: CrossCheckComparison
  margin: string
  summed: boolean
}

/** A field's type as the project's templates hold it, or null. */
export function fieldType(templates: ExtractionTemplate[], kind: string, name: string): FactType | null {
  return templates.find((template) => template.doc_type === kind)?.fields.find((field) => field.name === name)?.type ?? null
}

/** What the form starts from: a rule as it is, or for a new one the first template's first two fields compared within the document, fitted to the templates. */
export function draftOf(check: CrossCheckInput | null, templates: ExtractionTemplate[]): CheckDraft {
  if (check) {
    const { title, left_kind, left_field, right_kind, right_field, pairing, by_item, comparison, summed } = check
    const keys = { left_key: check.left_key ?? '', right_key: check.right_key ?? '' }
    const draft = { title, left_kind, left_field, right_kind, right_field, pairing, by_item, comparison, summed, ...keys }
    return fitted({ ...draft, margin: check.margin ? String(check.margin) : '' }, templates)
  }
  const first = templates[0]
  const kind = first?.doc_type ?? ''
  return fitted(
    {
      title: '',
      left_kind: kind,
      left_field: first?.fields[0]?.name ?? '',
      right_kind: kind,
      right_field: first?.fields[1]?.name ?? first?.fields[0]?.name ?? '',
      pairing: 'same',
      left_key: '',
      right_key: '',
      by_item: false,
      comparison: 'equal',
      margin: '',
      summed: false,
    },
    templates,
  )
}

/** The draft made whole after a change: each field one its kind's template has, a comparison the two fields' types allow, keys for a pairing by key, the same document only for one kind, and no sum or margin where they mean nothing. */
export function fitted(draft: CheckDraft, templates: ExtractionTemplate[]): CheckDraft {
  const fields = (kind: string) => templates.find((template) => template.doc_type === kind)?.fields ?? []
  const has = (kind: string, name: string) => fields(kind).some((field) => field.name === name)
  const next = { ...draft }
  // A kind no template is for any more, as of a rule made before its template went, becomes the first kind there is.
  for (const side of ['left_kind', 'right_kind'] as const) {
    if (!templates.some((template) => template.doc_type === next[side])) next[side] = templates[0]?.doc_type ?? ''
  }
  if (!has(next.left_kind, next.left_field)) next.left_field = fields(next.left_kind)[0]?.name ?? ''
  if (!has(next.right_kind, next.right_field)) next.right_field = fields(next.right_kind)[0]?.name ?? ''
  if (next.pairing === 'same' && next.right_kind !== next.left_kind) next.pairing = 'every'
  if (next.pairing === 'key') {
    if (!has(next.left_kind, next.left_key)) next.left_key = keyOf(fields(next.left_kind))
    if (!has(next.right_kind, next.right_key)) next.right_key = has(next.right_kind, next.left_key) ? next.left_key : keyOf(fields(next.right_kind))
  }
  const left = fieldType(templates, next.left_kind, next.left_field)
  const right = fieldType(templates, next.right_kind, next.right_field)
  const offered = comparisonsFor(left, right).map((option) => option.comparison)
  if (!offered.includes(next.comparison) && offered.length) next.comparison = offered[0]
  if (!(numeric(left) && numeric(right))) next.summed = false
  if (WORDED.includes(next.comparison)) next.margin = ''
  return next
}

/** The field a pairing by key goes by unless chosen: the first text field whose name says it is a number or a reference. */
function keyOf(fields: { name: string; type: FactType }[]): string {
  const texts = fields.filter((field) => field.type === 'text')
  return (texts.find((field) => /number|reference|_id$|^id$/.test(field.name)) ?? texts[0] ?? fields[0])?.name ?? ''
}

/** What the form sends: the name trimmed, the margin a number, keys only for a pairing by key. */
export function inputOf(draft: CheckDraft): CrossCheckInput {
  const keyed = draft.pairing === 'key'
  return {
    title: draft.title.trim(),
    left_kind: draft.left_kind,
    left_field: draft.left_field,
    right_kind: draft.right_kind,
    right_field: draft.right_field,
    pairing: draft.pairing,
    left_key: keyed ? draft.left_key : null,
    right_key: keyed ? draft.right_key : null,
    by_item: draft.by_item,
    comparison: draft.comparison,
    margin: draft.margin.trim() ? Number(draft.margin) : 0,
    summed: draft.summed,
  }
}

/** What stops the draft from being saved as it stands, in the form's words; null when nothing does. The server checks the same and more. */
export function draftProblem(draft: CheckDraft, templates: ExtractionTemplate[]): string | null {
  const input = inputOf(draft)
  if (!input.title) return 'Give the cross-check a name.'
  if (!input.left_field || !input.right_field) return 'Choose the fields to compare.'
  const left = fieldType(templates, input.left_kind, input.left_field)
  const right = fieldType(templates, input.right_kind, input.right_field)
  if (comparisonsFor(left, right).length === 0) {
    return `${fieldWords(input.left_field)} and ${fieldWords(input.right_field)} cannot be compared: choose two numbers or amounts, two dates or two texts.`
  }
  if (input.pairing === 'same' && input.left_field === input.right_field) return 'Within the same document, compare the field with another one.'
  if (input.pairing === 'key' && (!input.left_key || !input.right_key)) return 'Choose the field of each side whose values pair the documents.'
  if (!Number.isFinite(input.margin) || input.margin < 0) return 'Give the margin as a number, 0 or more.'
  if (left === 'date' && !Number.isInteger(input.margin)) return 'A margin between dates is a whole number of days.'
  return null
}

/** The rule as its sentence needs it, from the draft and the templates. */
export function wordedDraft(draft: CheckDraft, templates: ExtractionTemplate[]): Worded {
  const input = inputOf(draft)
  return {
    ...input,
    margin: Number.isFinite(input.margin) ? input.margin : 0,
    left_type: fieldType(templates, input.left_kind, input.left_field),
    right_type: fieldType(templates, input.right_kind, input.right_field),
  }
}

/** A suggested rule as the form sends it, without the types it came with. */
export function suggestionInput({ left_type: _left, right_type: _right, ...input }: Worded): CrossCheckInput {
  return input
}

/** What deleting a rule asks. */
export function deletionQuestion(check: CrossCheck): Question {
  return {
    title: `Delete the cross-check “${check.title}”?`,
    body: 'What it finds is worked out from the facts whenever it is shown, so nothing else goes with it. This cannot be undone.',
    action: 'Delete it',
  }
}
