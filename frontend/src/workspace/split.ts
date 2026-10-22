// The widths of the workspace's panes, in percent of the whole, and how a divider between two of
// them moves: by a drag, by the arrow keys, or by the buttons a click on it offers
// (workspace/SplitPane.tsx).

/** The remembered widths, in percent, of each set of panes that has been shown. */
export type Splits = Record<string, number[]>

/** How far an arrow key moves a divider, in percent of the width. */
export const STEP = 2
/** How far Shift and an arrow, or one of a divider's buttons, moves it. */
export const BIG_STEP = 10

/** The panes' starting widths: each its `share` of the shares together. */
export function defaultPercents(shares: number[]): number[] {
  const total = shares.reduce((sum, share) => sum + share, 0)
  return shares.map((share) => (share / total) * 100)
}

/**
 * The widths once the divider after pane `index` moved by `delta` percent of `width` pixels: the
 * divider gives width from one neighbour to the other, and neither goes below its minimum, in
 * pixels (`mins`); the panes further off keep theirs.
 */
export function moved(from: number[], mins: number[], index: number, delta: number, width: number): number[] {
  const toPercent = (px: number) => (px / width) * 100
  const allowed = Math.min(from[index + 1] - toPercent(mins[index + 1]), Math.max(toPercent(mins[index]) - from[index], delta))
  const next = [...from]
  next[index] = from[index] + allowed
  next[index + 1] = from[index + 1] - allowed
  return next
}

/** Whether the divider after pane `index` can move by `delta` at all, or a neighbour is at its minimum. */
export function movable(from: number[], mins: number[], index: number, delta: number, width: number): boolean {
  // A divider held at a minimum may move by a rounding error, which nobody would see.
  return Math.abs(moved(from, mins, index, delta, width)[index] - from[index]) > 0.01
}

export const isSplits = (value: unknown): value is Splits =>
  typeof value === 'object' &&
  value !== null &&
  Object.values(value).every(
    (percents) =>
      Array.isArray(percents) &&
      percents.every((percent) => typeof percent === 'number' && Number.isFinite(percent) && percent > 0 && percent < 100),
  )
