import { describe, expect, it } from 'vitest'
import type { Languages, Member, Organization } from './api.ts'
import {
  chatDayChoices,
  chosenLanguage,
  deletes,
  heldByTwoFactor,
  keptWords,
  languageChoices,
  languageName,
  sessionDayChoices,
  signInWords,
} from './organizationSettings.ts'

const pro = {
  allowance: { retention_days: 365 },
  chat_retention_days: 365,
  chat_days: null,
  retention_held: null,
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
    expect(chosenLanguage(pro, offered)).toBe('')
    expect(chosenLanguage({ ...pro, language: 'german' }, offered)).toBe('german')
    expect(chosenLanguage({ ...pro, language: 'english' }, offered)).toBe('')
    expect(chosenLanguage({ ...pro, language: 'klingon' }, offered)).toBe('')
  })
})

describe('chats kept', () => {
  it('words a time', () => {
    expect(keptWords(null)).toBe('Until deleted')
    expect(keptWords(1)).toBe('1 day after the last question')
    expect(keptWords(1000)).toBe(`${(1000).toLocaleString()} days after the last question`)
  })

  it('offers the times shorter than the plan’s, with the organisation’s own among them', () => {
    expect(chatDayChoices(pro).map((choice) => choice.value)).toEqual(['', '7', '30', '90', '180'])
    expect(chatDayChoices(pro)[0].label).toBe('As long as the plan keeps them: 365 days after the last question')
    const chosen = { ...pro, chat_days: 45, chat_retention_days: 45 }
    expect(chatDayChoices(chosen).map((choice) => choice.value)).toEqual(['', '7', '30', '45', '90', '180'])
    const forGood = { ...pro, allowance: { ...pro.allowance, retention_days: null }, chat_retention_days: null }
    expect(chatDayChoices(forGood).map((choice) => choice.value)).toEqual(['', '7', '30', '90', '180', '365', '730'])
  })

  it('says what a shorter time deletes, and nothing for one that keeps everything', () => {
    expect(deletes(pro, 30)).toBe(
      'The chats whose last question is more than 30 days old are deleted within six hours, for every member, and cannot be taken back.',
    )
    expect(deletes(pro, 365)).toBeNull()
    expect(deletes({ ...pro, chat_days: 30, chat_retention_days: 30 }, 90)).toBeNull()
    expect(deletes({ ...pro, chat_days: 30, chat_retention_days: 30 }, null)).toBeNull()
  })

  it('counts what a hold after a lower plan keeps', () => {
    // On Free's 30 days, held for good after Team for a while: 30 days takes what the hold keeps.
    const held = { ...pro, chat_retention_days: 30, retention_held: { until: '2026-11-20T00:00:00Z', chat_days: null, audit_days: 365 } }
    expect(deletes(held, 30)).not.toBeNull()
    const longer = { ...held, retention_held: { ...held.retention_held, chat_days: 365 } }
    expect(deletes(longer, 30)).not.toBeNull()
    // A hold of the audit log alone keeps no chat.
    const audit = { ...held, retention_held: { ...held.retention_held, chat_days: 30 } }
    expect(deletes(audit, 30)).toBeNull()
  })
})

describe('security', () => {
  const deployment = { ...pro, max_session_days: 30, session_days: null } as Organization

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
      'One member signs in without it: Bob. They set it up before working in the organisation again.',
    )
    const many = ['Bob', 'Cat', 'Dan', 'Eve', 'Fay'].map((name) => member(name, false))
    expect(heldByTwoFactor(many)).toBe(
      '5 members sign in without it: Bob, Cat, Dan and 2 more. Each sets it up before working in the organisation again.',
    )
    expect(heldByTwoFactor([{ ...member('', false), email: 'zed@example.com' }])).toContain(': zed@example.com.')
  })
})
