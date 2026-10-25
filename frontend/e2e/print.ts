import type { Page } from '@playwright/test'

/**
 * What Chromium prints of the page: `page.pdf`, which fires `beforeprint` and `afterprint` as
 * Ctrl+P does, and waits for the lazy images as a print does; how many sheets it took, and the
 * page's title while it printed, which names a PDF saved from the print.
 */
export async function printed(page: Page): Promise<{ sheets: number; title: string | undefined }> {
  const pdf = (await page.pdf()).toString('latin1')
  return { sheets: pdf.match(/\/Type\s*\/Page\b/g)?.length ?? 0, title: /\/Title\s*\(([^)]*)\)/.exec(pdf)?.[1] }
}
