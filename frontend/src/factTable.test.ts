import { describe, expect, it } from 'vitest'
import type { Fact, FactCheck, TemplateField } from './api.ts'
import {
  BY_NAME,
  ariaSort,
  cellAfter,
  cellDescription,
  cellMark,
  cellWords,
  checkWords,
  conditionParam,
  conditionProblem,
  conditionWords,
  correctionOf,
  correctionProblem,
  draftOf,
  earlierWords,
  exportUrl,
  factTableKey,
  nextSort,
  operatorsFor,
  quoteWords,
  uncheckedWords,
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
  check: null,
  ...patch,
})

const checked = (patch: Partial<FactCheck> = {}): FactCheck => ({
  id: 9,
  checked_by: 'Ada',
  checked_at: '2026-10-23T09:00:00Z',
  corrected_from: null,
  value: 'EUR 1,200.00',
  ...patch,
})
// The day as the browser words it, which the tests do not hold to one locale.
const DAY = new Date('2026-10-23T09:00:00Z').toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })

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
        unchecked: true,
      },
      'abc',
    )
    expect(query.toString()).toBe(
      'kind=purchase+order&order=desc&sort=total_amount&filter=vendor_name%3Acontains%3Aacme&filter=due_date%3Alacks&q=bolts&unchecked=true&after=abc',
    )
    expect(tableQuery({ kind: 'invoice', sort: BY_NAME, conditions: [], words: ' ', unchecked: false }, null).toString()).toBe('kind=invoice&order=asc')
  })

  it('downloads every row the table asks for, from its first page, as the file chosen', () => {
    const ask = { kind: 'invoice', sort: { field: 'total_amount', order: 'desc' as const }, conditions: [{ field: 'iban', op: 'has' as const, value: '' }], words: 'acme', unchecked: true }
    expect(exportUrl(3, ask, 'xlsx')).toBe(
      '/api/projects/3/fact-table/export?kind=invoice&order=desc&sort=total_amount&filter=iban%3Ahas&q=acme&unchecked=true&format=xlsx',
    )
    expect(exportUrl(3, { ...ask, sort: BY_NAME, conditions: [], words: '', unchecked: false }, 'csv')).toBe(
      '/api/projects/3/fact-table/export?kind=invoice&order=asc&format=csv',
    )
  })

  it('keys each table by what it asks, so that a page of one never stands for another', () => {
    const ask = { kind: 'invoice', sort: BY_NAME, conditions: [], words: '', unchecked: false }
    expect(factTableKey(3, ask)).toEqual(['fact-table', 3, 'invoice', null, 'asc', [], '', false])
    expect(factTableKey(3, { ...ask, conditions: [{ field: 'iban', op: 'has', value: '' }] })).not.toEqual(factTableKey(3, ask))
    expect(factTableKey(3, { ...ask, unchecked: true })).not.toEqual(factTableKey(3, ask))
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
    expect(operatorsFor('text').map(({ op }) => op)).toEqual(['has', 'lacks', 'unchecked', 'contains'])
    expect(operatorsFor('money').map(({ label }) => label)).toEqual(['is stated', 'is not stated', 'is not checked', 'contains', 'is at least', 'is at most'])
    expect(operatorsFor('date').at(-1)?.label).toBe('is on or before')
  })

  it('words a condition as the list of them shows it', () => {
    expect(conditionWords({ field: 'total_amount', op: 'min', value: '1000' }, TOTAL)).toBe('total amount is at least 1000')
    expect(conditionWords({ field: 'due_date', op: 'max', value: '2026-03-01' }, DUE)).toBe('due date is on or before 2026-03-01')
    expect(conditionWords({ field: 'vendor_name', op: 'contains', value: 'acme ' }, VENDOR)).toBe('vendor name contains “acme”')
    expect(conditionWords({ field: 'vendor_name', op: 'lacks', value: '' }, VENDOR)).toBe('vendor name is not stated')
    expect(conditionParam({ field: 'vendor_name', op: 'has', value: 'x' })).toBe('vendor_name:has')
    expect(conditionWords({ field: 'line_item', op: 'unchecked', value: '' }, VENDOR)).toBe('line item is not checked')
    expect(conditionParam({ field: 'line_item', op: 'unchecked', value: 'x' })).toBe('line_item:unchecked')
  })

  it('refuses a condition the server would', () => {
    expect(conditionProblem({ field: 'iban', op: 'has', value: '' }, undefined)).toBe('Choose a field.')
    expect(conditionProblem({ field: 'vendor_name', op: 'has', value: '' }, VENDOR)).toBeNull()
    expect(conditionProblem({ field: 'total_amount', op: 'unchecked', value: '' }, TOTAL)).toBeNull()
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
    expect(cellDescription([fact()])).toBe('Not checked. Quoted as “Total due: EUR 1,200.00”, page 2. Enter offers to check, correct or open it.')
    expect(cellDescription([fact(), fact()])).toBe('2 values, 0 checked. Enter lists them, to check, correct or open each.')
    expect(cellDescription([fact({ check: checked() }), fact({ check: checked() })])).toBe('2 values, all checked. Enter lists them, to check, correct or open each.')
  })

  it('say who checked a fact, and what the model read in place of a correction', () => {
    expect(checkWords(checked())).toBe(`Checked by Ada on ${DAY}.`)
    expect(checkWords(checked({ checked_by: null, corrected_from: 'EUR 90.00' }))).toBe(`Corrected by a former member on ${DAY}; read as “EUR 90.00”.`)
    expect(cellDescription([fact({ check: checked() })])).toBe(`Checked by Ada on ${DAY}. Quoted as “Total due: EUR 1,200.00”, page 2. Enter offers to check, correct or open it.`)
    expect(earlierWords(checked({ corrected_from: 'EUR 90.00', value: '2,000.00' }))).toBe(
      `Ada wrote “2,000.00” on ${DAY}, before the document was read again, which reads otherwise now.`,
    )
    expect(cellDescription([], [checked()])).toBe('Not stated. A check made before the document was read again holds no value now. Enter shows the earlier check.')
  })

  it('mark a cell all checked, one to look at, or one read again since a check', () => {
    expect(cellMark([fact()])).toBeNull()
    expect(cellMark([fact({ check: checked() }), fact({ check: checked() })])).toBe('checked')
    expect(cellMark([fact({ check: checked() }), fact()])).toBeNull()
    // A quote not found in the text, which nobody has checked since.
    expect(cellMark([fact({ verified: false })])).toBe('flagged')
    expect(cellMark([fact({ verified: false, check: checked() })])).toBe('checked')
    expect(cellMark([fact({ check: checked() })], [checked()])).toBe('changed')
  })

  it('count the rows against the documents of the kind, and those with unchecked facts', () => {
    expect(rowsWords(1, 1, false)).toBe('1 document')
    expect(rowsWords(2, 3, true)).toBe('2 of 3 documents')
    expect(uncheckedWords(2)).toBe('2 with unchecked facts')
    expect(uncheckedWords(0)).toBe('every fact checked')
  })
})

describe('corrections', () => {
  it('start from the fact as its input takes it', () => {
    expect(draftOf(fact())).toEqual({ value: '1200', currency: 'EUR' })
    expect(draftOf(fact({ value_type: 'date', date: '2026-03-01', number: null }))).toEqual({ value: '2026-03-01', currency: '' })
    expect(draftOf(fact({ value_type: 'text', value: 'INV-7' }))).toEqual({ value: 'INV-7', currency: '' })
  })

  it('send the value as the type takes it, an amount with its currency', () => {
    expect(correctionOf('money', ' 1250.5 ', ' usd ')).toEqual({ value: '1250.5', currency: 'usd' })
    expect(correctionOf('money', '1250.5', '')).toEqual({ value: '1250.5' })
    expect(correctionOf('text', ' INV-7 ', 'EUR')).toEqual({ value: 'INV-7' })
  })

  it('refuse a value the server would', () => {
    expect(correctionProblem('text', '  ')).toBe('Write the value the document states.')
    expect(correctionProblem('money', '1,250')).toBe('Give a number.')
    expect(correctionProblem('number', '-3.5')).toBeNull()
    expect(correctionProblem('date', '1 March')).toBe('Choose a date.')
    expect(correctionProblem('date', '2026-03-01')).toBeNull()
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
