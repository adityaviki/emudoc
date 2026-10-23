import { describe, expect, it } from 'vitest'
import type { Fact, TemplateField } from './api.ts'
import {
  BY_NAME,
  ariaSort,
  cellAfter,
  cellDescription,
  cellWords,
  conditionParam,
  conditionProblem,
  conditionWords,
  factTableKey,
  nextSort,
  operatorsFor,
  quoteWords,
  rowsWords,
  sourceOf,
  tableQuery,
} from './factTable.ts'

const field = (name: string, type: TemplateField['type']): TemplateField => ({ name, type, description: '', required: false, examples: [] })
const TOTAL = field('total_amount', 'money')
const DUE = field('due_date', 'date')
const VENDOR = field('vendor_name', 'text')

const fact = (patch: Partial<Fact> = {}): Fact => ({
  id: 1,
  document_id: 4,
  document_title: 'acme-10.pdf',
  name: 'total_amount',
  value_type: 'money',
  value: 'EUR 1,200.00',
  number: 1200,
  date: null,
  currency: 'EUR',
  quote: 'Total due: EUR 1,200.00',
  page: 2,
  block_ordinal: 14,
  passage_id: 31,
  verified: true,
  ...patch,
})

describe('tableQuery', () => {
  it('asks for the kind, the sort, each condition, the words and the page', () => {
    const query = tableQuery(
      {
        kind: 'purchase order',
        sort: { field: 'total_amount', order: 'desc' },
        conditions: [
          { field: 'vendor_name', op: 'contains', value: ' acme ' },
          { field: 'due_date', op: 'lacks', value: 'ignored' },
        ],
        words: '  bolts ',
      },
      'abc',
    )
    expect(query.toString()).toBe(
      'kind=purchase+order&order=desc&sort=total_amount&filter=vendor_name%3Acontains%3Aacme&filter=due_date%3Alacks&q=bolts&after=abc',
    )
    expect(tableQuery({ kind: 'invoice', sort: BY_NAME, conditions: [], words: ' ' }, null).toString()).toBe('kind=invoice&order=asc')
  })

  it('keys each table by what it asks, so that a page of one never stands for another', () => {
    const ask = { kind: 'invoice', sort: BY_NAME, conditions: [], words: '' }
    expect(factTableKey(3, ask)).toEqual(['fact-table', 3, 'invoice', null, 'asc', [], ''])
    expect(factTableKey(3, { ...ask, conditions: [{ field: 'iban', op: 'has', value: '' }] })).not.toEqual(factTableKey(3, ask))
  })
})

describe('sorting', () => {
  it('turns a sorted column around and starts another lowest first', () => {
    expect(nextSort(BY_NAME, null)).toEqual({ field: null, order: 'desc' })
    expect(nextSort(BY_NAME, 'total_amount')).toEqual({ field: 'total_amount', order: 'asc' })
    expect(nextSort({ field: 'total_amount', order: 'asc' }, 'total_amount')).toEqual({ field: 'total_amount', order: 'desc' })
    expect(nextSort({ field: 'total_amount', order: 'desc' }, 'due_date')).toEqual({ field: 'due_date', order: 'asc' })
  })

  it('says which column is sorted and which way', () => {
    expect(ariaSort(BY_NAME, null)).toBe('ascending')
    expect(ariaSort({ field: 'due_date', order: 'desc' }, 'due_date')).toBe('descending')
    expect(ariaSort({ field: 'due_date', order: 'desc' }, null)).toBe('none')
  })
})

describe('conditions', () => {
  it('offers comparisons to numbers, amounts and dates alone', () => {
    expect(operatorsFor('text').map(({ op }) => op)).toEqual(['has', 'lacks', 'contains'])
    expect(operatorsFor('money').map(({ label }) => label)).toEqual(['is stated', 'is not stated', 'contains', 'is at least', 'is at most'])
    expect(operatorsFor('date').at(-1)?.label).toBe('is on or before')
  })

  it('words a condition as the list of them shows it', () => {
    expect(conditionWords({ field: 'total_amount', op: 'min', value: '1000' }, TOTAL)).toBe('total amount is at least 1000')
    expect(conditionWords({ field: 'due_date', op: 'max', value: '2026-03-01' }, DUE)).toBe('due date is on or before 2026-03-01')
    expect(conditionWords({ field: 'vendor_name', op: 'contains', value: 'acme ' }, VENDOR)).toBe('vendor name contains “acme”')
    expect(conditionWords({ field: 'vendor_name', op: 'lacks', value: '' }, VENDOR)).toBe('vendor name is not stated')
    expect(conditionParam({ field: 'vendor_name', op: 'has', value: 'x' })).toBe('vendor_name:has')
  })

  it('refuses a condition the server would', () => {
    expect(conditionProblem({ field: 'iban', op: 'has', value: '' }, undefined)).toBe('Choose a field.')
    expect(conditionProblem({ field: 'vendor_name', op: 'has', value: '' }, VENDOR)).toBeNull()
    expect(conditionProblem({ field: 'vendor_name', op: 'contains', value: ' ' }, VENDOR)).toBe('Give the words that vendor name should contain.')
    expect(conditionProblem({ field: 'total_amount', op: 'min', value: 'lots' }, TOTAL)).toBe('Give a number.')
    expect(conditionProblem({ field: 'total_amount', op: 'min', value: '' }, TOTAL)).toBe('Give a number.')
    expect(conditionProblem({ field: 'total_amount', op: 'max', value: '-1.5' }, TOTAL)).toBeNull()
    expect(conditionProblem({ field: 'due_date', op: 'min', value: '' }, DUE)).toBe('Choose a date.')
    expect(conditionProblem({ field: 'due_date', op: 'min', value: '2026-03-01' }, DUE)).toBeNull()
  })
})

describe('cells', () => {
  it('open a fact at its block, else its passage, else its page', () => {
    expect(sourceOf(fact())).toEqual({ block: 14 })
    expect(sourceOf(fact({ block_ordinal: null }))).toEqual({ passage: 31 })
    expect(sourceOf(fact({ block_ordinal: null, passage_id: null }))).toEqual({ page: 2 })
    expect(sourceOf(fact({ block_ordinal: null, passage_id: null, page: null }))).toBeNull()
  })

  it('show the first value and how many more', () => {
    expect(cellWords([fact(), fact({ id: 2, value: 'EUR 9.00' })])).toEqual({ value: 'EUR 1,200.00', more: 1 })
    expect(cellWords([])).toEqual({ value: '', more: 0 })
  })

  it('say where a fact was found, and when its quote was not', () => {
    expect(quoteWords(fact())).toBe('Quoted as “Total due: EUR 1,200.00”, page 2.')
    expect(quoteWords(fact({ verified: false, page: null }))).toBe('Quoted as “Total due: EUR 1,200.00”, which was not found in the text.')
    expect(quoteWords(fact({ quote: null }))).toBe('Read without a quote, page 2.')
    expect(cellDescription([fact()])).toBe('Quoted as “Total due: EUR 1,200.00”, page 2. Enter opens it in the document.')
    expect(cellDescription([fact(), fact()])).toBe('2 values. Enter lists them, each opening its place in the document.')
  })

  it('count the rows against the documents of the kind', () => {
    expect(rowsWords(1, 1, false)).toBe('1 document')
    expect(rowsWords(2, 3, true)).toBe('2 of 3 documents')
  })
})

describe('cellAfter', () => {
  it('moves by the arrows within the grid, and by Home and End along a row or to its corners', () => {
    expect(cellAfter([0, 0], 'ArrowUp', 4, 5)).toEqual([0, 0])
    expect(cellAfter([0, 0], 'ArrowDown', 4, 5)).toEqual([1, 0])
    expect(cellAfter([3, 4], 'ArrowRight', 4, 5)).toEqual([3, 4])
    expect(cellAfter([2, 3], 'ArrowLeft', 4, 5)).toEqual([2, 2])
    expect(cellAfter([2, 3], 'Home', 4, 5)).toEqual([2, 0])
    expect(cellAfter([2, 3], 'End', 4, 5)).toEqual([2, 4])
    expect(cellAfter([2, 3], 'Home', 4, 5, true)).toEqual([0, 0])
    expect(cellAfter([2, 3], 'End', 4, 5, true)).toEqual([3, 4])
    expect(cellAfter([2, 3], 'PageDown', 40, 5)).toEqual([12, 3])
    expect(cellAfter([2, 3], 'PageUp', 40, 5)).toEqual([0, 3])
    expect(cellAfter([2, 3], 'Enter', 4, 5)).toBeNull()
  })
})
