import { describe, expect, it } from 'vitest'
import { MAX_FEEDBACK_CHARS, attachedWords, feedbackOf, feedbackPage, sentTo, writeHeading } from './feedback.ts'

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
