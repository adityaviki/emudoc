import { describe, expect, it, vi } from 'vitest'
import { GAP, MARGIN, offerTour, placeBeside, takeTour, workspaceTour } from './tour.ts'

const VIEWPORT = { width: 1280, height: 800 }
const CARD = { width: 320, height: 160 }

describe('the tour', () => {
  it('points at the workspace left to right, then at the header', () => {
    expect(workspaceTour(false).map((step) => step.target)).toEqual([
      'library',
      'upload',
      'chat',
      'organization',
      'search',
      'notifications',
      'account',
    ])
    expect(workspaceTour(true)[0].body).toMatch(/^The sample’s documents/)
  })

  it('puts a card below what it points at, lined up with its start and kept inside the viewport', () => {
    const button = { left: 1240, top: 10, width: 28, height: 28 }
    expect(placeBeside(button, CARD, VIEWPORT)).toEqual({ left: VIEWPORT.width - CARD.width - MARGIN, top: 38 + GAP })
  })

  it('puts it above when there is no room below', () => {
    const composer = { left: 700, top: 700, width: 500, height: 80 }
    expect(placeBeside(composer, CARD, VIEWPORT)).toEqual({ left: 700, top: 700 - GAP - CARD.height })
  })

  it('puts it beside a pane as tall as the viewport, to its right, else to its left', () => {
    const library = { left: 0, top: 48, width: 700, height: 752 }
    expect(placeBeside(library, CARD, VIEWPORT)).toEqual({ left: 700 + GAP, top: 48 })
    const chat = { left: 700, top: 48, width: 580, height: 752 }
    expect(placeBeside(chat, CARD, VIEWPORT)).toEqual({ left: 700 - GAP - CARD.width, top: 48 })
  })

  it('puts it over the middle of what fills the viewport', () => {
    const everything = { left: 0, top: 0, width: 390, height: 800 }
    expect(placeBeside(everything, CARD, { width: 390, height: 800 })).toEqual({ left: 35, top: 320 })
  })

  it('starts from the user menu in the workspace that offers it, and nowhere once it is gone', () => {
    const start = vi.fn()
    const withdraw = offerTour(start)
    takeTour()
    expect(start).toHaveBeenCalledTimes(1)
    withdraw()
    takeTour()
    expect(start).toHaveBeenCalledTimes(1)
  })
})
