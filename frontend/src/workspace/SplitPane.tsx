import { useRef, useState } from 'react'
import type { CSSProperties, KeyboardEvent, PointerEvent, ReactNode } from 'react'
import { useLocalStorage } from '../hooks.ts'

export interface Pane {
  key: string
  /** What the pane is, which names the divider after it. */
  label: string
  node: ReactNode
  /** How wide this pane starts, relative to the others. */
  share: number
  /** The narrowest it may get, in pixels. */
  min: number
}

interface SplitPanesProps {
  panes: Pane[]
  storageKey: string
  /** Below the `lg` breakpoint only one pane shows; this is its key. */
  shown: string
}

/** The remembered widths, in percent, of each set of panes that has been shown. */
type Splits = Record<string, number[]>

interface Drag {
  /** Which divider is held: the one after this pane. */
  index: number
  startX: number
  width: number
  from: number[]
}

/**
 * Panes side by side with dividers between them, dragged, or moved by the arrow keys (Shift for
 * larger steps) once focused; the widths are remembered in this browser.
 */
export function SplitPanes({ panes, storageKey, shown }: SplitPanesProps) {
  const [splits, setSplits] = useLocalStorage<Splits>(storageKey, {}, isSplits)
  const [drag, setDrag] = useState<Drag | null>(null)
  const container = useRef<HTMLDivElement>(null)

  // Each combination of panes keeps its own split: the library is a sidebar beside a document
  // but the main view without one.
  const layout = panes.map((pane) => pane.key).join('|')
  const stored = splits[layout]
  const percents = stored && stored.length === panes.length ? stored : defaultPercents(panes)

  function onPointerDown(event: PointerEvent<HTMLDivElement>, index: number) {
    if (event.button !== 0) return
    event.currentTarget.setPointerCapture(event.pointerId)
    setDrag({ index, startX: event.clientX, width: container.current!.getBoundingClientRect().width, from: percents })
  }

  /** The widths once the divider after pane `index` moved by `delta` percent of the `width`. */
  function moved(from: number[], index: number, delta: number, width: number): number[] {
    const toPercent = (px: number) => (px / width) * 100
    // The divider moves width from one neighbour to the other; neither may go below its minimum.
    const allowed = Math.min(from[index + 1] - toPercent(panes[index + 1].min), Math.max(toPercent(panes[index].min) - from[index], delta))
    const next = [...from]
    next[index] = from[index] + allowed
    next[index + 1] = from[index + 1] - allowed
    return next
  }

  function onPointerMove(event: PointerEvent<HTMLDivElement>) {
    if (!drag) return
    const { index, from, width } = drag
    const next = moved(from, index, ((event.clientX - drag.startX) / width) * 100, width)
    setSplits((current) => ({ ...current, [layout]: next }), { persist: false })
  }

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>, index: number) {
    const direction = { ArrowLeft: -1, ArrowRight: 1 }[event.key]
    if (!direction) return
    event.preventDefault()
    const next = moved(percents, index, direction * (event.shiftKey ? 10 : 2), container.current!.getBoundingClientRect().width)
    setSplits((current) => ({ ...current, [layout]: next }))
  }

  function onPointerUp() {
    if (!drag) return
    setDrag(null)
    setSplits((value) => value)
  }

  return (
    <div ref={container} className={`flex h-full min-h-0 ${drag ? 'select-none' : ''}`}>
      {panes.map((pane, index) => {
        const last = index === panes.length - 1
        return [
          <div
            key={pane.key}
            style={{ '--split': `${percents[index]}%` } as CSSProperties}
            // The last pane takes what the dividers leave over.
            className={`min-w-0 w-full ${last ? 'lg:flex-1' : 'lg:w-(--split)'} ${
              shown === pane.key ? 'flex' : 'hidden lg:flex'
            } flex-col`}
          >
            {pane.node}
          </div>,
          !last && (
            <div
              key={`divider:${pane.key}`}
              role="separator"
              aria-orientation="vertical"
              // Named for the pane before it, whose share of the width it says.
              aria-label={pane.label}
              aria-valuenow={Math.round(percents[index])}
              aria-valuemin={0}
              aria-valuemax={100}
              tabIndex={0}
              onKeyDown={(event) => onKeyDown(event, index)}
              onPointerDown={(event) => onPointerDown(event, index)}
              onPointerMove={onPointerMove}
              onPointerUp={onPointerUp}
              onPointerCancel={onPointerUp}
              // A double-click puts this set of panes back to its starting widths.
              onDoubleClick={() =>
                setSplits((current) => {
                  const rest = { ...current }
                  delete rest[layout]
                  return rest
                })
              }
              className={`hidden w-1.5 shrink-0 cursor-col-resize touch-none border-l border-line transition-colors hover:bg-accent/40 focus-visible:-outline-offset-2! lg:block ${
                drag?.index === index ? 'bg-accent/40' : ''
              }`}
            />
          ),
        ]
      })}
      {/* An iframe (the PDF viewer) swallows pointer events; this sheet keeps the drag alive over it. */}
      {drag && <div className="fixed inset-0 z-50 cursor-col-resize" />}
    </div>
  )
}

function defaultPercents(panes: Pane[]): number[] {
  const total = panes.reduce((sum, pane) => sum + pane.share, 0)
  return panes.map((pane) => (pane.share / total) * 100)
}

const isSplits = (value: unknown): value is Splits =>
  typeof value === 'object' &&
  value !== null &&
  Object.values(value).every(
    (percents) =>
      Array.isArray(percents) &&
      percents.every((percent) => typeof percent === 'number' && Number.isFinite(percent) && percent > 0 && percent < 100),
  )
