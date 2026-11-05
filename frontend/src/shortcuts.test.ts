import { describe, expect, it } from 'vitest'
import { askForComposer, listHint, onMac, shortcutList, shortcutOf, tabMoveKeys, tabMoveOf, takeComposerAsk } from './shortcuts.ts'
import type { Pressed } from './shortcuts.ts'

const key = (key: string, held: Partial<Pressed> = {}): Pressed => ({
  key,
  ctrlKey: false,
  metaKey: false,
  shiftKey: false,
  altKey: false,
  ...held,
})

describe('shortcutOf', () => {
  it('hears Ctrl elsewhere and ⌘ on a Mac, never both', () => {
    expect(shortcutOf(key('O', { ctrlKey: true, shiftKey: true }), false)).toBe('newChat')
    expect(shortcutOf(key('O', { metaKey: true, shiftKey: true }), true)).toBe('newChat')
    expect(shortcutOf(key('O', { metaKey: true, shiftKey: true }), false)).toBeNull()
    expect(shortcutOf(key('O', { ctrlKey: true, shiftKey: true }), true)).toBeNull()
    expect(shortcutOf(key('O', { ctrlKey: true, metaKey: true, shiftKey: true }), false)).toBeNull()
  })

  it('wants every key of a shortcut, and no other', () => {
    // Ctrl+O alone is the browser's, and Alt changes what a key types on some layouts.
    expect(shortcutOf(key('o', { ctrlKey: true }), false)).toBeNull()
    expect(shortcutOf(key('O', { ctrlKey: true, shiftKey: true, altKey: true }), false)).toBeNull()
    expect(shortcutOf(key('/', { ctrlKey: true }), false)).toBe('shortcuts')
    // A layout that types the slash with Shift.
    expect(shortcutOf(key('/', { ctrlKey: true, shiftKey: true }), false)).toBe('shortcuts')
    expect(shortcutOf(key('Escape', { shiftKey: true }), false)).toBe('composer')
    expect(shortcutOf(key('Escape', { shiftKey: true, ctrlKey: true }), false)).toBeNull()
  })

  it('takes no key alone, which a field would type or a layer closes on', () => {
    for (const alone of ['o', 'O', '/', '?', 'Escape', 'n']) expect(shortcutOf(key(alone), false)).toBeNull()
  })
})

describe('tabMoveOf', () => {
  it('moves a tab by Ctrl and Shift with an arrow, ⌘ on a Mac, never both', () => {
    expect(tabMoveOf(key('ArrowLeft', { ctrlKey: true, shiftKey: true }), false)).toBe(-1)
    expect(tabMoveOf(key('ArrowRight', { ctrlKey: true, shiftKey: true }), false)).toBe(1)
    expect(tabMoveOf(key('ArrowRight', { metaKey: true, shiftKey: true }), true)).toBe(1)
    expect(tabMoveOf(key('ArrowRight', { metaKey: true, shiftKey: true }), false)).toBeNull()
    expect(tabMoveOf(key('ArrowRight', { ctrlKey: true, shiftKey: true }), true)).toBeNull()
    expect(tabMoveOf(key('ArrowRight', { ctrlKey: true, metaKey: true, shiftKey: true }), false)).toBeNull()
  })

  it('leaves the arrows alone, and with fewer or more keys, to show another tab', () => {
    expect(tabMoveOf(key('ArrowLeft'), false)).toBeNull()
    expect(tabMoveOf(key('ArrowLeft', { ctrlKey: true }), false)).toBeNull()
    expect(tabMoveOf(key('ArrowLeft', { shiftKey: true }), false)).toBeNull()
    expect(tabMoveOf(key('ArrowLeft', { ctrlKey: true, shiftKey: true, altKey: true }), false)).toBeNull()
    expect(tabMoveOf(key('Home', { ctrlKey: true, shiftKey: true }), false)).toBeNull()
  })

  it('names the keys for the menu and for screen readers', () => {
    expect(tabMoveKeys(false)).toEqual({
      left: 'Ctrl+Shift+←',
      right: 'Ctrl+Shift+→',
      named: 'Control+Shift+ArrowLeft Control+Shift+ArrowRight',
    })
    expect(tabMoveKeys(true).left).toBe('⌘+Shift+←')
    expect(tabMoveKeys(true).named).toBe('Meta+Shift+ArrowLeft Meta+Shift+ArrowRight')
  })
})

describe('the words', () => {
  it('name the keys of the computer the page runs on', () => {
    expect(shortcutList(false)[0]).toEqual({ keys: ['Ctrl', 'Shift', 'O'], does: 'Start a new chat' })
    expect(shortcutList(true).map((shortcut) => shortcut.keys.join('+'))).toEqual(['⌘+Shift+O', 'Shift+Esc', 'Esc', '⌘+/'])
    expect(listHint(false)).toBe('Ctrl+/ for shortcuts')
    expect(listHint(true)).toBe('⌘+/ for shortcuts')
  })

  it("tell a Mac by its browser's agent", () => {
    expect(onMac('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15')).toBe(true)
    expect(onMac('Mozilla/5.0 (iPad; CPU OS 18_0 like Mac OS X)')).toBe(true)
    expect(onMac('Mozilla/5.0 (X11; Linux x86_64) Gecko/20100101 Firefox/144.0')).toBe(false)
    expect(onMac('Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/141.0.0.0')).toBe(false)
  })
})

describe('the composer asked for', () => {
  it('takes each ask once, however many composers look', () => {
    expect(takeComposerAsk(4)).toBe(false)
    askForComposer()
    askForComposer()
    expect(takeComposerAsk(4)).toBe(true)
    expect(takeComposerAsk(4)).toBe(false)
    askForComposer()
    expect(takeComposerAsk(null)).toBe(true)
  })

  it("leaves a new chat's ask to the new chat's composer, drawn after the one shown", () => {
    askForComposer('new')
    expect(takeComposerAsk(4)).toBe(false)
    expect(takeComposerAsk(null)).toBe(true)
    expect(takeComposerAsk(null)).toBe(false)
  })
})
