import { describe, expect, it } from 'vitest'
import { droppedAt, moved } from './state.ts'
import type { OpenTab } from './state.ts'

const tabs = (...ids: number[]): OpenTab[] => ids.map((id) => ({ id }))
const ids = (open: OpenTab[]) => open.map((tab) => tab.id)

describe('moved', () => {
  it('puts a tab at its new place among the others', () => {
    expect(ids(moved(tabs(1, 2, 3, 4), 1, 2))).toEqual([2, 3, 1, 4])
    expect(ids(moved(tabs(1, 2, 3, 4), 4, 0))).toEqual([4, 1, 2, 3])
    expect(ids(moved(tabs(1, 2, 3, 4), 2, 3))).toEqual([1, 3, 4, 2])
  })

  it('keeps where a tab points', () => {
    const open: OpenTab[] = [{ id: 1, focus: { passage: 7 } }, { id: 2 }]
    expect(moved(open, 1, 1)).toEqual([{ id: 2 }, { id: 1, focus: { passage: 7 } }])
  })

  it('stops at the ends', () => {
    expect(ids(moved(tabs(1, 2, 3), 2, -1))).toEqual([2, 1, 3])
    expect(ids(moved(tabs(1, 2, 3), 2, 9))).toEqual([1, 3, 2])
  })

  it('gives the same list back when nothing moves, so the store is not written again', () => {
    const open = tabs(1, 2, 3)
    expect(moved(open, 2, 1)).toBe(open)
    expect(moved(open, 3, 9)).toBe(open)
    expect(moved(open, 5, 0)).toBe(open)
  })
})

describe('droppedAt', () => {
  it('counts the gaps with the dragged tab still in its place', () => {
    // Four tabs, the second dragged: the gaps on either side of it leave it where it is.
    expect(droppedAt(1, 0)).toBe(0)
    expect(droppedAt(1, 1)).toBe(1)
    expect(droppedAt(1, 2)).toBe(1)
    expect(droppedAt(1, 3)).toBe(2)
    expect(droppedAt(1, 4)).toBe(3)
  })

  it('moves a tab dropped after the last to the end', () => {
    const open = tabs(1, 2, 3)
    expect(ids(moved(open, 1, droppedAt(0, open.length)))).toEqual([2, 3, 1])
  })
})
