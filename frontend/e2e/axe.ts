import AxeBuilder from '@axe-core/playwright'
import { expect } from '@playwright/test'
import type { Page } from '@playwright/test'

// axe-core's rules for WCAG 2.2's levels A and AA, which the pages are held to. Its best
// practices are left out: a menu, laid over the page outside its landmarks, breaks one.
const WCAG = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']

/**
 * Checks the page as it stands against axe-core's rules for WCAG 2.2 AA, in the light theme and
 * then the dark, and fails with every violation and the elements it was found on. The theme is
 * put back as it was, and nothing is saved, as the header's toggle would.
 */
export async function expectAccessible(page: Page, where: string): Promise<void> {
  const before = await page.evaluate(`document.documentElement.dataset.theme ?? ''`)
  for (const theme of ['light', 'dark']) {
    await page.evaluate(`document.documentElement.dataset.theme = '${theme}'`)
    // A colour measured while the theme's transition runs is neither theme's.
    await page.evaluate(
      `Promise.allSettled(document.getAnimations().filter((animation) => animation instanceof CSSTransition).map((animation) => animation.finished))`,
    )
    const { violations } = await new AxeBuilder({ page }).withTags(WCAG).analyze()
    const found = violations.map(
      (violation) => `${violation.id} (${violation.impact}): ${violation.help}, at ${violation.nodes.map((node) => node.target.join(' ')).join('; ')}`,
    )
    expect(found, `What axe-core finds on ${where} in the ${theme} theme`).toEqual([])
  }
  await page.evaluate(before ? `document.documentElement.dataset.theme = '${before}'` : `delete document.documentElement.dataset.theme`)
}
