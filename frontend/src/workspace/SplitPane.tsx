import { useRef, useState } from 'react'
import type { CSSProperties, PointerEvent, ReactNode } from 'react'
import { useLocalStorage } from '../hooks.ts'

export interface Pane {
  key: string
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

/** Panes side by side with draggable dividers between them; the widths are remembered in this browser. */
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

  function onPointerMove(event: PointerEvent<HTMLDivElement>) {
    if (!drag) return
    const { index, from, width } = drag
    const toPercent = (px: number) => (px / width) * 100
    // The divider moves width from one neighbour to the other; neither may go below its minimum.
    const delta = Math.min(
      from[index + 1] - toPercent(panes[index + 1].min),
      Math.max(toPercent(panes[index].min) - from[index], toPercent(event.clientX - drag.startX)),
    )
    const next = [...from]
    next[index] = from[index] + delta
    next[index + 1] = from[index + 1] - delta
    setSplits((current) => ({ ...current, [layout]: next }), { persist: false })
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
              className={`hidden w-1.5 shrink-0 cursor-col-resize touch-none border-l border-line transition-colors hover:bg-accent/40 lg:block ${
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
