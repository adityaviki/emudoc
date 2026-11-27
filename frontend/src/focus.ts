// Where the keyboard goes when it leaves something, or what it was on goes away: the rules the
// components that move the focus follow (components/Menu.tsx, workspace/Library.tsx).

/** The button that shows and hides the library's project files under its search (workspace/Library.tsx). */
export const PROJECT_FILES_TOGGLE = 'project-files-toggle'
/** The button that shows and hides the member's My files above them (workspace/MyFiles.tsx). */
export const MY_FILES_TOGGLE = 'my-files-toggle'

/**
 * Gives the library the keyboard once what held it went (the last tab closed): its tree, or, while the project's files
 * are hidden or show a page without one (the trash, Email in), the button of their heading.
 */
export function focusLibrary() {
  const tree = window.document.querySelector<HTMLElement>('[role="tree"]')
  if (tree && !tree.closest('[hidden]')) tree.focus()
  else window.document.getElementById(PROJECT_FILES_TOGGLE)?.focus()
}

/**
 * Whether Tab, or Shift+Tab when `backward`, takes the keyboard out of a layer that has `stops`
 * places Tab stops at, the focus on the one at `at` (-1 for none of them, such as a menu's item,
 * which the arrows reach, or the layer itself when `onLayer`). From the layer itself Tab goes in,
 * to its first stop; from its last, or with none, it goes out, as Shift+Tab does from its first.
 */
export function leavesLayer(stops: number, at: number, backward: boolean, onLayer = false): boolean {
  if (backward) return at <= 0
  if (at === -1 && !onLayer) return true
  return at >= stops - 1
}

/**
 * The row a listing's cursor moves to once the rows of `gone` are deleted: the first left after
 * the last of them, else the nearest left before it, else none.
 */
export function cursorAfter(keys: string[], gone: Set<string>): string | null {
  const last = Math.max(-1, ...keys.flatMap((key, index) => (gone.has(key) ? [index] : [])))
  const left = (key: string) => !gone.has(key)
  return keys.slice(last + 1).find(left) ?? keys.slice(0, Math.max(0, last)).findLast(left) ?? null
}
