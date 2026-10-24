import { describe, expect, it } from 'vitest'
import type { ApiKey, Me } from './api.ts'
import { KEY_DAYS, UPLOAD_CLIENT, callWith, daysOf, describeKey, mayRoll, uploadWith } from './apiKeys.ts'
import { formatDate } from './format.ts'

const key: ApiKey = {
  id: 3,
  name: 'Invoices sync',
  scope: 'read',
  hint: 'emk_Ab3d',
  user_id: 7,
  made_by: 'Ada',
  created_at: '2026-10-01T09:00:00Z',
  rolled_at: null,
  last_used_at: null,
  expires_at: null,
  expired: false,
}

describe('API keys', () => {
  it('asks for the days chosen, or none for a key that runs until it is revoked', () => {
    expect(daysOf(KEY_DAYS[0].value)).toBe(90)
    expect(KEY_DAYS.map((choice) => daysOf(choice.value))).toEqual([90, 30, 365, null])
  })

  it('says whom a key acts as, when it was last used and when it runs out', () => {
    expect(describeKey(key)).toBe('emk_Ab3d… · acts as Ada · never used')
    const used = { ...key, last_used_at: '2026-10-20T12:00:00Z', expires_at: '2027-01-01T00:00:00Z' }
    expect(describeKey(used)).toBe(
      `emk_Ab3d… · acts as Ada · last used ${formatDate(used.last_used_at)} · runs until ${formatDate(used.expires_at)}`,
    )
    expect(describeKey({ ...used, expired: true })).toContain(`ran out on ${formatDate(used.expires_at)}`)
  })

  it('lets only the member a key acts as roll it', () => {
    expect(mayRoll(key, { id: 7 } as Me)).toBe(true)
    expect(mayRoll(key, { id: 8 } as Me)).toBe(false)
  })

  it('shows how a program calls this deployment with the key', () => {
    // Made at run time: a key written out here would be one to gitleaks.
    const secret = ['emk', 'x'.repeat(43)].join('_')
    expect(callWith(secret, 'https://emudoc.example.org')).toBe(
      `curl -H "Authorization: Bearer ${secret}" https://emudoc.example.org/api/projects`,
    )
  })

  it('shows how the upload client, served by the app, sends a folder with the key', () => {
    const secret = ['emk', 'x'.repeat(43)].join('_')
    expect(UPLOAD_CLIENT).toBe('/api/help/emudoc_upload.py')
    expect(uploadWith(secret, 'https://emudoc.example.org')).toBe(
      `EMUDOC_API_KEY=${secret} python3 emudoc_upload.py send --server https://emudoc.example.org --project "Invoices" ./Invoices`,
    )
  })
})
