import { describe, expect, it } from 'vitest'
import type { InboundMessage } from './api.ts'
import { outcomeWords, senderEntry, senderProblem, sendersWords, sentWords } from './mailbox.ts'

const message = (fields: Partial<InboundMessage> = {}): InboundMessage => ({
  id: 1,
  received_at: '2026-10-24T08:02:00Z',
  sender: 'lena@northwind.example',
  member: null,
  subject: 'Invoice INV-7',
  outcome: 'kept',
  reason: '',
  kept: 2,
  rejected: [],
  ...fields,
})

describe('the senders’ list', () => {
  it('keeps an entry as the server does', () => {
    expect(senderEntry(' AP@Northwind.Example ')).toBe('ap@northwind.example')
    expect(senderEntry('@Acme.example')).toBe('acme.example')
    expect(senderEntry('acme.example')).toBe('acme.example')
  })

  it('takes an address or a domain, once', () => {
    expect(senderProblem('ap@acme.example', [])).toBeNull()
    expect(senderProblem('acme.example', [])).toBeNull()
    expect(senderProblem('@acme.example', [])).toBeNull()
    expect(senderProblem('', [])).toBe('Type an address such as ap@acme.com, or a domain such as acme.com.')
    expect(senderProblem('ap@nowhere', [])).toBe('ap@nowhere is not an email address. Type one such as ap@acme.com.')
    expect(senderProblem('a b@acme.example', [])).toBe('a b@acme.example is not an email address. Type one such as ap@acme.com.')
    expect(senderProblem('acme', [])).toBe('acme is neither an address nor a domain. Type one such as ap@acme.com or acme.com.')
    expect(senderProblem('ACME.example', ['acme.example'])).toBe('acme.example is on the list already.')
  })

  it('says whom the address takes mail from', () => {
    expect(sendersWords([])).toBe(
      'It takes mail from the members who may change the project, from their own confirmed addresses, and nobody else yet.',
    )
    expect(sendersWords(['acme.example'])).toContain('and from the sender below')
    expect(sendersWords(['acme.example', 'ap@globex.example'])).toContain('and from the 2 senders below')
  })
})

describe('a message received', () => {
  it('says what became of it', () => {
    expect(outcomeWords(message())).toBe('2 documents kept')
    expect(outcomeWords(message({ kept: 1 }))).toBe('1 document kept')
    expect(outcomeWords(message({ outcome: 'refused', kept: 0 }))).toBe('Turned away')
    expect(outcomeWords(message({ outcome: 'empty', kept: 0 }))).toBe('Nothing kept')
  })

  it('names its sender, a member by name', () => {
    expect(sentWords(message())).toMatch(/^lena@northwind\.example, /)
    expect(sentWords(message({ member: 'Bob', sender: 'bob@acme.example' }))).toMatch(/^Bob \(bob@acme\.example\), /)
    expect(sentWords(message({ sender: '' }))).toMatch(/^An unreadable sender, /)
  })
})
