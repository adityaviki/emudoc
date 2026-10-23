import { describe, expect, it } from 'vitest'
import type { CrossCheck, ExtractionTemplate, Fact, Finding, TemplateField } from './api.ts'
import {
  checkSentence,
  comparisonsFor,
  countsWords,
  deletionQuestion,
  draftOf,
  draftProblem,
  findingWords,
  fitted,
  inputOf,
  partnersWords,
  suggestionInput,
  wordedDraft,
} from './crossChecks.ts'
import type { Worded } from './crossChecks.ts'

const field = (name: string, type: TemplateField['type']): TemplateField => ({ name, type, description: '', required: false, examples: [] })

const template = (doc_type: string, title: string, fields: TemplateField[]): ExtractionTemplate => ({
  id: null,
  doc_type,
  title,
  description: '',
  fields,
  built_in: true,
  replaces_built_in: false,
  documents: 0,
  matching: 0,
  reading: 0,
  changed: 0,
  updated_at: null,
})

const TEMPLATES = [
  template('invoice', 'Invoice', [
    field('invoice_number', 'text'),
    field('issue_date', 'date'),
    field('due_date', 'date'),
    field('po_number', 'text'),
    field('unit_price', 'money'),
    field('quantity', 'number'),
    field('payment_terms', 'text'),
  ]),
  template('price list', 'Price list', [field('vendor_name', 'text'), field('unit_price', 'money')]),
  template('delivery note', 'Delivery note', [field('delivery_note_number', 'text'), field('po_number', 'text'), field('delivered_item', 'number')]),
]

const PRICES: Worded = {
  title: 'Prices as on the price list',
  left_kind: 'invoice',
  left_field: 'unit_price',
  right_kind: 'price list',
  right_field: 'unit_price',
  pairing: 'every',
  left_key: null,
  right_key: null,
  by_item: true,
  comparison: 'equal',
  margin: 0,
  summed: false,
  left_type: 'money',
  right_type: 'money',
}

const DELIVERED: Worded = {
  ...PRICES,
  title: 'Quantities no more than delivered',
  left_field: 'quantity',
  right_kind: 'delivery note',
  right_field: 'delivered_item',
  pairing: 'key',
  left_key: 'po_number',
  right_key: 'po_number',
  comparison: 'at_most',
  summed: true,
  left_type: 'number',
  right_type: 'number',
}

const DUE: Worded = {
  ...PRICES,
  title: 'Due within 30 days of issue',
  left_field: 'due_date',
  right_kind: 'invoice',
  right_field: 'issue_date',
  pairing: 'same',
  by_item: false,
  comparison: 'at_most',
  margin: 30,
  left_type: 'date',
  right_type: 'date',
}

const fact = (value: string, document_title = 'inv-1.pdf'): Fact => ({
  id: 1,
  document_id: 1,
  document_title,
  name: 'unit_price',
  value_type: 'money',
  value,
  number: null,
  date: null,
  currency: null,
  quote: value,
  page: 1,
  block_ordinal: 0,
  passage_id: null,
  verified: true,
  check: null,
})

const finding = (changes: Partial<Finding>): Finding => ({
  document_id: 1,
  title: 'inv-1.pdf',
  filename: 'inv-1.pdf',
  outcome: 'differs',
  reason: 'differs',
  left: fact('Hex bolts: 190.00'),
  right: [fact('Hex bolts: 185.00', 'prices.csv')],
  partners: [{ document_id: 2, title: 'prices.csv' }],
  difference: 5,
  ...changes,
})

describe('a rule', () => {
  it('says what it compares in a sentence', () => {
    expect(checkSentence(PRICES)).toBe("Each invoice's unit price equals the unit price of every price list, line by line.")
    expect(checkSentence(DELIVERED)).toBe(
      "Each invoice's quantity is at most the delivered item of the delivery note documents with the same po number, added up, line by line.",
    )
    expect(checkSentence(DUE)).toBe("Each invoice's due date is at most 30 days after its issue date.")
    expect(checkSentence({ ...DUE, margin: 0, comparison: 'at_least' })).toBe("Each invoice's due date is no earlier than its issue date.")
    expect(checkSentence({ ...DELIVERED, right_key: 'order_reference', summed: false, by_item: false })).toBe(
      "Each invoice's quantity is at most the delivered item of the delivery note documents whose order reference is its po number.",
    )
    expect(checkSentence({ ...PRICES, margin: 0.5, by_item: false })).toBe("Each invoice's unit price is within 0.5 of the unit price of every price list.")
  })

  it('is compared by size for numbers and dates, and by its words for texts', () => {
    expect(comparisonsFor('money', 'number').map((option) => option.comparison)).toEqual(['equal', 'at_most', 'at_least'])
    expect(comparisonsFor('date', 'date')[1].label).toBe('is no later than')
    expect(comparisonsFor('text', 'text').map((option) => option.label)).toEqual(['says the same as', 'states the same numbers as'])
    expect(comparisonsFor('date', 'money')).toEqual([])
  })

  it('counts what it finds', () => {
    expect(countsWords({ differs: 1, missing: 2, holds: 0 })).toBe('1 differs · 2 miss a value · 0 hold')
    expect(countsWords({ differs: 0, missing: 0, holds: 0 })).toMatch(/^Nothing to check yet/)
  })

  it('asks before it is deleted', () => {
    expect(deletionQuestion({ ...PRICES, id: 3, problem: null, counts: null, updated_at: '' } as CrossCheck).title).toBe(
      'Delete the cross-check “Prices as on the price list”?',
    )
  })
})

describe('a finding', () => {
  it('says how far apart the two were, without repeating their values', () => {
    expect(findingWords(finding({}), PRICES)).toBe('5 more than prices.csv.')
    expect(findingWords(finding({ left: { ...fact('Hex bolts: 190.00'), currency: 'EUR' } }), PRICES)).toBe('5 EUR more than prices.csv.')
    expect(findingWords(finding({ difference: -2.5 }), { ...PRICES, margin: 1 })).toBe('2.5 less than prices.csv (1 allowed).')
    expect(findingWords(finding({ difference: 45 }), DUE)).toBe('45 days after its issue date (30 days allowed).')
    expect(findingWords(finding({ outcome: 'holds', reason: 'holds', difference: 0 }), PRICES)).toBe('Agrees with prices.csv.')
    const deliveries = [
      { document_id: 3, title: 'dn-1.pdf' },
      { document_id: 4, title: 'dn-2.pdf' },
    ]
    expect(findingWords(finding({ right: [fact('20'), fact('10')], partners: deliveries, difference: 5 }), DELIVERED)).toBe(
      '5 more than dn-1.pdf and dn-2.pdf, added up.',
    )
  })

  it('says what was missing for it to be made', () => {
    const missing = (reason: Finding['reason'], check: Worded = PRICES, changes: Partial<Finding> = {}) =>
      findingWords(finding({ outcome: 'missing', reason, right: [], difference: null, ...changes }), check)
    expect(missing('no_value')).toBe('States no unit price.')
    expect(missing('no_key', DELIVERED)).toBe('States no po number, which pairs it with a delivery note.')
    expect(missing('no_partner', DELIVERED, { partners: [] })).toBe('No delivery note in the project states its po number.')
    expect(missing('no_partner', PRICES, { partners: [] })).toBe('The project has no price list read for its template.')
    expect(missing('no_partner_value')).toBe('prices.csv states no unit price.')
    expect(missing('no_item')).toBe("No unit price of prices.csv names this line's item.")
    expect(missing('untyped', DUE)).toBe('A value has no date to compare: correct it in the facts table.')
    expect(findingWords(finding({ reason: 'currency', difference: null }), PRICES)).toMatch(/^In another currency than prices.csv/)
  })

  it('names the documents it was compared with, a few of them', () => {
    const titles = ['a.pdf', 'b.pdf', 'c.pdf', 'd.pdf'].map((title) => ({ title }))
    expect(partnersWords(titles.slice(0, 1))).toBe('a.pdf')
    expect(partnersWords(titles.slice(0, 2))).toBe('a.pdf and b.pdf')
    expect(partnersWords(titles)).toBe('a.pdf, b.pdf and 2 others')
  })
})

describe('the form', () => {
  it('starts a new rule from the first template, within the document, compared as its fields allow', () => {
    const draft = draftOf(null, TEMPLATES)
    expect([draft.left_kind, draft.left_field, draft.right_field, draft.pairing]).toEqual(['invoice', 'invoice_number', 'issue_date', 'same'])
    // A text and a date cannot be compared: the form says so before the server does.
    expect(draftProblem({ ...draft, title: 'x' }, TEMPLATES)).toBe('invoice number and issue date cannot be compared: choose two numbers or amounts, two dates or two texts.')
  })

  it('fits each change to the templates', () => {
    const draft = draftOf(null, TEMPLATES)
    const prices = fitted({ ...draft, left_field: 'unit_price', right_kind: 'price list' }, TEMPLATES)
    // Another kind cannot be the same document, and its field becomes one it has, which an amount is not compared with.
    expect([prices.pairing, prices.right_field]).toEqual(['every', 'vendor_name'])
    expect(draftProblem({ ...prices, title: 'x' }, TEMPLATES)).toMatch(/^unit price and vendor name cannot be compared/)
    const amounts = fitted({ ...prices, right_field: 'unit_price' }, TEMPLATES)
    expect(amounts.comparison).toBe('equal')
    const keyed = fitted({ ...amounts, right_kind: 'delivery note', pairing: 'key' }, TEMPLATES)
    expect([keyed.left_key, keyed.right_key]).toEqual(['invoice_number', 'delivery_note_number'])
    expect(fitted({ ...keyed, left_key: 'po_number', right_key: '' }, TEMPLATES).right_key).toBe('po_number')
    // A sum is for numbers, a margin for what has a size.
    expect(fitted({ ...amounts, summed: true, right_field: 'vendor_name' }, TEMPLATES)).toMatchObject({ summed: false, margin: '' })
    // A kind whose template is gone becomes the first there is.
    expect(fitted({ ...amounts, left_kind: 'quotation' }, TEMPLATES).left_kind).toBe('invoice')
  })

  it('sends the rule with its margin a number and keys only for a pairing by key', () => {
    const draft = draftOf(DELIVERED, TEMPLATES)
    expect(draft.margin).toBe('')
    expect(inputOf({ ...draft, title: '  Delivered  ', margin: '2' })).toEqual({ ...suggestionInput(DELIVERED), title: 'Delivered', margin: 2 })
    expect(inputOf({ ...draft, pairing: 'every' })).toMatchObject({ left_key: null, right_key: null })
    expect(wordedDraft(draft, TEMPLATES)).toMatchObject({ left_type: 'number', right_type: 'number' })
  })

  it('says what stops it from being saved', () => {
    const draft = draftOf(DUE, TEMPLATES)
    expect(draftProblem({ ...draft, title: ' ' }, TEMPLATES)).toBe('Give the cross-check a name.')
    expect(draftProblem({ ...draft, margin: '-1' }, TEMPLATES)).toBe('Give the margin as a number, 0 or more.')
    expect(draftProblem({ ...draft, margin: '1.5' }, TEMPLATES)).toBe('A margin between dates is a whole number of days.')
    expect(draftProblem({ ...draft, right_field: 'due_date' }, TEMPLATES)).toBe('Within the same document, compare the field with another one.')
    expect(draftProblem(draft, TEMPLATES)).toBeNull()
  })
})
