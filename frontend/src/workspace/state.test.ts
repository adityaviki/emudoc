import { describe, expect, it } from 'vitest'
import { closedByMenu, droppedAt, moved, pinned } from './state.ts'
import type { OpenTab } from './state.ts'

const tabs = (...ids: number[]): OpenTab[] => ids.map((id) => ({ id }))
const ids = (open: OpenTab[]) => open.map((tab) => tab.id)
// The ids as the strip shows them, a pinned one marked with a star.
const shown = (open: OpenTab[]) => open.map((tab) => (tab.pinned ? `${tab.id}*` : String(tab.id)))
const pin = (open: OpenTab[], ...each: number[]) => each.reduce((tabs, id) => pinned(tabs, id, true), open)

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

describe('moved, with pinned tabs', () => {
  it('keeps a tab among those pinned as it is, or not', () => {
    const open = pin(tabs(1, 2, 3, 4), 1, 2)
    expect(shown(moved(open, 4, 0))).toEqual(['1*', '2*', '4', '3'])
    expect(shown(moved(open, 1, 3))).toEqual(['2*', '1*', '3', '4'])
    expect(moved(open, 3, 1)).toBe(open)
    expect(moved(open, 2, 2)).toBe(open)
  })
})

describe('pinned', () => {
  it('puts a tab pinned after those pinned before it', () => {
    expect(shown(pin(tabs(1, 2, 3, 4), 3))).toEqual(['3*', '1', '2', '4'])
    expect(shown(pin(tabs(1, 2, 3, 4), 3, 2))).toEqual(['3*', '2*', '1', '4'])
  })

  it('puts a tab unpinned first of the rest, and writes no key for it', () => {
    const open = pin(tabs(1, 2, 3, 4), 3, 2, 4)
    const unpinned = pinned(open, 3, false)
    expect(shown(unpinned)).toEqual(['2*', '4*', '3', '1'])
    expect(JSON.parse(JSON.stringify(unpinned))[2]).toEqual({ id: 3 })
  })

  it('keeps where a tab points', () => {
    const open: OpenTab[] = [{ id: 1 }, { id: 2, focus: { page: 3 } }]
    expect(pinned(open, 2, true)).toEqual([{ id: 2, focus: { page: 3 }, pinned: true }, { id: 1 }])
  })

  it('gives the same list back when nothing changes', () => {
    const open = pin(tabs(1, 2), 1)
    expect(pinned(open, 1, true)).toBe(open)
    expect(pinned(open, 2, false)).toBe(open)
    expect(pinned(open, 9, true)).toBe(open)
  })
})

describe('closedByMenu', () => {
  it('leaves the pinned tabs open', () => {
    const open = pin(tabs(1, 2, 3, 4), 2)
    expect(closedByMenu(open)).toEqual([1, 3, 4])
    expect(closedByMenu(open, 3)).toEqual([1, 4])
    expect(closedByMenu(open, 2)).toEqual([1, 3, 4])
    expect(closedByMenu(pin(tabs(1), 1))).toEqual([])
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
