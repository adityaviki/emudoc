import { describe, expect, it } from 'vitest'
import { auditExportUrl, auditQuery } from './api.ts'
import type { AuditAction, AuditEvent } from './api.ts'
import { ACTIONS, KINDS, NO_CHOICES, actionsOf, describeDetail, filterOf, whoDid } from './audit.ts'
import type { AuditKind } from './audit.ts'

const event = (action: AuditAction, detail: AuditEvent['detail'] = {}, fields: Partial<AuditEvent> = {}): AuditEvent => ({
  id: 1,
  occurred_at: '2026-10-21T09:30:00Z',
  action,
  user_id: 7,
  user_name: 'Ada',
  user_email: 'ada@example.com',
  ip: '203.0.113.7',
  project_id: null,
  project_name: null,
  target_type: null,
  target_id: null,
  detail,
  ...fields,
})

describe('the actions', () => {
  it('files every action under one of the kinds the filter offers', () => {
    const kinds = Object.keys(KINDS) as AuditKind[]
    const filed = kinds.flatMap(actionsOf)
    expect(filed.toSorted()).toEqual(Object.keys(ACTIONS).toSorted())
    expect(new Set(filed).size).toBe(filed.length)
    for (const kind of kinds) expect(actionsOf(kind).length).toBeGreaterThan(0)
  })
})

describe('the filter', () => {
  it('asks for nothing until something is chosen', () => {
    expect(filterOf(NO_CHOICES)).toEqual({ actions: [], userId: null, projectId: null, since: null, until: null })
    expect(auditQuery(filterOf(NO_CHOICES)).toString()).toBe('')
    expect(auditExportUrl(filterOf(NO_CHOICES))).toBe('/api/audit/export')
  })

  it('asks for a kind as its actions, and one action alone', () => {
    expect(filterOf({ ...NO_CHOICES, what: 'kind:chats' }).actions).toEqual([
      'chat_shared',
      'chat_unshared',
      'chat_deleted',
      'chat_restored',
      'chat_purged',
    ])
    expect(filterOf({ ...NO_CHOICES, what: 'sign_in' }).actions).toEqual(['sign_in'])
    const query = auditQuery(filterOf({ ...NO_CHOICES, what: 'kind:chats', who: '7', project: '12' }))
    expect(query.getAll('action')).toHaveLength(5)
    expect([query.get('user_id'), query.get('project_id')]).toEqual(['7', '12'])
  })

  it('runs the days from the first midnight until the one after the last, in the browser’s zone', () => {
    const { since, until } = filterOf({ ...NO_CHOICES, from: '2026-10-01', to: '2026-10-03' })
    const first = new Date(since ?? '')
    const after = new Date(until ?? '')
    expect([first.getFullYear(), first.getMonth(), first.getDate(), first.getHours(), first.getMinutes()]).toEqual([2026, 9, 1, 0, 0])
    expect([after.getMonth(), after.getDate(), after.getHours()]).toEqual([9, 4, 0])
    // The last day of a month runs into the next.
    expect(new Date(filterOf({ ...NO_CHOICES, to: '2026-10-31' }).until ?? '').getMonth()).toBe(10)
    expect(auditExportUrl(filterOf({ ...NO_CHOICES, from: '2026-10-01' }))).toMatch(/^\/api\/audit\/export\?since=2026-/)
  })
})

describe('a row', () => {
  it('names who did it, the app, or a member whose account is gone', () => {
    expect(whoDid(event('sign_in'))).toBe('Ada')
    expect(whoDid(event('sign_in', {}, { user_name: '' }))).toBe('ada@example.com')
    expect(whoDid(event('sample_added', {}, { user_id: null, user_name: null, user_email: null }))).toBe('Emudoc')
    expect(whoDid(event('sign_in', {}, { user_name: null, user_email: null }))).toBe('A former member')
  })

  it('words its detail from the names and settings it holds', () => {
    expect(describeDetail(event('document_uploaded', { filename: 'msa.pdf', size_bytes: 1200 }))).toBe('msa.pdf')
    const bob = new Map([[9, 'Bob']])
    expect(describeDetail(event('role_changed', { from: 'member', to: 'admin' }, { target_type: 'user', target_id: 9 }), bob)).toBe(
      'Bob · Member → Admin',
    )
    expect(describeDetail(event('ownership_transferred', { from: 'admin' }, { target_type: 'user', target_id: 4 }), bob)).toBe(
      'a former member · was Admin',
    )
    expect(describeDetail(event('invitation_sent', { role: 'viewer', again: true }))).toBe('Viewer')
    expect(describeDetail(event('plan_changed', { from: 'free', to: 'team' }))).toBe('Free → Team')
    expect(describeDetail(event('sign_in', { method: 'google' }))).toBe('through Google')
    expect(describeDetail(event('sign_in', { second_factor: 'recovery_code' }))).toBe('with a recovery code')
    expect(describeDetail(event('sign_in_failed', { reason: 'code' }))).toBe('wrong code')
    expect(describeDetail(event('joining_changed', { domains: 'acme.com', role: 'member' }))).toBe('Member · acme.com')
    expect(describeDetail(event('pack_bought', { unit: 'pages', quantity: 1000, month: '2026-10' }))).toBe('1,000 pages')
    expect(describeDetail(event('entitlements_changed', { entitlement: 'pages_per_month', from: 200, to: null, agreed: true }))).toBe(
      'pages per month · 200 → none',
    )
    expect(describeDetail(event('sign_out'))).toBe('')
    // A project's name is said once, as the row's project.
    expect(describeDetail(event('sample_added', { name: 'Sample', documents: 8 }, { project_name: 'Sample' }))).toBe('')
    expect(describeDetail(event('project_deleted', { name: 'Old', documents: 3 }))).toBe('Old')
  })

  it('words a setting by what it is', () => {
    const setting = (detail: AuditEvent['detail']) => describeDetail(event('settings_changed', detail))
    expect(setting({ setting: 'language', from: null, to: 'german' })).toBe('Language: the default → German')
    expect(setting({ setting: 'chat_days', from: 30, to: 7 })).toBe('Chats kept: 30 days after the last question → 7 days after the last question')
    expect(setting({ setting: 'session_days', from: null, to: 1 })).toBe('Sign in again: every day')
    expect(setting({ setting: 'session_days', from: 7, to: null })).toBe('Sign in again: as Emudoc asks')
    expect(setting({ setting: 'mfa_required', from: false, to: true })).toBe('Two-factor sign-in required')
    expect(setting({ setting: 'mfa_required', from: true, to: false })).toBe('Two-factor sign-in no longer required')
    expect(setting({ setting: 'notices', kind: 'quota', way: 'email', from: true, to: false })).toBe(
      "The month's pages or questions run low, by email: off",
    )
  })
})
