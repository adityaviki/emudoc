// The short tour of a project's workspace: its steps, each anchored to an element that carries
// `data-tour` with the step's target, and where its card goes beside that element. The tour
// itself is components/Tour.tsx; the workspace starts it the first time a member opens a
// project, and the user menu offers it again from inside one (`takeTour`).

import { ACCEPTED_TYPES_LABEL } from './api.ts'

export interface TourStep {
  /** The `data-tour` value of the element the step points at; a step whose element is not shown is left out. */
  target: string
  title: string
  body: string
}

/** The workspace's steps, left to right and then the header's; `sample` words the library for the sample project. */
export function workspaceTour(sample: boolean): TourStep[] {
  return [
    {
      target: 'library',
      title: 'The library',
      body: sample
        ? 'The sample’s documents, indexed already. Open one to read it beside its outline and the facts found in it.'
        : 'The project’s documents and folders. Open one to read it beside its outline and the facts found in it; the ring beside each fills as it is indexed.',
    },
    {
      target: 'upload',
      title: 'Upload',
      body: `Add files here, or drop them anywhere on the library: ${ACCEPTED_TYPES_LABEL}. Each is scanned, read, outlined and indexed in the background.`,
    },
    {
      target: 'chat',
      title: 'Ask',
      body: 'Ask about the documents. An answer cites the passages it relies on, and each citation opens its document there: check them, since a model can be wrong.',
    },
    {
      target: 'organization',
      title: 'Your organisations',
      body: 'The organisation you work in. Switch to another of yours here, or start a new one.',
    },
    {
      target: 'search',
      title: 'Search',
      body: 'Find passages across the project by their words and by what they mean. The results open in a tab beside your documents; the library’s own box filters its files by name.',
    },
    {
      target: 'notifications',
      title: 'Notices',
      body: 'Told here: an upload indexed while you were away, or the month’s pages or questions running low.',
    },
    {
      target: 'account',
      title: 'Your account',
      body: 'Your account, the organisation’s members and plan, and this tour again whenever you want it.',
    },
  ]
}

export interface Box {
  left: number
  top: number
  width: number
  height: number
}

/** How far a card keeps from the window's edges and from what it points at. */
export const MARGIN = 8
export const GAP = 12

/**
 * Where a card goes beside `target` in a viewport of the given size:
 * below it, else above, else to its right, else to its left, the first side with room, lined up
 * with the target's start and kept inside the viewport; over the target's middle when no side
 * has room, as for a pane that fills the viewport.
 */
export function placeBeside(
  target: Box,
  card: { width: number; height: number },
  viewport: { width: number; height: number },
): { left: number; top: number } {
  const fitsX = (left: number) => left >= MARGIN && left + card.width <= viewport.width - MARGIN
  const fitsY = (top: number) => top >= MARGIN && top + card.height <= viewport.height - MARGIN
  const clampX = (left: number) => Math.max(MARGIN, Math.min(left, viewport.width - card.width - MARGIN))
  const clampY = (top: number) => Math.max(MARGIN, Math.min(top, viewport.height - card.height - MARGIN))

  const below = target.top + target.height + GAP
  if (fitsY(below)) return { left: clampX(target.left), top: below }
  const above = target.top - GAP - card.height
  if (fitsY(above)) return { left: clampX(target.left), top: above }
  const right = target.left + target.width + GAP
  if (fitsX(right)) return { left: right, top: clampY(target.top) }
  const left = target.left - GAP - card.width
  if (fitsX(left)) return { left, top: clampY(target.top) }
  return {
    left: clampX(target.left + (target.width - card.width) / 2),
    top: clampY(target.top + (target.height - card.height) / 2),
  }
}

// The workspace that is open, which the user menu's "Take the tour" asks to start it.
let start: (() => void) | null = null

/** Let `takeTour` start the tour here, until the returned function is called. */
export function offerTour(handler: () => void): () => void {
  start = handler
  return () => {
    if (start === handler) start = null
  }
}

/** Start the tour of the open workspace, if one is open. */
export function takeTour() {
  start?.()
}
