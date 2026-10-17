import { describe, expect, it } from 'vitest'
import { formatDate, parsePipeTable } from './format.ts'

describe('parsePipeTable', () => {
  it('reads the rows of a table and drops the separator under the header', () => {
    const table = ['| Item | Price |', '| :--- | ---: |', '| Pallet | 12.50 |', '| Crate | 4 |'].join('\n')
    expect(parsePipeTable(table)).toEqual([
      ['Item', 'Price'],
      ['Pallet', '12.50'],
      ['Crate', '4'],
    ])
  })

  it('keeps an escaped pipe inside its cell and empty cells in place', () => {
    const table = '| Clause | Text |\n|---|---|\n| 4.2 | either a \\| or b |\n|  | blank |'
    expect(parsePipeTable(table)).toEqual([
      ['Clause', 'Text'],
      ['4.2', 'either a | or b'],
      ['', 'blank'],
    ])
  })

  it('takes blank lines and the indentation around a table', () => {
    expect(parsePipeTable('\n  | a | b |\n  |---|---|\n\n  | 1 | 2 |\n')).toEqual([
      ['a', 'b'],
      ['1', '2'],
    ])
  })

  it('says null for text that is not a table', () => {
    expect(parsePipeTable('')).toBeNull()
    expect(parsePipeTable('| a | b |')).toBeNull()
    expect(parsePipeTable('| a | b |\nThe total is 4.')).toBeNull()
    expect(parsePipeTable('Prices:\n| a | b |\n|---|---|')).toBeNull()
    expect(parsePipeTable('|---|---|\n| :-: | --- |')).toBeNull()
  })
})

describe('formatDate', () => {
  it('shows nothing for a date that is not there', () => {
    expect(formatDate(null)).toBe('')
    expect(formatDate('')).toBe('')
  })

  it('names the year of a date it is given', () => {
    expect(formatDate('2026-10-17T12:00:00Z')).toContain('2026')
  })
})
