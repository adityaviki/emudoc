import { describe, expect, it } from 'vitest'
import type { Member } from '../api.ts'
import {
  MAX_FEEDBACK_CHARS,
  attachedWords,
  byHandWords,
  feedbackOf,
  feedbackPage,
  mailtoOf,
  readersByHand,
  sentTo,
  writeHeading,
} from './feedback.ts'

describe('feedbackPage', () => {
  it('keeps the path and the ids of what is open, in the order the workspace names them', () => {
    expect(feedbackPage('/projects/12/documents/34', '?page=3&chat=7')).toBe('/projects/12/documents/34?chat=7&page=3')
    expect(feedbackPage('/projects/12', '?chat=new&trash')).toBe('/projects/12?chat=new&trash=')
    expect(feedbackPage('/organization/usage', '')).toBe('/organization/usage')
  })

  it("leaves out a search's words and anything that is not an id", () => {
    expect(feedbackPage('/projects/12', '?q=invoice%20acme&folder=5')).toBe('/projects/12?folder=5')
    expect(feedbackPage('/projects/12', '?chat=<script>&passage=1234567890123')).toBe('/projects/12')
    expect(feedbackPage('/projects/12', '?organization=4')).toBe('/projects/12')
  })

  it('writes a segment that is no plain word as a star, as an error report does', () => {
    expect(feedbackPage('/projects/12/documents/a b', '')).toBe('/projects/12/documents/*')
  })
})

describe('feedbackOf', () => {
  it('trims the words and attaches where the member was and what failed', () => {
    const failed = { reference: '0123456789abcdef0123456789abcdef', status: 409 }
    expect(feedbackOf('problem', '  It shows twice.\n', '/projects/12', '?chat=7', failed)).toEqual({
      kind: 'problem',
      message: 'It shows twice.',
      page: '/projects/12?chat=7',
      failed,
    })
    expect(feedbackOf('idea', 'More colour', '/', '', null).failed).toBeNull()
  })

  it('knows the longest message the server takes', () => {
    expect(MAX_FEEDBACK_CHARS).toBe(10_000)
  })
})

describe('attachedWords', () => {
  it('names the failed request only when there was one', () => {
    expect(attachedWords(null)).toBe("Sent with your name and address, this page's address and Emudoc's version, so that whoever answers can find what you met.")
    expect(attachedWords({ reference: 'x'.repeat(32), status: 500 })).toContain('and the reference of the last request that failed here')
  })
})

describe('whom the help menu writes to', () => {
  it("is the server's address for help where it names one, else the workspace's owners and admins", () => {
    expect(writeHeading('help@example.com')).toBe('Write to support')
    expect(writeHeading(null)).toBe("Write to your workspace's admins")
    expect(sentTo('help@example.com')).toBe('help@example.com')
    expect(sentTo(null)).toBe("your workspace's admins")
  })
})

describe('where the install mails nothing', () => {
  const member = (user_id: number, name: string, role: Member['role']): Member => ({
    user_id,
    name,
    email: `${name.toLowerCase() || 'nameless'}@example.com`,
    role,
    joined_at: '2026-11-13T09:00:00Z',
    mfa_enabled: null,
    last_seen_at: null,
  })
  const members = [member(1, 'Ada', 'owner'), member(2, 'Bob', 'admin'), member(3, '', 'admin'), member(4, 'Cy', 'member'), member(5, 'Dee', 'viewer')]

  it("lists the server's address for help, else the owners and admins but the member, as the list orders them", () => {
    expect(readersByHand('help@example.com', members, 4)).toEqual([{ name: 'Support', email: 'help@example.com' }])
    expect(readersByHand(null, members, 4).map((reader) => reader.name)).toEqual(['Ada', 'Bob', 'nameless@example.com'])
    expect(readersByHand(null, members, 2).map((reader) => reader.email)).toEqual(['ada@example.com', 'nameless@example.com'])
    expect(readersByHand(null, members.slice(0, 1), 1)).toEqual([])
  })

  it('says the menu sends nothing, and whom to write to', () => {
    expect(byHandWords('help@example.com')).toBe('This workspace sends no email, so the help menu cannot send your message. Write to support from your own email:')
    expect(byHandWords(null)).toBe('This workspace sends no email, so the help menu cannot send your message. Write to one of its owners or admins from your own email:')
  })

  it("writes a mail that carries the page's address and the failed request, encoded", () => {
    const failed = { reference: '0123456789abcdef0123456789abcdef', status: 500 }
    const href = mailtoOf('ada@example.com', 'https://emudoc.example.com', '/projects/12?chat=7', failed)
    expect(href.startsWith('mailto:ada@example.com?subject=Help%20with%20Emudoc&body=')).toBe(true)
    const body = decodeURIComponent(href.split('&body=')[1])
    expect(body).toBe(`\n\nPage: https://emudoc.example.com/projects/12?chat=7\nLast failed request: ${failed.reference}, answered 500`)
    expect(decodeURIComponent(mailtoOf('a@example.com', 'https://x.example', '/', null).split('&body=')[1])).toBe('\n\nPage: https://x.example/')
  })
})
