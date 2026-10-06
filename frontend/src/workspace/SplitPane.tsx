import { useRef, useState } from 'react'
import type { CSSProperties, PointerEvent, ReactNode } from 'react'
import { useLocalStorage } from '../hooks.ts'

const DEFAULT_PERCENT = 55
// The narrowest either pane may get, in pixels.
const MIN_WIDTH = 320

interface SplitPaneProps {
  left: ReactNode
  right: ReactNode
  storageKey: string
  /** Below the `lg` breakpoint only one pane shows; this picks which. */
  shown: 'left' | 'right'
}

/** Two panes side by side with a draggable divider; the split is remembered in this browser. */
export function SplitPane({ left, right, storageKey, shown }: SplitPaneProps) {
  const [percent, setPercent] = useLocalStorage(storageKey, DEFAULT_PERCENT, isPercent)
  const [dragging, setDragging] = useState(false)
  const container = useRef<HTMLDivElement>(null)
  const bounds = useRef<DOMRect | null>(null)

  function onPointerDown(event: PointerEvent<HTMLDivElement>) {
    if (event.button !== 0) return
    bounds.current = container.current!.getBoundingClientRect()
    event.currentTarget.setPointerCapture(event.pointerId)
    setDragging(true)
  }

  function onPointerMove(event: PointerEvent<HTMLDivElement>) {
    const rect = bounds.current
    if (!dragging || !rect) return
    const minPercent = (MIN_WIDTH / rect.width) * 100
    const next = ((event.clientX - rect.left) / rect.width) * 100
    setPercent(Math.min(100 - minPercent, Math.max(minPercent, next)), { persist: false })
  }

  function onPointerUp() {
    if (!dragging) return
    setDragging(false)
    setPercent((value) => value)
  }

  return (
    <div ref={container} className={`flex h-full min-h-0 ${dragging ? 'select-none' : ''}`}>
      <div
        style={{ '--split': `${percent}%` } as CSSProperties}
        className={`min-w-0 w-full lg:w-(--split) ${shown === 'left' ? 'flex' : 'hidden lg:flex'} flex-col`}
      >
        {left}
      </div>
      <div
        role="separator"
        aria-orientation="vertical"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onDoubleClick={() => setPercent(DEFAULT_PERCENT)}
        className={`hidden w-1.5 shrink-0 cursor-col-resize touch-none border-l border-line transition-colors hover:bg-accent/40 lg:block ${
          dragging ? 'bg-accent/40' : ''
        }`}
      />
      <div className={`min-w-0 flex-1 ${shown === 'right' ? 'flex' : 'hidden lg:flex'} flex-col`}>{right}</div>
      {/* An iframe (the PDF viewer) swallows pointer events; this sheet keeps the drag alive over it. */}
      {dragging && <div className="fixed inset-0 z-50 cursor-col-resize" />}
    </div>
  )
}

const isPercent = (value: unknown): value is number =>
  typeof value === 'number' && Number.isFinite(value) && value > 0 && value < 100
