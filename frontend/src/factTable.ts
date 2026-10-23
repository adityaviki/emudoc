// The facts table as the library's facts view asks for it and words it (backend/app/fact_table.py,
// `GET /api/projects/{id}/fact-table`): a project's documents of one kind side by side, a column
// for each field of the kind's template, sorted by one of them and kept by conditions on them.

import type { Fact, FactType, TemplateField } from './api.ts'
import type { Focus } from './workspace/url.ts'

/** Every facts table of the project, whatever its kind, sort or conditions: what a change to its templates or documents makes stale. */
export const factTablesKey = (projectId: number) => ['fact-table', projectId] as const

/** What the table is sorted by: a field, or the document's name (null); the lowest first, or the highest. */
export interface TableSort {
  field: string | null
  order: 'asc' | 'desc'
}

export const BY_NAME: TableSort = { field: null, order: 'asc' }

/** What a condition asks of a row's field: any fact at all, none, one whose words hold the value, or one at least or at most the value. */
export type Operator = 'has' | 'lacks' | 'contains' | 'min' | 'max'

export interface TableCondition {
  field: string
  op: Operator
  /** Empty for `has` and `lacks`; a number, or a date as 2026-03-01, for `min` and `max`. */
  value: string
}

/** What the table is asked for: its kind, its sort, its conditions and the words its rows hold. */
export interface TableAsk {
  kind: string
  sort: TableSort
  conditions: TableCondition[]
  words: string
}

export const factTableKey = (projectId: number, ask: TableAsk) =>
  [...factTablesKey(projectId), ask.kind, ask.sort.field, ask.sort.order, ask.conditions.map(conditionParam), ask.words.trim()] as const

/** The query of a page of the table, past `after` when it is given. */
export function tableQuery(ask: TableAsk, after: string | null): URLSearchParams {
  const params = new URLSearchParams({ kind: ask.kind, order: ask.sort.order })
  if (ask.sort.field) params.set('sort', ask.sort.field)
  for (const condition of ask.conditions) params.append('filter', conditionParam(condition))
  if (ask.words.trim()) params.set('q', ask.words.trim())
  if (after) params.set('after', after)
  return params
}

/** A condition as the server reads it: `field:op`, or `field:op:value`. */
export function conditionParam({ field, op, value }: TableCondition): string {
  return op === 'has' || op === 'lacks' ? `${field}:${op}` : `${field}:${op}:${value.trim()}`
}

/** A sort after the column's header was pressed: a column sorted already turns around, another starts lowest first. */
export function nextSort(sort: TableSort, field: string | null): TableSort {
  if (sort.field === field) return { field, order: sort.order === 'asc' ? 'desc' : 'asc' }
  return { field, order: 'asc' }
}

/** What a column header's `aria-sort` says. */
export function ariaSort(sort: TableSort, field: string | null): 'ascending' | 'descending' | 'none' {
  if (sort.field !== field) return 'none'
  return sort.order === 'asc' ? 'ascending' : 'descending'
}

/** A field's name as a column says it: its words, as a kind of document is written. */
export const fieldWords = (name: string) => name.replaceAll('_', ' ')

const COMPARED: FactType[] = ['number', 'money', 'date']

/** The conditions a field of the type takes, as the form offers them. */
export function operatorsFor(type: FactType): { op: Operator; label: string }[] {
  const dated = type === 'date'
  return [
    { op: 'has', label: 'is stated' },
    { op: 'lacks', label: 'is not stated' },
    { op: 'contains', label: 'contains' },
    ...(COMPARED.includes(type)
      ? [
          { op: 'min' as const, label: dated ? 'is on or after' : 'is at least' },
          { op: 'max' as const, label: dated ? 'is on or before' : 'is at most' },
        ]
      : []),
  ]
}

/** A condition in words, for the list of those the table holds: "total amount is at least 1000". */
export function conditionWords(condition: TableCondition, field: TemplateField | undefined): string {
  const label = operatorsFor(field?.type ?? 'text').find(({ op }) => op === condition.op)?.label ?? condition.op
  const value = condition.op === 'contains' ? ` “${condition.value.trim()}”` : condition.op === 'min' || condition.op === 'max' ? ` ${condition.value.trim()}` : ''
  return `${fieldWords(condition.field)} ${label}${value}`
}

/** What stops the condition from being added as it stands, in the form's words; null when nothing does. The server checks the same. */
export function conditionProblem(condition: TableCondition, field: TemplateField | undefined): string | null {
  if (!field) return 'Choose a field.'
  const value = condition.value.trim()
  if (condition.op === 'has' || condition.op === 'lacks') return null
  if (condition.op === 'contains') return value ? null : `Give the words that ${fieldWords(field.name)} should contain.`
  if (field.type === 'date') return /^\d{4}-\d{2}-\d{2}$/.test(value) ? null : 'Choose a date.'
  return value !== '' && Number.isFinite(Number(value)) ? null : 'Give a number.'
}

/** Where a fact opens its document: at the block its quote is in, else its passage, else its page; null when nothing says where it was found. */
export function sourceOf(fact: Fact): Focus | null {
  if (fact.block_ordinal != null) return { block: fact.block_ordinal }
  if (fact.passage_id != null) return { passage: fact.passage_id }
  if (fact.page != null) return { page: fact.page }
  return null
}

/** What a cell shows: its first fact's value, and how many more it holds. */
export function cellWords(facts: Fact[]): { value: string; more: number } {
  return { value: facts[0]?.value ?? '', more: Math.max(facts.length - 1, 0) }
}

/** Where a fact was found, in a few words: its quote and page, or that its quote is not in the text. */
export function quoteWords(fact: Fact): string {
  const page = fact.page != null ? `, page ${fact.page}` : ''
  if (!fact.quote) return `Read without a quote${page}.`
  if (!fact.verified) return `Quoted as “${fact.quote}”, which was not found in the text${page}.`
  return `Quoted as “${fact.quote}”${page}.`
}

/** What the screen reader hears of a cell besides its value: each fact's quote, and how to open it. */
export function cellDescription(facts: Fact[]): string {
  if (facts.length === 1) return `${quoteWords(facts[0])} Enter opens it in the document.`
  return `${facts.length} values. Enter lists them, each opening its place in the document.`
}

/** How many of the kind's documents the table shows, once its conditions keep some. */
export function rowsWords(total: number, documents: number, filtered: boolean): string {
  const noun = (n: number) => `${n} document${n === 1 ? '' : 's'}`
  return filtered ? `${total} of ${noun(documents)}` : noun(total)
}

/** The cell the keyboard moves to from `at` by `key` in a grid of `rows` rows and `columns` columns (the header's row and the name's column included); null for a key the grid does not take. */
export function cellAfter(at: [number, number], key: string, rows: number, columns: number, ctrl = false): [number, number] | null {
  const [row, column] = at
  const clamp = (value: number, size: number) => Math.min(Math.max(value, 0), size - 1)
  switch (key) {
    case 'ArrowUp':
      return [clamp(row - 1, rows), column]
    case 'ArrowDown':
      return [clamp(row + 1, rows), column]
    case 'ArrowLeft':
      return [row, clamp(column - 1, columns)]
    case 'ArrowRight':
      return [row, clamp(column + 1, columns)]
    case 'Home':
      return ctrl ? [0, 0] : [row, 0]
    case 'End':
      return ctrl ? [rows - 1, columns - 1] : [row, columns - 1]
    case 'PageUp':
      return [clamp(row - 10, rows), column]
    case 'PageDown':
      return [clamp(row + 10, rows), column]
    default:
      return null
  }
}
