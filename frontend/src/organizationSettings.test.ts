import { describe, expect, it } from 'vitest'
import type { Languages, Member, Organization } from './api.ts'
import {
  chosenLanguage,
  deletes,
  heldByTwoFactor,
  keptChoices,
  keptWords,
  languageChoices,
  languageName,
  sessionDayChoices,
  signInWords,
} from './organizationSettings.ts'

const workspace = {
  chat_days: null,
  audit_days: 365,
  language: null,
} as unknown as Organization
const offered: Languages = { languages: ['english', 'german', 'portuguese', 'simple'], default: 'english' }

describe('languages', () => {
  it('names a configuration as a sentence says it', () => {
    expect(languageName('german')).toBe('German')
    expect(languageName('english_hunspell')).toBe('English hunspell')
    expect(languageName('simple')).toBe('Any language, words as written')
  })

  it('offers the deployment’s first as the choice of none', () => {
    expect(languageChoices(offered)).toEqual([
      { value: '', label: 'English, as Emudoc searches by default' },
      { value: 'german', label: 'German' },
      { value: 'portuguese', label: 'Portuguese' },
      { value: 'simple', label: 'Any language, words as written' },
    ])
  })

  it('takes naming the deployment’s, or one the database no longer has, for none', () => {
    expect(chosenLanguage(workspace, offered)).toBe('')
    expect(chosenLanguage({ ...workspace, language: 'german' }, offered)).toBe('german')
    expect(chosenLanguage({ ...workspace, language: 'english' }, offered)).toBe('')
    expect(chosenLanguage({ ...workspace, language: 'klingon' }, offered)).toBe('')
  })
})

describe('chats and audit log entries kept', () => {
  it('words a time', () => {
    expect(keptWords(null)).toBe('Until deleted')
    expect(keptWords(1)).toBe('1 day after the last question')
    expect(keptWords(1000)).toBe(`${(1000).toLocaleString()} days after the last question`)
    expect(keptWords(365, 'entries')).toBe('365 days')
  })

  it('offers until deleted and the times offered, with the organisation’s own among them', () => {
    expect(keptChoices(null, 'chats').map((choice) => choice.value)).toEqual(['', '7', '30', '90', '180', '365', '730'])
    expect(keptChoices(null, 'chats')[0].label).toBe('Until deleted')
    expect(keptChoices(45, 'chats').map((choice) => choice.value)).toEqual(['', '7', '30', '45', '90', '180', '365', '730'])
    expect(keptChoices(365, 'entries').map((choice) => choice.value)).toEqual(['', '30', '90', '180', '365', '730', '1825'])
  })

  it('says what a shorter time deletes, and nothing for one that keeps everything', () => {
    expect(deletes(null, 30, 'chats')).toBe(
      'The chats whose last question is more than 30 days old are deleted within six hours, for every member, and cannot be taken back.',
    )
    expect(deletes(365, 90, 'entries')).toBe('The audit log entries more than 90 days old are deleted within six hours, and cannot be taken back.')
    expect(deletes(30, 90, 'chats')).toBeNull()
    expect(deletes(30, null, 'chats')).toBeNull()
    expect(deletes(365, 365, 'entries')).toBeNull()
  })
})

describe('security', () => {
  const deployment = { ...workspace, max_session_days: 30, session_days: null } as Organization

  it('offers the sessions shorter than the deployment keeps one, with the organisation’s own among them', () => {
    expect(signInWords(1)).toBe('Every day')
    expect(signInWords(14)).toBe('Every 14 days')
    expect(sessionDayChoices(deployment).map((choice) => choice.value)).toEqual(['', '1', '7', '14'])
    expect(sessionDayChoices(deployment)[0].label).toBe('As often as Emudoc asks: every 30 days')
    expect(sessionDayChoices({ ...deployment, session_days: 3 }).map((choice) => choice.value)).toEqual(['', '1', '3', '7', '14'])
    expect(sessionDayChoices({ ...deployment, max_session_days: 90 }).map((choice) => choice.value)).toEqual(['', '1', '7', '14', '30', '60'])
  })

  it('names whom requiring two-factor would hold', () => {
    const member = (name: string, mfa_enabled: boolean | null): Member => ({
      user_id: name.length,
      name,
      email: `${name.toLowerCase()}@example.com`,
      role: 'member',
      joined_at: '2026-10-20T12:00:00Z',
      mfa_enabled,
    })
    expect(heldByTwoFactor([member('Ada', true)])).toBeNull()
    // A member the page was not told about is no member held.
    expect(heldByTwoFactor([member('Ada', true), member('Ann', null)])).toBeNull()
    expect(heldByTwoFactor([member('Ada', true), member('Bob', false)])).toBe(
      'One member signs in without it: Bob. They set it up before working in the workspace again.',
    )
    const many = ['Bob', 'Cat', 'Dan', 'Eve', 'Fay'].map((name) => member(name, false))
    expect(heldByTwoFactor(many)).toBe(
      '5 members sign in without it: Bob, Cat, Dan and 2 more. Each sets it up before working in the workspace again.',
    )
    expect(heldByTwoFactor([{ ...member('', false), email: 'zed@example.com' }])).toContain(': zed@example.com.')
  })
})
