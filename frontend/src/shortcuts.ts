import { useSyncExternalStore } from 'react'

/**
 * The workspace's keyboard shortcuts (decided 2026-10-25): a new chat, the box a question is asked
 * in, and the list of them, which `WorkspaceShortcuts` (workspace/Shortcuts.tsx) listens for; Stop
 * is the composer's own Escape. None is a single character (WCAG 2.1.4), and none types one, so
 * each works from a field too; none acts while a dialog, an open layer or the tour has the keys.
 */
export type Shortcut = 'newChat' | 'composer' | 'shortcuts'

/** What of a key pressed a shortcut is told by: a `KeyboardEvent` has it all. */
export interface Pressed {
  key: string
  ctrlKey: boolean
  metaKey: boolean
  shiftKey: boolean
  altKey: boolean
}

/** Whether the page runs on Apple's computers, where ⌘ takes Ctrl's place. */
export function onMac(agent = navigator.userAgent): boolean {
  return /Mac/.test(agent)
}

/** The shortcut a key pressed asks for, or null: with ⌘ on a Mac and Ctrl elsewhere, never both. */
export function shortcutOf(pressed: Pressed, mac: boolean): Shortcut | null {
  if (pressed.altKey) return null
  const command = mac ? pressed.metaKey && !pressed.ctrlKey : pressed.ctrlKey && !pressed.metaKey
  if (command && pressed.shiftKey && pressed.key.toLowerCase() === 'o') return 'newChat'
  // Some layouts type the slash with Shift, so it is not asked about.
  if (command && pressed.key === '/') return 'shortcuts'
  if (!pressed.ctrlKey && !pressed.metaKey && pressed.shiftKey && pressed.key === 'Escape') return 'composer'
  return null
}

/** The shortcuts as their list shows them: the keys pressed together, and what they do. */
export function shortcutList(mac: boolean): { keys: string[]; does: string }[] {
  const command = mac ? '⌘' : 'Ctrl'
  return [
    { keys: [command, 'Shift', 'O'], does: 'Start a new chat' },
    { keys: ['Shift', 'Esc'], does: 'Go to the box you ask in' },
    { keys: ['Esc'], does: 'Stop the answer being written, from the box you ask in' },
    { keys: [command, '/'], does: 'Show these shortcuts' },
  ]
}

/** The hint under the box a question is asked in, of where the shortcuts are listed. */
export function listHint(mac: boolean): string {
  return `${mac ? '⌘' : 'Ctrl'}+/ for shortcuts`
}

/** Whether something over the page has the keys: a dialog, a layer such as a menu, or the tour. */
export function keysHeld(): boolean {
  return document.querySelector('dialog[open], [aria-modal="true"], [data-layer]') !== null
}

// Asks for the composer to take the focus, and how many of them a composer took: an ask may come
// before the composer that takes it is drawn, a new chat's, which a navigation draws after the
// composer shown has seen the ask.
let asked = 0
let taken = 0
let forNew = false
const listeners = new Set<() => void>()

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

/** Ask that the box a question is asked in take the focus: the one shown, or a new chat's, drawn next. */
export function askForComposer(which: 'shown' | 'new' = 'shown'): void {
  asked += 1
  forNew = which === 'new'
  for (const listener of listeners) listener()
}

/** The asks so far, for a composer's effect to run on each. */
export function useComposerAsks(): number {
  return useSyncExternalStore(subscribe, () => asked)
}

/**
 * Whether an ask waits for the composer of the chat (null for a new one), which the caller takes:
 * each is taken once, by the first composer it is for drawn after it.
 */
export function takeComposerAsk(chatId: number | null): boolean {
  if (taken === asked || (forNew && chatId != null)) return false
  taken = asked
  return true
}
