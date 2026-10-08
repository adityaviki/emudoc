// Floating layers (menus, popovers) rendered into document.body and kept inside the viewport,
// and the menus built on them: a context menu with keyboard navigation.

import type { LucideIcon } from 'lucide-react'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { KeyboardEvent, ReactNode, RefObject } from 'react'
import { createPortal } from 'react-dom'

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
  ignore?: RefObject<Element | null>
  className?: string
}

export function Floating({ anchor, onClose, children, alignEnd, ignore, className = '' }: FloatingProps) {
  const ref = useRef<HTMLDivElement>(null)
  const [position, setPosition] = useState<{ left: number; top: number } | null>(null)

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
    window.addEventListener('pointerdown', onPointerDown, true)
    window.addEventListener('keydown', onKeyDown)
    window.addEventListener('blur', onClose)
    window.addEventListener('resize', onClose)
    window.addEventListener('scroll', onClose, true)
    return () => {
      window.removeEventListener('pointerdown', onPointerDown, true)
      window.removeEventListener('keydown', onKeyDown)
      window.removeEventListener('blur', onClose)
      window.removeEventListener('resize', onClose)
      window.removeEventListener('scroll', onClose, true)
    }
  }, [onClose, ignore])

  return createPortal(
    <div
      ref={ref}
      // Scroll events inside the layer must not close it: stop them before the window hears them.
      onScroll={(event) => event.stopPropagation()}
      style={position ? { left: position.left, top: position.top } : { left: 0, top: 0, visibility: 'hidden' }}
      className={`fixed z-50 overflow-hidden rounded-lg border border-line bg-surface shadow-xl shadow-ink/8 ${className}`}
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
      onSelect: () => void
    }
  | 'separator'

interface MenuProps {
  items: MenuItem[]
  onClose: () => void
}

/** A list of actions. Arrow keys move, Enter or a click runs one; every choice closes the menu. */
export function Menu({ items, onClose }: MenuProps) {
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    ref.current?.querySelector<HTMLButtonElement>('button:not(:disabled)')?.focus()
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
            role="menuitem"
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
            <span className="flex-1">{item.label}</span>
            {item.shortcut && <span className="text-[11px] text-muted">{item.shortcut}</span>}
          </button>
        ),
      )}
    </div>
  )
}

