// Floating layers (menus, popovers) rendered into document.body and kept inside the viewport,
// and the menus built on them: a context menu with keyboard navigation. A layer sits in the
// keyboard's order as if it came right after what opened it, which is where focus goes back.

import { Check } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { KeyboardEvent, ReactNode, RefObject } from 'react'
import { createPortal } from 'react-dom'
import { leavesLayer } from '../focus.ts'

/** Where a layer opens: at a point (a right-click) or below an element (a dropdown button). */
export type Anchor = { x: number; y: number } | DOMRect

const MARGIN = 8
const GAP = 4

interface FloatingProps {
  anchor: Anchor
  onClose: () => void
  children: ReactNode
  /** Align the layer's right edge with a rect anchor's right edge. */
  alignEnd?: boolean
  /** Pointer-downs inside this element do not close the layer (the button that toggles it). */
  ignore?: RefObject<HTMLElement | null>
  /** What focus goes back to: by default the button it ignores, else what had focus as it opened. */
  opener?: HTMLElement | null
  /** A preview shown on hover or focus, which leaves the focus where it is rather than take it. */
  preview?: boolean
  className?: string
}

/** What Tab stops at inside an element, in order. */
function tabStops(element: HTMLElement): HTMLElement[] {
  return [
    ...element.querySelectorAll<HTMLElement>('a[href], button, input, select, textarea, [tabindex]'),
  ].filter((stop) => stop.tabIndex >= 0 && !stop.matches(':disabled') && stop.getClientRects().length > 0)
}

/**
 * A layer over the page, which closes on Escape, a click outside, a scroll, a resize and the
 * window's blur. It takes the focus as it opens, unless something in it took it first or it is a
 * preview, and gives it back to its opener when it closes with the focus inside; Tab past its
 * last stop goes on from the opener, and Shift+Tab before its first goes back to the opener.
 */
export function Floating({ anchor, onClose, children, alignEnd, ignore, opener, preview, className = '' }: FloatingProps) {
  const ref = useRef<HTMLDivElement>(null)
  const [position, setPosition] = useState<{ left: number; top: number } | null>(null)
  // Read once, as the layer opens, before anything in it takes the focus.
  const back = useRef<HTMLElement | null>(null)

  useLayoutEffect(() => {
    const layer = ref.current
    const active = document.activeElement
    const before = opener ?? ignore?.current ?? (active instanceof HTMLElement && active !== document.body ? active : null)
    back.current = before
    return () => {
      // Run before the layer leaves the page, while the focus it holds can still be seen.
      if (layer?.contains(document.activeElement) && before?.isConnected) before.focus()
    }
    // The opener is what had focus as the layer opened, read once.
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    const layer = ref.current
    if (!layer || preview || layer.contains(document.activeElement)) return
    ;(tabStops(layer)[0] ?? layer).focus()
  }, [preview])

  useLayoutEffect(() => {
    const element = ref.current
    if (!element) return
    const { width, height } = element.getBoundingClientRect()
    let left: number
    let top: number
    if ('width' in anchor) {
      left = alignEnd ? anchor.right - width : anchor.left
      top = anchor.bottom + GAP
      // Flip above the anchor when there is no room below it.
      if (top + height > window.innerHeight - MARGIN && anchor.top - GAP - height >= MARGIN) {
        top = anchor.top - GAP - height
      }
    } else {
      left = anchor.x
      top = anchor.y
    }
    left = Math.max(MARGIN, Math.min(left, window.innerWidth - width - MARGIN))
    top = Math.max(MARGIN, Math.min(top, window.innerHeight - height - MARGIN))
    setPosition({ left, top })
  }, [anchor, alignEnd])

  useEffect(() => {
    function onPointerDown(event: PointerEvent) {
      const target = event.target as Node
      if (ref.current?.contains(target) || ignore?.current?.contains(target)) return
      onClose()
    }
    function onKeyDown(event: globalThis.KeyboardEvent) {
      if (event.key === 'Escape') onClose()
    }
    // What scrolls under the layer moves its anchor away; a list of its own scrolling does not.
    function onScroll(event: Event) {
      if (event.target instanceof Node && ref.current?.contains(event.target)) return
      onClose()
    }
    window.addEventListener('pointerdown', onPointerDown, true)
    window.addEventListener('keydown', onKeyDown)
    window.addEventListener('blur', onClose)
    window.addEventListener('resize', onClose)
    window.addEventListener('scroll', onScroll, true)
    return () => {
      window.removeEventListener('pointerdown', onPointerDown, true)
      window.removeEventListener('keydown', onKeyDown)
      window.removeEventListener('blur', onClose)
      window.removeEventListener('resize', onClose)
      window.removeEventListener('scroll', onScroll, true)
    }
  }, [onClose, ignore])

  function onKeyDown(event: KeyboardEvent) {
    // What is pressed in the layer is the layer's alone: through the portal, the page under it
    // would act on it too (a listing's Enter would open its row). Escape, which the window's
    // listener then no longer hears from inside, closes it here.
    event.stopPropagation()
    if (event.key === 'Escape') onClose()
    const layer = ref.current
    const before = back.current
    if (event.key !== 'Tab' || !layer || !before?.isConnected) return
    const stops = tabStops(layer)
    const at = stops.indexOf(document.activeElement as HTMLElement)
    if (!leavesLayer(stops.length, at, event.shiftKey, document.activeElement === layer)) return
    // Out of the layer: back to the opener, and on from there in the browser's own order.
    if (event.shiftKey) event.preventDefault()
    before.focus()
    onClose()
  }

  return createPortal(
    <div
      ref={ref}
      tabIndex={-1}
      onKeyDown={onKeyDown}
      // Unplaced, it is laid out unseen to be measured, and may be focused meanwhile, which a hidden element may not.
      style={position ? { left: position.left, top: position.top } : { left: 0, top: 0, opacity: 0 }}
      className={`fixed z-50 overflow-hidden rounded-lg border border-line bg-surface shadow-xl shadow-ink/8 outline-none ${className}`}
    >
      {children}
    </div>,
    document.body,
  )
}

export type MenuItem =
  | {
      label: string
      icon?: LucideIcon
      shortcut?: string
      danger?: boolean
      disabled?: boolean
      /** Set on each of a menu of choices, true on the one chosen: a radio item. */
      checked?: boolean
      onSelect: () => void
    }
  | 'separator'

interface MenuProps {
  items: MenuItem[]
  onClose: () => void
}

/** A list of actions. Arrow keys, Home and End move, Enter or a click runs one; every choice closes the menu. */
export function Menu({ items, onClose }: MenuProps) {
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    // A menu of choices opens at the one chosen.
    const chosen = ref.current?.querySelector<HTMLButtonElement>('button[aria-checked="true"]:not(:disabled)')
    ;(chosen ?? ref.current?.querySelector<HTMLButtonElement>('button:not(:disabled)'))?.focus()
  }, [])

  function onKeyDown(event: KeyboardEvent) {
    const buttons = [...(ref.current?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)') ?? [])]
    if (!buttons.length) return
    const index = buttons.indexOf(document.activeElement as HTMLButtonElement)
    const moves: Record<string, number> = {
      ArrowDown: index < 0 ? 0 : (index + 1) % buttons.length,
      ArrowUp: index < 0 ? buttons.length - 1 : (index - 1 + buttons.length) % buttons.length,
      Home: 0,
      End: buttons.length - 1,
    }
    if (event.key in moves) {
      event.preventDefault()
      buttons[moves[event.key]].focus()
    }
  }

  return (
    <div ref={ref} role="menu" onKeyDown={onKeyDown} className="min-w-44 p-1">
      {items.map((item, index) =>
        item === 'separator' ? (
          <div key={index} role="separator" className="-mx-1 my-1 border-t border-line" />
        ) : (
          <button
            key={index}
            type="button"
            role={item.checked === undefined ? 'menuitem' : 'menuitemradio'}
            aria-checked={item.checked}
            // The arrows move between the items; Tab leaves the menu (Floating).
            tabIndex={-1}
            disabled={item.disabled}
            onClick={() => {
              onClose()
              item.onSelect()
            }}
            className={`flex h-8 w-full items-center gap-2.5 rounded-[5px] px-2 text-left text-[13px] outline-none hover:bg-sunken focus-visible:bg-sunken disabled:opacity-40 ${
              item.danger ? 'text-bad' : 'text-ink'
            }`}
          >
            {item.icon && <item.icon className="size-4 shrink-0 opacity-70" />}
            <span className="min-w-0 flex-1 truncate">{item.label}</span>
            {item.checked && <Check className="size-4 shrink-0 text-accent" />}
            {item.shortcut && <span className="text-[11px] text-muted">{item.shortcut}</span>}
          </button>
        ),
      )}
    </div>
  )
}

