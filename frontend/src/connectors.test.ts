import { describe, expect, it } from 'vitest'
import type { Connector } from './api.ts'
import { connectError, connectedWords, keptWords, moreRejected, standingWords } from './connectors.ts'
import { formatDateTime } from './format.ts'

const connector = (fields: Partial<Connector> = {}): Connector => ({
  id: 1,
  provider: 'google',
  label: 'Google Drive',
  account: 'ann@gmail.example',
  member: 'Ann',
  mine: true,
  status: 'active',
  folder: 'Invoices',
  target_folder_id: null,
  target: null,
  problem: '',
  synced_at: '2026-10-24T10:02:00Z',
  next_sync_at: '2026-10-24T10:17:00Z',
  syncing: false,
  kept: 2,
  rejected: [],
  rejected_count: 0,
  ...fields,
})

describe('connected folders', () => {
  it('words why a consent connected nothing, and nothing for a code the server does not send', () => {
    expect(connectError('cancelled')).toBe('The connection was cancelled, so nothing was connected.')
    expect(connectError('scope')).toMatch(/^The service was connected without leave to see your files/)
    expect(connectError('anything else')).toBeNull()
    expect(connectError(null)).toBeNull()
  })

  it('says how each stands', () => {
    expect(standingWords(connector())).toBe(`Synced ${formatDateTime('2026-10-24T10:02:00Z')}`)
    expect(standingWords(connector({ syncing: true }))).toBe('Syncing…')
    expect(standingWords(connector({ problem: 'Google Drive did not answer.' }))).toBe('Not synced in full')
    expect(standingWords(connector({ status: 'paused' }))).toBe('Paused')
    expect(standingWords(connector({ status: 'choosing', folder: null }))).toBe('Choose its folder')
    expect(standingWords(connector({ status: 'choosing', folder: null, mine: false }))).toBe('Ann is choosing its folder')
    expect(standingWords(connector({ synced_at: null }))).toBe('Waiting for its first sync')
  })

  it('says what it holds, whose it is and how many files it left out', () => {
    expect(keptWords(connector(), 15)).toBe('2 documents in the project’s root, synced every 15 minutes.')
    expect(keptWords(connector({ kept: 1, target: 'From Drive', status: 'paused' }), 15)).toBe('1 document in From Drive.')
    expect(connectedWords(connector())).toBe('You connected it as ann@gmail.example.')
    expect(connectedWords(connector({ mine: false, account: '' }))).toBe('Ann connected it.')
    const rejected = [{ path: 'Survey', reason: 'Not exported.' }]
    expect(moreRejected(connector({ rejected, rejected_count: 1 }))).toBeNull()
    expect(moreRejected(connector({ rejected, rejected_count: 26 }))).toBe('and 25 more')
  })
})
