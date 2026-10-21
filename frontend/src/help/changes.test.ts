import { describe, expect, it } from 'vitest'
import { dayWords, unreadPerDay, unreadWords } from './changes.ts'

describe('dayWords', () => {
  it('words a day whatever zone the browser is in', () => {
    expect(dayWords({ first: '2026-10-21', last: '2026-10-21' }, 'en-GB')).toBe('21 October 2026')
    expect(dayWords({ first: '2026-01-01', last: '2026-01-01' }, 'en-US')).toBe('January 1, 2026')
  })

  it('words days told as one as a span', () => {
    // Spaced as the browser's own data spaces a range, thin or not at all.
    expect(dayWords({ first: '2026-10-05', last: '2026-10-10' }, 'en-GB')).toMatch(/^5\s?–\s?10 October 2026$/)
    expect(dayWords({ first: '2026-09-28', last: '2026-10-03' }, 'en-GB')).toMatch(/^28 September\s?–\s?3 October 2026$/)
  })
})

describe('unreadPerDay', () => {
  const days = [{ entries: ['a', 'b'] }, { entries: ['c'] }, { entries: ['d', 'e', 'f'] }]

  it('marks the newest entries, from the top, across days', () => {
    expect(unreadPerDay(days, 0)).toEqual([0, 0, 0])
    expect(unreadPerDay(days, 1)).toEqual([1, 0, 0])
    expect(unreadPerDay(days, 4)).toEqual([2, 1, 1])
  })

  it('marks what the page holds of more than it loaded', () => {
    expect(unreadPerDay(days, 40)).toEqual([2, 1, 3])
    expect(unreadPerDay([], 3)).toEqual([])
  })
})

describe('unreadWords', () => {
  it('counts the entries', () => {
    expect(unreadWords(1)).toBe('1 new')
    expect(unreadWords(12)).toBe('12 new')
  })
})
