import { describe, expect, it } from 'vitest'
import { cursorAfter, leavesLayer } from './focus.ts'

describe('leavesLayer', () => {
  it('lets Tab go from one stop to the next, and out from the last', () => {
    expect(leavesLayer(3, 0, false)).toBe(false)
    expect(leavesLayer(3, 1, false)).toBe(false)
    expect(leavesLayer(3, 2, false)).toBe(true)
  })

  it('lets Shift+Tab go back to the stop before, and out from the first', () => {
    expect(leavesLayer(3, 2, true)).toBe(false)
    expect(leavesLayer(3, 1, true)).toBe(false)
    expect(leavesLayer(3, 0, true)).toBe(true)
  })

  it("takes Tab out of a menu, whose items the arrows reach rather than Tab", () => {
    expect(leavesLayer(0, -1, false)).toBe(true)
    expect(leavesLayer(0, -1, true)).toBe(true)
  })

  it('takes Tab from the layer itself into its first stop, or out when it has none', () => {
    expect(leavesLayer(2, -1, false, true)).toBe(false)
    expect(leavesLayer(0, -1, false, true)).toBe(true)
    expect(leavesLayer(2, -1, true, true)).toBe(true)
  })
})

describe('cursorAfter', () => {
  const keys = ['f:1', 'f:2', 'd:3', 'd:4', 'd:5']

  it('moves to the row after the last one deleted', () => {
    expect(cursorAfter(keys, new Set(['d:3']))).toBe('d:4')
    expect(cursorAfter(keys, new Set(['f:1', 'd:4']))).toBe('d:5')
  })

  it('moves back to the nearest row before when nothing is left after', () => {
    expect(cursorAfter(keys, new Set(['d:5']))).toBe('d:4')
    expect(cursorAfter(keys, new Set(['d:3', 'd:4', 'd:5']))).toBe('f:2')
  })

  it('skips the rows deleted with a folder, wherever they are', () => {
    expect(cursorAfter(keys, new Set(['f:2', 'd:4']))).toBe('d:5')
    expect(cursorAfter(keys, new Set(['f:1', 'd:5']))).toBe('d:4')
  })

  it('has nowhere to go once every row is deleted', () => {
    expect(cursorAfter(keys, new Set(keys))).toBeNull()
    expect(cursorAfter([], new Set())).toBeNull()
  })
})
