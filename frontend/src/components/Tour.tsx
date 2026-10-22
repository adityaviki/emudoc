import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import type { KeyboardEvent } from 'react'
import { createPortal } from 'react-dom'
import { placeBeside } from '../tour.ts'
import type { Box, TourStep } from '../tour.ts'
import { Button } from './ui.tsx'

// How far the ring around the element pointed at stands off it.
const RING = 4

/**
 * The element a step points at: the first of those marked for it that is shown, since a hidden
 * pane, on a narrow screen, has no size, and the pane switcher shown there stands in for it.
 */
function targetOf(step: TourStep): Element | null {
  return (
    [...document.querySelectorAll(`[data-tour="${step.target}"]`)].find((element) => {
      const { width, height } = element.getBoundingClientRect()
      return width > 0 && height > 0
    }) ?? null
  )
}

function boxOf(element: Element): Box {
  const { left, top, width, height } = element.getBoundingClientRect()
  return { left: left - RING, top: top - RING, width: width + 2 * RING, height: height + 2 * RING }
}

/**
 * A few cards, one after another, each pointing at a part of the page with the rest dimmed.
 * The page waits underneath until the tour ends: by its last card, by "Skip the tour" or by
 * Escape, which all call `onEnd`. Focus moves into the card, Tab stays in it, and focus goes
 * back where it was at the end. The steps whose element is not on the page are left out.
 */
export function Tour({ steps, onEnd }: { steps: TourStep[]; onEnd: () => void }) {
  // The steps that can be shown, found once the page under the tour is laid out.
  const [shown, setShown] = useState<TourStep[] | null>(null)
  const [index, setIndex] = useState(0)
  const [box, setBox] = useState<Box | null>(null)
  const [place, setPlace] = useState<{ left: number; top: number } | null>(null)
  const card = useRef<HTMLDivElement>(null)
  const id = useId()

  useLayoutEffect(() => {
    const found = steps.filter((step) => targetOf(step) !== null)
    // What is shown is read from the page once laid out, as a measurement is, before it paints.
    // oxlint-disable-next-line react/set-state-in-effect
    if (found.length) setShown(found)
    else onEnd()
  }, [steps, onEnd])

  // Back to what had focus, once the tour is over.
  useEffect(() => {
    const before = document.activeElement
    return () => {
      if (before instanceof HTMLElement && before.isConnected) before.focus()
    }
  }, [])

  useEffect(() => {
    function onKeyDown(event: globalThis.KeyboardEvent) {
      if (event.key === 'Escape') onEnd()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [onEnd])

  const step = shown?.[index]
  // The element pointed at, measured again whenever it or the window changes size.
  useLayoutEffect(() => {
    if (!step) return
    const element = targetOf(step)
    if (!element) return
    const measure = () => setBox(boxOf(element))
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(element)
    window.addEventListener('resize', measure)
    return () => {
      observer.disconnect()
      window.removeEventListener('resize', measure)
    }
  }, [step])

  // The card goes beside it, once its own size is known.
  useLayoutEffect(() => {
    if (!box || !card.current) return
    const { width, height } = card.current.getBoundingClientRect()
    setPlace(placeBeside(box, { width, height }, { width: window.innerWidth, height: window.innerHeight }))
  }, [box, index])

  useEffect(() => {
    card.current?.querySelector<HTMLButtonElement>('[data-next]')?.focus()
  }, [index, shown])

  if (!shown || !step) return null
  const last = index === shown.length - 1

  // Tab goes round the card's buttons alone.
  function onKeyDown(event: KeyboardEvent) {
    if (event.key === 'Tab') {
      const buttons = [...(card.current?.querySelectorAll<HTMLButtonElement>('button') ?? [])]
      const at = buttons.indexOf(document.activeElement as HTMLButtonElement)
      const next = (at + (event.shiftKey ? -1 : 1) + buttons.length) % buttons.length
      event.preventDefault()
      buttons[next]?.focus()
    }
  }

  return createPortal(
    // The page underneath takes no click while the tour shows, and a click beside the card
    // keeps the focus in it.
    <div
      className="fixed inset-0 z-50"
      onKeyDown={onKeyDown}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) event.preventDefault()
      }}
    >
      {box && (
        <div
          aria-hidden
          className="pointer-events-none fixed rounded-lg shadow-[0_0_0_9999px_var(--color-scrim)] ring-2 ring-accent"
          style={{ left: box.left, top: box.top, width: box.width, height: box.height }}
        />
      )}
      <div
        ref={card}
        role="dialog"
        aria-modal="true"
        aria-labelledby={`${id}-title`}
        aria-describedby={`${id}-body`}
        // Unplaced, it is laid out unseen to be measured, and its button focused meanwhile, which a hidden one may not be.
        style={place ?? { left: 0, top: 0, opacity: 0 }}
        className="fixed w-80 max-w-[calc(100vw-16px)] rounded-xl border border-line bg-surface p-4 shadow-xl shadow-ink/8"
      >
        <p className="text-[11px] font-medium text-muted tabular-nums">
          {index + 1} of {shown.length}
        </p>
        <h2 id={`${id}-title`} className="mt-0.5 font-semibold">
          {step.title}
        </h2>
        <p id={`${id}-body`} className="mt-1 text-[13px] leading-relaxed text-muted">
          {step.body}
        </p>
        <div className="mt-4 flex items-center justify-between gap-2">
          {last ? <span /> : (
            <Button variant="ghost" className="-ml-2" onClick={onEnd}>
              Skip the tour
            </Button>
          )}
          <div className="flex gap-2">
            {index > 0 && <Button onClick={() => setIndex(index - 1)}>Back</Button>}
            <Button variant="primary" data-next onClick={() => (last ? onEnd() : setIndex(index + 1))}>
              {last ? 'Done' : 'Next'}
            </Button>
          </div>
        </div>
      </div>
    </div>,
    document.body,
  )
}
