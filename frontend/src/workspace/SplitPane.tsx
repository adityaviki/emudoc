import { useRef, useState } from 'react'
import type { CSSProperties, FocusEvent, KeyboardEvent, PointerEvent, ReactNode } from 'react'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { IconButton } from '../components/ui.tsx'
import { useLocalStorage } from '../hooks.ts'
import { BIG_STEP, STEP, defaultPercents, isSplits, movable, moved } from './split.ts'
import type { Splits } from './split.ts'

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

interface Drag {
  /** Which divider is held: the one after this pane. */
  index: number
  startX: number
  width: number
  from: number[]
}

/** The divider whose buttons show, and the width they were last measured against. */
interface Offer {
  index: number
  width: number
}

/** How far, in pixels, a press may wander and still be a click rather than a drag. */
const CLICK_SLOP = 3

/**
 * Panes side by side with dividers between them, dragged, moved by the arrow keys (Shift for
 * larger steps) once focused, or by the buttons a click on one offers beside it, for whoever
 * cannot drag (WCAG 2.5.7); the widths are remembered in this browser.
 */
export function SplitPanes({ panes, storageKey, shown }: SplitPanesProps) {
  const [splits, setSplits] = useLocalStorage<Splits>(storageKey, {}, isSplits)
  const [drag, setDrag] = useState<Drag | null>(null)
  const [offer, setOffer] = useState<Offer | null>(null)
  const container = useRef<HTMLDivElement>(null)
  // Whether the last press was released where it began: a click, not the end of a drag.
  const clicked = useRef(false)

  // Each combination of panes keeps its own split: the library is a sidebar beside a document
  // but the main view without one.
  const layout = panes.map((pane) => pane.key).join('|')
  const stored = splits[layout]
  const percents = stored && stored.length === panes.length ? stored : defaultPercents(panes.map((pane) => pane.share))
  const mins = panes.map((pane) => pane.min)
  const measure = () => container.current!.getBoundingClientRect().width

  function onPointerDown(event: PointerEvent<HTMLDivElement>, index: number) {
    clicked.current = false
    if (event.button !== 0) return
    event.currentTarget.setPointerCapture(event.pointerId)
    setOffer(null)
    setDrag({ index, startX: event.clientX, width: measure(), from: percents })
  }

  function onPointerMove(event: PointerEvent<HTMLDivElement>) {
    if (!drag) return
    const { index, from, width } = drag
    const next = moved(from, mins, index, ((event.clientX - drag.startX) / width) * 100, width)
    setSplits((current) => ({ ...current, [layout]: next }), { persist: false })
  }

  function onPointerUp(event: PointerEvent<HTMLDivElement>, cancelled = false) {
    if (!drag) return
    setDrag(null)
    setSplits((value) => value)
    clicked.current = !cancelled && Math.abs(event.clientX - drag.startX) <= CLICK_SLOP
  }

  // The buttons show at the click rather than at the release, after the mouse events a touch
  // screen sends once the finger is up, which would otherwise land on them.
  function onClick(index: number) {
    if (clicked.current) setOffer({ index, width: measure() })
  }

  /** Moves the divider after pane `index` by `delta` percent, as its keys and buttons do. */
  function nudge(index: number, delta: number) {
    const width = measure()
    if (offer) setOffer({ index: offer.index, width })
    setSplits((current) => ({ ...current, [layout]: moved(percents, mins, index, delta, width) }))
  }

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>, index: number) {
    const direction = { ArrowLeft: -1, ArrowRight: 1 }[event.key]
    if (!direction) return
    event.preventDefault()
    nudge(index, direction * (event.shiftKey ? BIG_STEP : STEP))
  }

  // The buttons go once the keyboard leaves the divider and them, or on Escape, which leaves it on the divider.
  function onBlur(event: FocusEvent<HTMLDivElement>) {
    if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setOffer(null)
  }

  function onOfferKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key !== 'Escape' || !offer) return
    const divider = event.currentTarget.firstElementChild as HTMLElement
    divider.focus()
    setOffer(null)
  }

  return (
    <div ref={container} className={`flex h-full min-h-0 ${drag ? 'select-none' : ''}`}>
      {panes.map((pane, index) => {
        const last = index === panes.length - 1
        const name = pane.label.toLowerCase()
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
            <div key={`divider:${pane.key}`} className="relative hidden shrink-0 lg:block" onBlur={onBlur} onKeyDown={onOfferKeyDown}>
              <div
                role="separator"
                aria-orientation="vertical"
                // Named for the pane before it, whose share of the width it says.
                aria-label={pane.label}
                aria-valuenow={Math.round(percents[index])}
                aria-valuemin={0}
                aria-valuemax={100}
                tabIndex={0}
                title="Drag, click or use the arrow keys to resize; double-click for the starting widths"
                onKeyDown={(event) => onKeyDown(event, index)}
                onPointerDown={(event) => onPointerDown(event, index)}
                onPointerMove={onPointerMove}
                onPointerUp={(event) => onPointerUp(event)}
                onPointerCancel={(event) => onPointerUp(event, true)}
                onClick={() => onClick(index)}
                // A double-click puts this set of panes back to its starting widths.
                onDoubleClick={() =>
                  setSplits((current) => {
                    const rest = { ...current }
                    delete rest[layout]
                    return rest
                  })
                }
                className={`h-full w-1.5 cursor-col-resize touch-none border-l border-line transition-colors hover:bg-accent/40 focus-visible:-outline-offset-2! ${
                  drag?.index === index || offer?.index === index ? 'bg-accent/40' : ''
                }`}
              />
              {offer?.index === index && (
                <div
                  className="absolute top-1/2 left-1/2 z-20 flex -translate-x-1/2 -translate-y-1/2 gap-0.5 rounded-lg border border-line bg-surface p-0.5 shadow-md"
                  // A press here keeps the focus where it is: the divider losing it would take the buttons away.
                  onMouseDown={(event) => event.preventDefault()}
                >
                  {[
                    { delta: -BIG_STEP, label: `Narrow the ${name}`, Icon: ChevronLeft },
                    { delta: BIG_STEP, label: `Widen the ${name}`, Icon: ChevronRight },
                  ].map(({ delta, label, Icon }) => {
                    // At a minimum the button stays where it is, and says it does nothing, rather than lose the focus.
                    const stuck = !movable(percents, mins, index, delta, offer.width)
                    return (
                      <IconButton
                        key={label}
                        label={label}
                        aria-disabled={stuck}
                        className="aria-disabled:cursor-default aria-disabled:opacity-40"
                        onClick={() => {
                          if (!stuck) nudge(index, delta)
                        }}
                      >
                        <Icon className="size-4" />
                      </IconButton>
                    )
                  })}
                </div>
              )}
            </div>
          ),
        ]
      })}
      {/* An iframe (the PDF viewer) swallows pointer events; this sheet keeps the drag alive over it. */}
      {drag && <div className="fixed inset-0 z-50 cursor-col-resize" />}
    </div>
  )
}
