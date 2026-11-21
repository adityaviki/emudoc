import { describe, expect, it } from 'vitest'
import { closedByMenu, documentTab as d, droppedAt, moved, myFileTab, pinned, shownAfter } from './state.ts'
import type { OpenTab } from './state.ts'

const tabs = (...ids: number[]): OpenTab[] => ids.map((id) => ({ id }))
const ids = (open: OpenTab[]) => open.map((tab) => tab.id)
// The ids as the strip shows them, a pinned one marked with a star.
const shown = (open: OpenTab[]) => open.map((tab) => (tab.pinned ? `${tab.id}*` : String(tab.id)))
const pin = (open: OpenTab[], ...each: number[]) => each.reduce((tabs, id) => pinned(tabs, d(id), true), open)

describe('moved', () => {
  it('puts a tab at its new place among the others', () => {
    expect(ids(moved(tabs(1, 2, 3, 4), d(1), 2))).toEqual([2, 3, 1, 4])
    expect(ids(moved(tabs(1, 2, 3, 4), d(4), 0))).toEqual([4, 1, 2, 3])
    expect(ids(moved(tabs(1, 2, 3, 4), d(2), 3))).toEqual([1, 3, 4, 2])
  })

  it('keeps where a tab points', () => {
    const open: OpenTab[] = [{ id: 1, focus: { passage: 7 } }, { id: 2 }]
    expect(moved(open, d(1), 1)).toEqual([{ id: 2 }, { id: 1, focus: { passage: 7 } }])
  })

  it('stops at the ends', () => {
    expect(ids(moved(tabs(1, 2, 3), d(2), -1))).toEqual([2, 1, 3])
    expect(ids(moved(tabs(1, 2, 3), d(2), 9))).toEqual([1, 3, 2])
  })

  it('gives the same list back when nothing moves, so the store is not written again', () => {
    const open = tabs(1, 2, 3)
    expect(moved(open, d(2), 1)).toBe(open)
    expect(moved(open, d(3), 9)).toBe(open)
    expect(moved(open, d(5), 0)).toBe(open)
  })
})

describe('moved, with pinned tabs', () => {
  it('keeps a tab among those pinned as it is, or not', () => {
    const open = pin(tabs(1, 2, 3, 4), 1, 2)
    expect(shown(moved(open, d(4), 0))).toEqual(['1*', '2*', '4', '3'])
    expect(shown(moved(open, d(1), 3))).toEqual(['2*', '1*', '3', '4'])
    expect(moved(open, d(3), 1)).toBe(open)
    expect(moved(open, d(2), 2)).toBe(open)
  })
})

describe('pinned', () => {
  it('puts a tab pinned after those pinned before it', () => {
    expect(shown(pin(tabs(1, 2, 3, 4), 3))).toEqual(['3*', '1', '2', '4'])
    expect(shown(pin(tabs(1, 2, 3, 4), 3, 2))).toEqual(['3*', '2*', '1', '4'])
  })

  it('puts a tab unpinned first of the rest, and writes no key for it', () => {
    const open = pin(tabs(1, 2, 3, 4), 3, 2, 4)
    const unpinned = pinned(open, d(3), false)
    expect(shown(unpinned)).toEqual(['2*', '4*', '3', '1'])
    expect(JSON.parse(JSON.stringify(unpinned))[2]).toEqual({ id: 3 })
  })

  it('keeps where a tab points', () => {
    const open: OpenTab[] = [{ id: 1 }, { id: 2, focus: { page: 3 } }]
    expect(pinned(open, d(2), true)).toEqual([{ id: 2, focus: { page: 3 }, pinned: true }, { id: 1 }])
  })

  it('gives the same list back when nothing changes', () => {
    const open = pin(tabs(1, 2), 1)
    expect(pinned(open, d(1), true)).toBe(open)
    expect(pinned(open, d(2), false)).toBe(open)
    expect(pinned(open, d(9), true)).toBe(open)
  })
})

describe('a tab of My files', () => {
  it('is told apart from the document of the same id', () => {
    const open: OpenTab[] = [{ id: 1 }, { id: 1, myFile: true }, { id: 2 }]
    expect(moved(open, myFileTab(1), 0)).toEqual([{ id: 1, myFile: true }, { id: 1 }, { id: 2 }])
    expect(pinned(open, myFileTab(1), true)).toEqual([{ id: 1, myFile: true, pinned: true }, { id: 1 }, { id: 2 }])
    expect(closedByMenu(open, d(1))).toEqual([myFileTab(1), d(2)])
  })
})

describe('shownAfter', () => {
  it('shows the nearest tab that stays open, to the right, else to the left, and none once all close', () => {
    const open: OpenTab[] = [{ id: 1 }, { id: 2, myFile: true }, { id: 3 }]
    expect(shownAfter(open, myFileTab(2), new Set([myFileTab(2)]))).toEqual({ id: 3 })
    expect(shownAfter(open, d(3), new Set([d(3)]))).toEqual({ id: 2, myFile: true })
    expect(shownAfter(open, d(1), new Set([d(1), myFileTab(2)]))).toEqual({ id: 3 })
    expect(shownAfter(open, d(1), new Set(open.map((tab) => (tab.myFile ? myFileTab(tab.id) : d(tab.id)))))).toBeUndefined()
  })
})

describe('closedByMenu', () => {
  it('leaves the pinned tabs open', () => {
    const open = pin(tabs(1, 2, 3, 4), 2)
    expect(closedByMenu(open)).toEqual(['d1', 'd3', 'd4'])
    expect(closedByMenu(open, d(3))).toEqual(['d1', 'd4'])
    expect(closedByMenu(open, d(2))).toEqual(['d1', 'd3', 'd4'])
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
    expect(ids(moved(open, d(1), droppedAt(0, open.length)))).toEqual([2, 3, 1])
  })
})
