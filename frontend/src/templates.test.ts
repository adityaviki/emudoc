import { describe, expect, it } from 'vitest'
import type { ExtractionTemplate } from './api.ts'
import { NEW_FIELD, deletionQuestion, draftOf, draftProblem, factName, inputOf, kindOf, originOf, templateWords } from './templates.ts'

const invoice = (changes: Partial<ExtractionTemplate> = {}): ExtractionTemplate => ({
  id: null,
  doc_type: 'invoice',
  title: 'Invoice',
  description: 'A bill.',
  fields: [
    { name: 'invoice_number', type: 'text', description: 'Its number.', required: true, examples: ['INV-1', 'INV-2'] },
    { name: 'total_amount', type: 'money', description: '', required: true, examples: [] },
    { name: 'iban', type: 'text', description: '', required: false, examples: [] },
  ],
  built_in: true,
  replaces_built_in: false,
  documents: 0,
  matching: 0,
  reading: 0,
  changed: 0,
  updated_at: null,
  ...changes,
})

describe('a field and a kind', () => {
  it('are named as the server names them', () => {
    expect(factName('Invoice Number')).toBe('invoice_number')
    expect(factName('  —VAT (%)— ')).toBe('vat')
    expect(factName('—')).toBe('')
    expect(kindOf(' Purchase_Order  ')).toBe('purchase order')
  })
})

describe('the form', () => {
  it('starts from a template as it is, its examples in one line', () => {
    const draft = draftOf(invoice())
    expect(draft.fields[0].examples).toBe('INV-1; INV-2')
    expect(inputOf(draft)).toEqual({
      doc_type: 'invoice',
      title: 'Invoice',
      description: 'A bill.',
      fields: invoice().fields,
    })
  })

  it('starts a new template with one empty field, and sends no field left empty', () => {
    const draft = draftOf(null)
    expect(draft.fields).toEqual([NEW_FIELD])
    draft.fields.push({ ...NEW_FIELD, name: ' Hours ', examples: '7.5;; 8 ;' })
    expect(inputOf({ ...draft, title: ' Timesheet ', doc_type: 'timesheet' }).fields).toEqual([
      { name: 'Hours', type: 'text', description: '', required: false, examples: ['7.5', '8'] },
    ])
  })

  it('says what keeps a draft from being saved', () => {
    const draft = draftOf(invoice())
    expect(draftProblem(draft)).toBeNull()
    expect(draftProblem({ ...draft, title: ' ' })).toBe('Give the template a name.')
    expect(draftProblem({ ...draft, doc_type: '__' })).toBe('Name the kind of document the template is for.')
    expect(draftProblem({ ...draft, fields: [{ ...NEW_FIELD }] })).toBe('Add a field for the template to read.')
    expect(draftProblem({ ...draft, fields: [{ ...NEW_FIELD, description: 'Its total.' }] })).toBe('Name each field.')
    expect(draftProblem({ ...draft, fields: [{ ...NEW_FIELD, name: '%' }] })).toBe('The field “%” needs a letter or a digit in its name.')
    const twice = [{ ...NEW_FIELD, name: 'Total' }, { ...NEW_FIELD, name: 'total ' }]
    expect(draftProblem({ ...draft, fields: twice })).toBe('Two fields are named total. Give each field a name of its own.')
  })
})

describe('a template', () => {
  it('says what it is for, how many fields it reads and the documents read for it', () => {
    expect(templateWords(invoice())).toBe('For invoice documents · 3 fields, 2 required')
    expect(templateWords(invoice({ documents: 1, fields: [invoice().fields[2]] }))).toBe('For invoice documents · 1 field · 1 document read for it')
    expect(templateWords(invoice({ documents: 4, reading: 2, changed: 1 }))).toBe(
      'For invoice documents · 3 fields, 2 required · 4 documents read for it · 2 being read · 1 changed when read again',
    )
    expect(originOf(invoice())).toBe('Built in')
    expect(originOf(invoice({ id: 4, built_in: false, replaces_built_in: true }))).toBe('Replaces the built-in one')
    expect(originOf(invoice({ id: 4, built_in: false, doc_type: 'timesheet' }))).toBeNull()
  })

  it('asks before it is deleted, saying what its documents are read for then', () => {
    const own = invoice({ id: 4, built_in: false, replaces_built_in: true, title: 'Supplier invoice' })
    expect(deletionQuestion(own).title).toBe('Delete the template “Supplier invoice”?')
    expect(deletionQuestion(own).body).toMatch(/^The project's invoice documents are read for the built-in invoice template once they are read again;/)
    expect(deletionQuestion({ ...own, replaces_built_in: false }).body).toMatch(/^The project's invoice documents are read without a template once/)
  })
})
