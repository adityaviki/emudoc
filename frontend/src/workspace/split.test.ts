import { describe, expect, it } from 'vitest'
import { BIG_STEP, defaultPercents, isSplits, movable, moved } from './split.ts'

// The library, a document and the chat, side by side 2,000 pixels wide: at least 12, 18 and 16 %.
const MINS = [240, 360, 320]
const WIDTH = 2000

describe('defaultPercents', () => {
  it('gives each pane its share of the shares together', () => {
    expect(defaultPercents([25, 45, 30])).toEqual([25, 45, 30])
    expect(defaultPercents([1, 3])).toEqual([25, 75])
    expect(defaultPercents([2, 1, 1])).toEqual([50, 25, 25])
  })
})

describe('moved', () => {
  it('gives width from one neighbour to the other, the pane further off keeping its own', () => {
    expect(moved([30, 40, 30], MINS, 0, BIG_STEP, WIDTH)).toEqual([40, 30, 30])
    expect(moved([30, 40, 30], MINS, 1, -BIG_STEP, WIDTH)).toEqual([30, 30, 40])
  })

  it('stops where the pane before it would go below its minimum', () => {
    expect(moved([20, 50, 30], MINS, 0, -BIG_STEP, WIDTH)).toEqual([12, 58, 30])
  })

  it('stops where the pane after it would go below its minimum', () => {
    expect(moved([30, 50, 20], MINS, 1, BIG_STEP, WIDTH)).toEqual([30, 54, 16])
  })

  it('reads the minimums against the width the panes have', () => {
    expect(moved([30, 40, 30], MINS, 0, -BIG_STEP, 2000)).toEqual([20, 50, 30])
    expect(moved([30, 40, 30], MINS, 0, -BIG_STEP, 1000)).toEqual([24, 46, 30])
  })
})

describe('movable', () => {
  it('says a divider moves while both neighbours are above their minimums', () => {
    expect(movable([30, 40, 30], MINS, 0, -BIG_STEP, WIDTH)).toBe(true)
    expect(movable([30, 40, 30], MINS, 0, BIG_STEP, WIDTH)).toBe(true)
  })

  it('says it does not once the pane it would narrow is at its minimum', () => {
    const narrowest = moved([20, 50, 30], MINS, 0, -BIG_STEP, WIDTH)
    expect(movable(narrowest, MINS, 0, -BIG_STEP, WIDTH)).toBe(false)
    expect(movable(narrowest, MINS, 0, BIG_STEP, WIDTH)).toBe(true)
    const widest = moved([30, 50, 20], MINS, 1, BIG_STEP, WIDTH)
    expect(movable(widest, MINS, 1, BIG_STEP, WIDTH)).toBe(false)
  })
})

describe('isSplits', () => {
  it('takes widths in percent by layout, and nothing else from the storage', () => {
    expect(isSplits({ 'library|chat': [55, 45] })).toBe(true)
    expect(isSplits({})).toBe(true)
    expect(isSplits({ 'library|chat': [0, 100] })).toBe(false)
    expect(isSplits({ 'library|chat': ['55', 45] })).toBe(false)
    expect(isSplits([55, 45])).toBe(false)
    expect(isSplits(null)).toBe(false)
  })
})
