import { describe, expect, it } from 'vitest'
import type { DraftTemplate } from './api.ts'
import { builtInAgainQuestion, originWords, sectionsWords, templateFormOf, templateInputOf, templateProblem, writtenToWords } from './draftTemplates.ts'

const letter: DraftTemplate = {
  kind: 'dispute_letter',
  id: null,
  built_in: true,
  tone: 'Firm and factual, never accusing.',
  sections: [
    { heading: 'Date', description: 'The date of the letter.', required: true },
    { heading: 'Our reference', description: '', required: false },
  ],
  guidance: '',
  updated_at: null,
}

describe('draft templates', () => {
  it('says what a template holds and where it comes from', () => {
    expect(originWords(letter)).toBe('Built in')
    expect(originWords({ built_in: false })).toBe('The project’s own')
    expect(sectionsWords(letter)).toBe('Date · Our reference (where it applies)')
    expect(sectionsWords({ sections: [] })).toBe('No parts of its own: the kind’s layout alone.')
  })

  it('starts the form from the template and sends it trimmed, an empty part left out', () => {
    const form = templateFormOf(letter)
    form.sections[0].heading = 'Changed'
    expect(letter.sections[0].heading).toBe('Date')
    expect(
      templateInputOf({
        tone: '  Formal. ',
        sections: [{ heading: ' Reference ', description: ' Ours, from the order. ', required: false }, { heading: ' ', description: '', required: true }],
        guidance: ' Sign as Accounts Payable. ',
      }),
    ).toEqual({
      tone: 'Formal.',
      sections: [{ heading: 'Reference', description: 'Ours, from the order.', required: false }],
      guidance: 'Sign as Accounts Payable.',
    })
  })

  it('asks for a name of its own for each part', () => {
    expect(templateProblem(templateFormOf(letter))).toBeNull()
    expect(templateProblem({ ...letter, sections: [...letter.sections, { heading: '', description: 'Who to call.', required: true }] })).toBe(
      'Name part 3, or remove it.',
    )
    expect(templateProblem({ ...letter, sections: [...letter.sections, { heading: ' date ', description: '', required: true }] })).toBe(
      'Two parts are named “date”. Give each part a name of its own.',
    )
  })

  it('says what giving a kind its built-in template back does, and when a new draft follows the project’s own', () => {
    expect(builtInAgainQuestion('dispute_letter')).toEqual({
      title: 'Use the built-in dispute letter template again?',
      body: 'The project’s own dispute letter template goes for good, and the next dispute letter is written to the built-in one. The drafts written already stay as they are.',
      action: 'Use the built-in one',
    })
    expect(writtenToWords(letter)).toBeNull()
    expect(writtenToWords(undefined)).toBeNull()
    expect(writtenToWords({ ...letter, id: 3, built_in: false })).toBe('Written to the project’s own template for this kind.')
  })
})
