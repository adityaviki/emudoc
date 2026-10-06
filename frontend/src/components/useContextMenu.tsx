import { useState } from 'react'
import type { MouseEvent } from 'react'
import { Floating, Menu } from './Menu.tsx'
import type { Anchor, MenuItem } from './Menu.tsx'

interface ContextMenuState {
  anchor: Anchor
  items: MenuItem[]
}

/** Holds one open context menu at a time; render `element` where the menu's owner renders. */
export function useContextMenu() {
  const [state, setState] = useState<ContextMenuState | null>(null)
  const close = () => setState(null)

  return {
    isOpen: state !== null,
    /** Open at the pointer, from a contextmenu event. */
    open(event: MouseEvent, items: MenuItem[]) {
      event.preventDefault()
      event.stopPropagation()
      setState({ anchor: { x: event.clientX, y: event.clientY }, items })
    },
    /** Open below an element, such as a "…" button. */
    openAt(element: Element, items: MenuItem[]) {
      setState({ anchor: element.getBoundingClientRect(), items })
    },
    close,
    element: state && (
      <Floating anchor={state.anchor} onClose={close}>
        <Menu items={state.items} onClose={close} />
      </Floating>
    ),
  }
}
