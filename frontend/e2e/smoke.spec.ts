import { createHmac } from 'node:crypto'
import { mkdtemp, readFile, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { expect, test } from '@playwright/test'
import type { BrowserContext, Page } from '@playwright/test'
import { expectAccessible } from './axe.ts'
import { INBOUND_DOMAIN, receiveMail } from './maintenance.ts'
import { printed } from './print.ts'
import { STANDIN, heldTurns, holdNextTurn, hooksReceived, refuseEmbeddings, releaseTurns } from './standin.ts'

// The member's way through the app, end to end: set the install up on the page its first visit opens, find the sign-in
// a visitor is sent to from /, and the sign-up page, which says whom to ask, skip the tour the first project shows, ask the sample
// project the organisation starts with whether an invoice applies its contract's discount, print its contract's pages, make a project, upload a contract,
// begun offline and waiting for the connection, wait for it to be indexed, find it by searching, ask about it, stop the
// answer while the model thinks and try it again, open the passage the answer
// cites and print the contract as it reads, upload a folder,
// whose panel follows each file while its request is held, and put it in the trash, export the project,
// download it and open the bell's notice that it is ready, leave the account's menu by the
// keyboard, choose the workspace's settings, read them in its audit log and download it, invite a colleague as a
// viewer from its Invitations, who makes an account from the invitation's link, copied since the run mails nothing, ends her tour, finds
// the project and is offered no change to it until made a member, then find in her help menu, the run mailing nothing,
// the workspace's admin to write to, by a mail that carries the page, read the help pages from the menu, filled in
// from what the run does, and follow an article's link into the app, make a read key, which reads the
// versioned API and its contract, add a webhook, then make the project private, which she no longer finds until chosen for it, and share the chat, which the
// colleague then reads but does not ask in; then read when each member was last seen, and on the workspace's
// Administration page its jobs, services and costs, and switch the model's provider off and on
// again; and last, upload an invoice, whose event is posted to the webhook, signed, and its
// delivery logged, and a second invoice of the same order, a Word
// document and an Excel workbook the agent writes into My files when asked in a chat, and last
// the project given an address, a supplier's domain listed, whose mail becomes documents and a
// stranger's is turned away, as the page lists them, and a folder of the member's Google Drive
// connected and synced, and last the invoice shared by a link with a password, which someone with
// no account opens and prints and its member revokes, and an answer about it shared, its citation leading the
// visitor to the passage, and last a chat pinned to the top of the list and unpinned, the shortcuts listed, a new
// chat started, its box reached and an answer stopped by the keyboard. The answer is the stand-in's (e2e/standin.py):
// the first sentence of the passage its search found best, with that passage's reference. Along the way each kind of
// page, panel and dialog is checked against axe-core's rules for WCAG 2.2 AA in both themes
// (e2e/axe.ts), and the keyboard's focus is followed where something it was on goes away.
const CONTRACT = join(import.meta.dirname, 'fixtures', 'service-contract.md')
// An invoice, which the stand-in takes for one by its opening words, so that the search filters it
// by its type, and whose delivery period a search finds by "March".
const INVOICE = join(import.meta.dirname, 'fixtures', 'invoice.md')
// A second invoice of the same order, 120 EUR over its total, which a chat compares with the first.
const SECOND_INVOICE = join(import.meta.dirname, 'fixtures', 'second-invoice.md')
// A third invoice, which is moved into a folder of its own.
const THIRD_INVOICE = join(import.meta.dirname, 'fixtures', 'third-invoice.md')
// A folder of two delivery notes, one of them in a folder of its own.
const DELIVERIES = join(import.meta.dirname, 'fixtures', 'deliveries')
// The project the workspace the setup makes starts with (backend/app/sample).
const SAMPLE = 'Sample: Acme supplier contract'

/** "New folder" from the menu of the listing's empty space, under its last row, where its own padding is. */
async function newFolder(page: Page, name: string) {
  const files = page.getByRole('tree', { name: 'Files' })
  const box = await files.boundingBox()
  if (!box) throw new Error('The listing is not shown.')
  await files.click({ button: 'right', position: { x: box.width / 2, y: box.height - 4 } })
  await page.getByRole('menuitem', { name: 'New folder' }).click()
  await page.getByLabel('New name').fill(name)
  await page.getByLabel('New name').press('Enter')
}

test('a new member uploads a contract, finds it, asks about it and opens the cited passage', async ({ page, browser, request }) => {
  // About 3.5 minutes on a machine of its own (2026-10-24, with the connected folder's step);
  // CI's runners once shared one, where it took 5 beside the backend suites and an image build,
  // and ran out of the 5 it had then.
  test.setTimeout(480_000)
  // The colleague's browser, from the invitation on.
  let theirs: BrowserContext
  let grace: Page
  // The first member's address.
  let address = ''
  // The organisation page's webhooks, and the secret of the one added, which signs its events.
  const webhooks = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Webhooks', exact: true }) })
  let hookSecret = ''

  await test.step('set the install up on the page its first visit opens', async () => {
    address = `ada.${Date.now()}@example.com`
    // A new install has no account, so every page sends to its setup, / too.
    await page.goto('/')
    await expect(page).toHaveURL(/\/setup$/)
    await page.goto('/projects/1')
    await expect(page).toHaveURL(/\/setup$/)
    await expect(page.getByRole('heading', { name: 'Set up Emudoc' })).toBeVisible()
    await expectAccessible(page, 'the setup page')
    await page.getByLabel('Workspace name').fill('Analytical Engines')
    await page.getByLabel('Your name').fill('Ada Lovelace')
    await page.getByLabel('Email', { exact: true }).fill(address)
    await page.getByLabel('Password', { exact: true }).fill('a sentence to remember')
    await page.getByRole('button', { name: 'Create the workspace' }).click()
    await expect(page.getByRole('heading', { name: 'Projects' })).toBeVisible()
    await expect(page.getByText(SAMPLE)).toBeVisible()
    await expectAccessible(page, 'the projects page')
  })

  await test.step('find the sign-in at / as a visitor, and the sign-up page, which says whom to ask', async () => {
    // A browser of nobody's: / is the app, which sends a visitor to its sign-in.
    const visit = await browser.newContext()
    const visitor = await visit.newPage()
    await visitor.goto('/')
    await expect(visitor).toHaveURL(/\/login$/)
    await expect(visitor.getByRole('heading', { name: 'Sign in' })).toBeVisible()
    // Sign-up is closed, as an install starts: no way to an account but an invitation.
    await expect(visitor.getByText("New to Emudoc? Ask your team's admin for an invitation.")).toBeVisible()
    await expect(visitor.locator('a[href^="/signup"]')).toHaveCount(0)
    await expectAccessible(visitor, 'the sign-in page')
    await visitor.goto('/signup')
    await expect(visitor.getByRole('heading', { name: 'Accounts are made by invitation' })).toBeVisible()
    await expectAccessible(visitor, 'the sign-up page')
    // The logo leads to /, the sign-in again for a visitor.
    await visitor.getByRole('link', { name: 'Emudoc' }).click()
    await expect(visitor).toHaveURL(/\/login$/)
    await visit.close()
  })

  await test.step('skip the tour, and ask the sample project whether an invoice applies the contract\'s discount', async () => {
    await page.getByText(SAMPLE).click()
    await expect(page).toHaveURL(/\/projects\/\d+$/)
    // The first project opened shows the tour: the sample takes no uploads, so it has no card.
    const tour = page.getByRole('dialog')
    await expect(tour).toContainText('1 of 6')
    await expect(tour).toContainText('The library')
    await expect(page.getByRole('button', { name: 'Next', exact: true })).toBeFocused()
    await expectAccessible(page, 'the tour')
    await page.getByRole('button', { name: 'Next', exact: true }).click()
    // Then the library's search box, under the library's buttons.
    await expect(tour).toContainText('2 of 6')
    await expect(tour).toContainText('Find files by their names')
    const seen = page.waitForResponse((response) => response.url().endsWith('/api/auth/onboarding'))
    await page.getByRole('button', { name: 'Skip the tour' }).click()
    expect((await seen).status()).toBe(200)
    await expect(tour).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Upload', exact: true })).toHaveCount(0)
    // Indexed already: the job each document's copy queued makes its vectors alone.
    const ready = page.getByRole('img', { name: /Embeddings: done/ })
    await expect(ready).toHaveCount(8, { timeout: 30_000 })
    await expect(ready.first()).toHaveAccessibleName(/Summaries and facts: done/)
    const composer = page.getByPlaceholder('Ask a question about these documents')
    await composer.fill('Does invoice INV-2026-0503 apply the volume discount the contract sets?')
    await composer.press('Enter')
    await expect(page).toHaveURL(/[?&]chat=\d+/)
    await expect(page.getByRole('button', { name: '1', exact: true })).toBeVisible({ timeout: 30_000 })
    await expect(page.getByText('Thinking')).toHaveCount(0)
    await expectAccessible(page, 'a project with an answer')
    // Printed, a PDF is its pages alone, a sheet each, under the document's name, the app's again after.
    await page.getByRole('treeitem', { name: 'acme-service-contract.pdf' }).click()
    await expect(page.getByRole('heading', { name: 'Master Services Agreement', exact: true })).toBeVisible()
    await expect(page.getByRole('img', { name: 'Page 1' })).toBeVisible()
    expect(await printed(page)).toEqual({ sheets: 2, title: 'Master Services Agreement' })
    await expect(page).toHaveTitle('Emudoc')
    const pdfTab = page.getByRole('tab', { name: /^acme-service-contract\.pdf/ })
    // A second document's tab, dragged before the first, is moved back by Ctrl+Shift and an arrow.
    await page.getByRole('treeitem', { name: 'price-list.csv' }).click()
    const csvTab = page.getByRole('tab', { name: /^price-list\.csv/ })
    await expect(csvTab).toHaveAttribute('aria-selected', 'true')
    const tabs = page.getByRole('tablist', { name: 'Open documents' }).getByRole('tab')
    await expect(tabs).toHaveText(['acme-service-contract.pdf', 'price-list.csv'])
    await csvTab.dragTo(pdfTab, { targetPosition: { x: 4, y: 8 } })
    await expect(tabs).toHaveText(['price-list.csv', 'acme-service-contract.pdf'])
    await csvTab.focus()
    await page.keyboard.press('ControlOrMeta+Shift+ArrowRight')
    await expect(tabs).toHaveText(['acme-service-contract.pdf', 'price-list.csv'])
    await expect(csvTab).toBeFocused()
    // Pinned from its menu, it goes first and stays open through "Close all"; its pin unpins it.
    await csvTab.click({ button: 'right' })
    await page.getByRole('menuitem', { name: 'Pin', exact: true }).click()
    await expect(tabs).toHaveText(['price-list.csv, pinned', 'acme-service-contract.pdf'])
    await expectAccessible(page, 'a pinned tab')
    await csvTab.click({ button: 'right' })
    await page.getByRole('menuitem', { name: 'Close all', exact: true }).click()
    await expect(pdfTab).toHaveCount(0)
    await expect(tabs).toHaveText(['price-list.csv, pinned'])
    await csvTab.getByTitle('Unpin tab').click()
    await expect(tabs).toHaveText(['price-list.csv'])
    // Closed by its cross, a tab leaves the others as wide as they were while the mouse stays on
    // the strip, so the next one's cross comes under it; they share the strip again once it leaves.
    await page.getByRole('treeitem', { name: 'acme-service-contract.pdf' }).click()
    await expect(tabs).toHaveText(['price-list.csv', 'acme-service-contract.pdf'])
    await csvTab.hover()
    await csvTab.getByTitle('Close tab').click()
    await expect(tabs).toHaveText(['acme-service-contract.pdf'])
    await expect(pdfTab).toHaveAttribute('style', /width/)
    await page.mouse.move(0, 0)
    await expect(pdfTab).not.toHaveAttribute('style', /width/)
    await pdfTab.focus()
    await page.keyboard.press('Delete')
    await expect(pdfTab).toHaveCount(0)
    // A menu takes the keyboard to its first item, and Escape gives it back to its button.
    const account = page.getByRole('button', { name: 'AL, your account' })
    await account.click()
    await expect(page.getByRole('menuitem', { name: 'Account and security' })).toBeFocused()
    await expectAccessible(page, 'the user menu')
    await page.keyboard.press('Escape')
    await expect(page.getByRole('menu')).toHaveCount(0)
    await expect(account).toBeFocused()
    await page.getByRole('link', { name: 'Emudoc' }).click()
    await expect(page.getByRole('heading', { name: 'Projects' })).toBeVisible()
  })

  await test.step('make a project', async () => {
    // Asked for less motion, nothing fades from one colour to another.
    const fades = `getComputedStyle(document.querySelector('header button')).transitionDuration`
    expect(await page.evaluate(fades)).not.toBe('0s')
    await page.emulateMedia({ reducedMotion: 'reduce' })
    expect(await page.evaluate(fades)).toBe('0s')
    await page.emulateMedia({ reducedMotion: null })
    await page.getByRole('button', { name: 'New project' }).click()
    await page.getByLabel('Name', { exact: true }).fill('Supplier contracts')
    await page.getByRole('button', { name: 'Create project' }).click()
    await expect(page).toHaveURL(/\/projects\/\d+$/)
  })

  await test.step('upload the contract, begun offline, and wait for it to be indexed', async () => {
    // Offline, the upload waits for the connection and says so, as the header does.
    const offline = page.getByRole('status').filter({ hasText: 'Offline' })
    // The project must have loaded first: offline, its query waits for the connection and the
    // library, with the upload's input, is never drawn.
    await expect(page.locator('input[name="files"]')).toBeAttached()
    await page.context().setOffline(true)
    await expect(offline).toBeVisible()
    await page.locator('input[name="files"]').setInputFiles(CONTRACT)
    await expect(page.getByText('Your browser is offline: the upload of 1 file starts once the connection is back.')).toBeVisible()
    await page.context().setOffline(false)
    await expect(offline).toHaveCount(0)
    // Every stage done but the malware scan, which has no scanner here.
    const status = page.getByRole('img', { name: /^Ready/ })
    await expect(status).toBeVisible({ timeout: 60_000 })
    for (const stage of ['Parse', 'Outline', 'Summaries and facts', 'Embeddings']) {
      await expect(status).toHaveAccessibleName(new RegExp(`${stage}: done`))
    }
    await expect(status).toHaveAccessibleName(/Malware scan: skipped/)
    await expectAccessible(page, 'the library with a document indexed')
    // The divider between the panes moves by the keyboard as well as by a drag.
    const divider = page.getByRole('separator', { name: 'Library' })
    const share = Number(await divider.getAttribute('aria-valuenow'))
    await divider.focus()
    await page.keyboard.press('ArrowRight')
    await expect(divider).toHaveAttribute('aria-valuenow', String(share + 2))
    await page.keyboard.press('ArrowLeft')
    await expect(divider).toHaveAttribute('aria-valuenow', String(share))
    // A click offers buttons beside it that move it without a drag, the keyboard left on it.
    await divider.click()
    const wider = page.getByRole('button', { name: 'Widen the library' })
    await wider.click()
    await expect(divider).toHaveAttribute('aria-valuenow', String(share + 10))
    await expectAccessible(page, "the divider's buttons")
    await page.getByRole('button', { name: 'Narrow the library' }).click()
    await expect(divider).toHaveAttribute('aria-valuenow', String(share))
    await expect(divider).toBeFocused()
    await page.keyboard.press('Escape')
    await expect(wider).toHaveCount(0)
    await expect(divider).toBeFocused()
    // Seen once, the tour does not come back.
    await expect(page.getByRole('dialog')).toHaveCount(0)
  })

  await test.step('search the project from the library, its files first and its passages below, and open a hit beside them', async () => {
    const box = page.getByLabel('Search the project')
    await box.fill('written notice')
    await expect(page).toHaveURL(/\/projects\/\d+\?q=written\+notice/)
    await expect(page.getByText('No file’s name holds “written notice”.')).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Passages' })).toBeVisible()
    const hit = page.getByRole('button', { name: /Service contract/ })
    await expect(hit.first().locator('mark', { hasText: /^notice$/i }).first()).toBeVisible()
    await expectAccessible(page, 'the search in the library')
    // A hit opens its document in a tab of the middle pane, the search staying in the library beside it.
    await hit.first().click()
    await expect(page).toHaveURL(/\/documents\/\d+\?(.*&)?passage=\d+/)
    await expect(page).toHaveURL(/[?&]q=written\+notice/)
    const documentTab = page.getByRole('tab', { name: /^service-contract\.md/ })
    await expect(documentTab).toHaveAttribute('aria-selected', 'true')
    await expect(hit.first()).toBeVisible()
    // A file whose name holds the words comes first, a row of the library's above the passages.
    await box.fill('CONTRACT')
    await expect(page).toHaveURL(/[?&]q=CONTRACT/)
    await expect(page.getByRole('heading', { name: 'Files', exact: true })).toBeVisible()
    await expect(page.getByRole('treeitem', { name: 'service-contract.md', exact: true })).toBeVisible()
    await expect(hit.first()).toBeVisible()
    await page.getByRole('button', { name: 'Clear search' }).click()
    await expect(page).not.toHaveURL(/[?&]q=/)
    await expect(box).toBeFocused()
    await expect(page.getByRole('heading', { name: 'Passages' })).toHaveCount(0)
    // The last tab closed by its key takes the middle pane with it, and the tree the focus.
    await documentTab.focus()
    await page.keyboard.press('Delete')
    await expect(documentTab).toHaveCount(0)
    await expect(page.getByRole('tree', { name: 'Files' })).toBeFocused()
  })

  await test.step('ask, stop the answer while the model thinks, try it again and get an answer that cites the contract', async () => {
    const composer = page.getByPlaceholder('Ask a question about these documents')
    await holdNextTurn(request)
    await composer.fill('How much notice does ending the contract take?')
    await composer.press('Enter')
    await expect(page).toHaveURL(/[?&]chat=\d+/)
    await expect(page.getByText('Thinking')).toBeVisible()
    // Stopped once its turn waits on the model: stopped before, it would leave the hold to the try.
    await expect.poll(() => heldTurns(request)).toBe(1)
    await page.getByRole('button', { name: 'Stop', exact: true }).click()
    await expect(page.getByText('Stopped.', { exact: true })).toBeVisible()
    // Tried again in its place, which counts no question. The held turn answers after, into a try
    // that is no longer its own.
    await page.getByRole('button', { name: 'Try again' }).click()
    await expect(page.getByText('Stopped.', { exact: true })).toHaveCount(0)
    const citation = page.getByRole('button', { name: '1', exact: true })
    await expect(citation).toBeVisible({ timeout: 30_000 })
    await expect(citation).toHaveAttribute('title', 'Service contract · 4 Termination')
    await expect(page.getByText(/Searched for "How much notice/)).toBeVisible()
    await expect(page.getByText("Either party may end this contract by giving ninety days' written notice to the other.")).toBeVisible()
    await expect(page.getByText('Thinking')).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Try again' })).toHaveCount(0)
    await releaseTurns(request)
  })

  await test.step('open the cited passage', async () => {
    await page.getByRole('button', { name: '1', exact: true }).click()
    await expect(page).toHaveURL(/\/documents\/\d+\?(.*&)?passage=\d+/)
    await expect(page.getByRole('heading', { name: 'Service contract', exact: true })).toBeVisible()
    await expect(page.getByText('Showing: 4 Termination')).toBeVisible()
    await expect(page.getByText(/^Either party may end this contract .* on the first page\.$/)).toBeInViewport()
    await expectAccessible(page, 'a document beside its chat')
    // Printed, it is the document alone, as it reads: none of the app around it and none of its
    // controls, its highlight still saying what it shows, and its text, which scrolls in its pane
    // on a short window, running on instead.
    const text = page.getByRole('region', { name: 'Text of service-contract.md' })
    const around = [
      page.getByRole('banner'),
      page.getByRole('tree', { name: 'Files' }),
      page.getByRole('tablist', { name: 'Open documents' }),
      page.getByPlaceholder('Ask a question about these documents'),
      page.getByRole('button', { name: 'Clear highlight' }),
      page.getByRole('button', { name: 'Print', exact: true }),
    ]
    const viewport = page.viewportSize()!
    await page.setViewportSize({ width: viewport.width, height: 400 })
    for (const part of around) await expect(part).toBeVisible()
    expect(await text.evaluate((region) => region.scrollHeight > region.clientHeight)).toBe(true)
    await page.emulateMedia({ media: 'print' })
    for (const part of around) await expect(part).toBeHidden()
    await expect(page.getByRole('heading', { name: 'Service contract', exact: true })).toBeVisible()
    await expect(page.getByText('Showing: 4 Termination')).toBeVisible()
    expect(await text.evaluate((region) => region.scrollHeight > region.clientHeight)).toBe(false)
    await page.emulateMedia({ media: null })
    await page.setViewportSize(viewport)
    expect(await printed(page)).toEqual({ sheets: 1, title: 'Service contract' })
    // Its Print button prints it as Ctrl+P does.
    await page.evaluate(`window.print = () => { document.body.dataset.printed = 'yes' }`)
    await page.getByRole('button', { name: 'Print', exact: true }).click()
    await expect(page.locator('body')).toHaveAttribute('data-printed', 'yes')
    // Its tab closes by the keyboard, and the keyboard goes back to the library with the pane.
    const tab = page.getByRole('tab', { name: /^service-contract\.md/ })
    await expect(tab).toHaveAttribute('aria-selected', 'true')
    await tab.focus()
    await page.keyboard.press('Delete')
    await expect(tab).toHaveCount(0)
    await expect(page.getByRole('tree', { name: 'Files' })).toBeFocused()
  })

  await test.step('rename the contract and move it into a folder, undoing each', async () => {
    const contract = page.getByRole('treeitem', { name: /service-contract\.md/ })
    const undo = page.getByRole('button', { name: 'Undo', exact: true })
    await contract.click()
    await page.keyboard.press('F2')
    await page.getByLabel('New name').fill('contract')
    await page.getByLabel('New name').press('Enter')
    await expect(page.getByRole('tree', { name: 'Files' })).toBeFocused()
    // The extension is kept.
    await expect(page.getByText('“service-contract.md” is now called “contract.md”.')).toBeVisible()
    await expect(page.getByRole('treeitem', { name: /(^|\s)contract\.md/ })).toBeVisible()
    await undo.click()
    await expect(contract).toBeVisible()
    await expect(undo).toHaveCount(0)

    await newFolder(page, 'Contracts')
    // The folder first: a drag pressed while it is still being made finds the rows moved under it.
    const folder = page.getByRole('treeitem', { name: /Contracts/ })
    await expect(folder).toBeVisible()
    await contract.dragTo(folder)
    await expect(page.getByText('“service-contract.md” is now in “Contracts”.')).toBeVisible()
    await expect(contract).toHaveCount(0)
    await undo.click()
    await expect(contract).toBeVisible()
    await expect(undo).toHaveCount(0)

    // The same move by the keyboard: Tab from the listing to its row's actions, and the folder
    // chosen in a dialog.
    await contract.click()
    await page.keyboard.press('Tab')
    await expect(contract.getByRole('button', { name: 'More actions' })).toBeFocused()
    await page.keyboard.press('Enter')
    await page.getByRole('menuitem', { name: 'Move to…' }).click()
    const where = page.getByRole('dialog', { name: 'Move “service-contract.md”' })
    await expect(where.getByLabel('Folder')).toBeFocused()
    await expectAccessible(page, 'the dialog that moves files')
    await where.getByLabel('Folder').selectOption({ label: 'Supplier contracts / Contracts' })
    await where.getByRole('button', { name: 'Move', exact: true }).click()
    await expect(page.getByText('“service-contract.md” is now in “Contracts”.')).toBeVisible()
    await expect(page.getByRole('tree', { name: 'Files' })).toBeFocused()
    await undo.click()
    await expect(contract).toBeVisible()
    await expect(undo).toHaveCount(0)
  })

  await test.step("give the contract a new version from its menu, and read the earlier one's text from its tab", async () => {
    const contract = page.getByRole('treeitem', { name: /service-contract\.md/ })
    const signed = `${await readFile(CONTRACT, 'utf8')}\n## 5 Signatures\n\nSigned by both parties on 1 March 2026.\n`
    await contract.click()
    await page.keyboard.press('Tab')
    await page.keyboard.press('Enter')
    const chooser = page.waitForEvent('filechooser')
    await page.getByRole('menuitem', { name: 'Upload a new version…' }).click()
    await (await chooser).setFiles({ name: 'service-contract.md', mimeType: 'text/markdown', buffer: Buffer.from(signed) })
    await expect(page.getByText(/^“service-contract\.md” is now version 2\./)).toBeVisible()
    // Read again from its new file, in its place in the library (its tab, open since the click, says so too).
    await expect(contract.getByRole('img', { name: /^Ready/ })).toBeVisible({ timeout: 60_000 })
    await contract.click()
    await page.keyboard.press('Enter')
    await expect(page.getByRole('heading', { name: 'Service contract', exact: true })).toBeVisible()
    await expect(page.getByText('Signed by both parties on 1 March 2026.')).toBeVisible()
    const opener = page.getByRole('button', { name: 'Versions' })
    await opener.click()
    const versions = page.getByRole('dialog', { name: 'Versions of “service-contract.md”' })
    await expect(versions.getByRole('button', { name: 'Upload a new version' })).toBeFocused()
    await expect(versions.getByText('Version 2, the current one')).toBeVisible()
    await versions.getByRole('button', { name: 'Show the text of version 1' }).click()
    const earlier = versions.getByRole('region', { name: 'Text of version 1' })
    await expect(earlier).toContainText("ninety days' written notice")
    await expect(earlier).not.toContainText('Signed by both parties')
    await expectAccessible(page, "a document's versions")
    await page.keyboard.press('Escape')
    await expect(versions).toHaveCount(0)
    await expect(opener).toBeFocused()
    const tab = page.getByRole('tab', { name: /^service-contract\.md/ })
    await tab.focus()
    await page.keyboard.press('Delete')
    await expect(page.getByRole('tree', { name: 'Files' })).toBeFocused()
  })

  await test.step('delete the contract and the folder, take the contract back from the trash and delete the folder for good', async () => {
    const contract = page.getByRole('treeitem', { name: /service-contract\.md/ })
    const folder = page.getByRole('treeitem', { name: /Contracts/ })
    await contract.click()
    await page.keyboard.press('Delete')
    await expect(contract).toHaveCount(0)
    await expect(page.getByText('“service-contract.md” is in the trash.')).toBeVisible()
    // The keyboard stays in the listing, on the row that is left, which a screen reader reads out.
    const files = page.getByRole('tree', { name: 'Files' })
    await expect(files).toBeFocused()
    await expect(files).toHaveAttribute('aria-activedescendant', (await folder.getAttribute('id')) ?? '')
    await folder.click()
    await page.keyboard.press('Delete')
    await expect(folder).toHaveCount(0)
    await page.getByRole('button', { name: 'Trash', exact: true }).click()
    await expect(page).toHaveURL(/[?&]trash/)
    await page.getByRole('button', { name: 'Restore service-contract.md' }).click()
    const trashed = page.getByRole('list', { name: 'In the trash' })
    await expect(trashed.getByRole('listitem')).toHaveCount(1)
    // Deleting for good asks first, in a dialog that says what goes; Escape keeps it.
    const purge = trashed.getByRole('button', { name: 'Delete Contracts for good' })
    await purge.click()
    const asked = page.getByRole('dialog', { name: 'Delete “Contracts” for good?' })
    await expect(asked).toContainText('The folder “Contracts” is deleted for good with everything in it')
    await expect(asked.getByRole('button', { name: 'Cancel' })).toBeFocused()
    await expectAccessible(page, 'the dialog that asks before deleting for good')
    await page.keyboard.press('Escape')
    await expect(asked).toHaveCount(0)
    await expect(purge).toBeFocused()
    await purge.click()
    await asked.getByRole('button', { name: 'Delete for good' }).click()
    await expect(page.getByText('The trash is empty')).toBeVisible()
    // The row and its button went: the keyboard goes on from the trash's heading.
    await expect(page.getByRole('heading', { name: 'Trash', exact: true })).toBeFocused()
    await expectAccessible(page, 'the trash')
    await page.getByRole('button', { name: 'Back to the library' }).click()
    await expect(contract).toBeVisible()
    await expect(folder).toHaveCount(0)
  })

  await test.step('upload a folder, following each file as it goes and then its indexing, open the file that could not be indexed, index the rest of a file partly indexed, and put it in the trash', async () => {
    // March's passages are refused once by the embeddings: that file is found by its words alone.
    // The receipt is a PDF without pages, which cannot be indexed.
    await refuseEmbeddings(request, 'printer paper')
    // The request is held while the panel that follows it is read.
    let release = () => {}
    const held = new Promise<void>((resolve) => {
      release = resolve
    })
    const uploads = '**/api/projects/*/documents*'
    await page.route(uploads, async (route) => {
      if (route.request().method() === 'POST') await held
      await route.continue()
    })
    await page.locator('input[name="folder"]').setInputFiles(DELIVERIES)
    const panel = page.getByRole('region', { name: 'Upload' })
    await expect(panel.getByRole('progressbar', { name: 'Uploading 3 files into Supplier contracts' })).toBeVisible()
    await panel.getByText('Each file', { exact: true }).click()
    const each = panel.getByRole('list', { name: 'Each file' })
    await expect(each.getByRole('listitem')).toHaveCount(3)
    await expect(each).toContainText('deliveries/march.md')
    await expect(each).toContainText('deliveries/april/week-1.md')
    await expectAccessible(page, 'the library while an upload is sent')
    release()
    // Once sent, the panel follows its documents until each is ready or could not be indexed,
    // and says why for the one that could not, which opens in its tab.
    await expect(panel.getByRole('status')).toHaveText('2 of 3 ready, 1 of them partly indexed; 1 could not be indexed.', {
      timeout: 60_000,
    })
    await expect(panel.getByRole('progressbar', { name: '3 files uploaded into Supplier contracts' })).toBeVisible()
    const failed = panel.getByRole('list', { name: 'Could not be indexed' })
    await expect(failed).toHaveText('receipt.pdf: This PDF has no pages. Export it again from the program that made it and upload that.')
    await expectAccessible(page, 'the library once an upload is indexed')
    await failed.getByRole('button', { name: 'receipt.pdf' }).click()
    await expect(page.getByRole('tab', { name: /receipt\.pdf/ })).toHaveAttribute('aria-selected', 'true')
    await panel.getByRole('button', { name: 'Dismiss' }).click()
    await expect(panel).toHaveCount(0)
    await expect(page.getByRole('tree')).toBeFocused()
    await page.unroute(uploads)
    // Its folders are made as they were, the one in it with its file.
    const folder = page.getByRole('treeitem', { name: /deliveries/ })
    await folder.click()
    await page.keyboard.press('ArrowRight')
    const march = page.getByRole('treeitem', { name: /march\.md/ })
    await expect(march).toBeVisible()
    await expect(page.getByRole('treeitem', { name: /april/ })).toBeVisible()
    // Partly indexed: the library counts it, and its tab says what it lacks and indexes the rest.
    await expect(march.getByRole('img', { name: /^Partly indexed/ })).toBeVisible({ timeout: 60_000 })
    const partly = page.getByText('1 document is partly indexed: found by its words, not yet by meaning.')
    await expect(partly).toBeVisible()
    await expectAccessible(page, 'the library with a document partly indexed')
    await march.click()
    await page.keyboard.press('Enter')
    const tab = page.getByRole('tabpanel', { name: /^march\.md/ })
    await expect(tab.getByText('Found by its words, not yet by meaning')).toBeVisible()
    await expectAccessible(page, 'a document partly indexed')
    await tab.getByRole('button', { name: 'Index the rest' }).click()
    await expect(tab.getByRole('heading', { level: 2 })).toBeFocused()
    await expect(tab.getByRole('img', { name: /^Ready/ })).toBeVisible({ timeout: 60_000 })
    await expect(tab.getByRole('img', { name: /^Ready/ })).toHaveAccessibleName(/Embeddings: done/)
    await expect(partly).toHaveCount(0)
    await folder.click()
    await page.keyboard.press('Delete')
    await expect(folder).toHaveCount(0)
    await expect(page.getByText('“deliveries” is in the trash.')).toBeVisible()
  })

  await test.step('export the project, which the worker makes, and download it', async () => {
    await page.getByRole('link', { name: 'Emudoc' }).click()
    await expect(page.getByRole('heading', { name: 'Projects' })).toBeVisible()
    // The card is the innermost block that holds the project's link, the last of those in the page's order.
    const card = page.locator('div').filter({ has: page.getByRole('link', { name: /^Supplier contracts/ }) }).last()
    await card.getByRole('button', { name: 'Export' }).click()
    const panel = page.getByRole('region', { name: 'Export Supplier contracts' })
    await panel.getByRole('button', { name: 'Make an export' }).click()
    const file = panel.getByRole('link', { name: 'Download' })
    await expect(file).toBeVisible({ timeout: 15_000 })
    await expect(panel).toContainText(/1 document, [\d.]+ kB, until /)
    await expectAccessible(page, "a project's exports")
    const downloading = page.waitForEvent('download')
    await file.click()
    const zip = await readFile(await (await downloading).path())
    expect(zip.subarray(0, 4).toString('latin1')).toBe('PK\u0003\u0004')
    await panel.getByRole('button', { name: 'Close' }).click()
    await expect(panel).toHaveCount(0)
    // The bell, which asks again as the page loads, tells it is ready, and its notice opens the panel.
    await page.reload()
    await page.getByRole('button', { name: 'Notifications, 1 unread' }).click()
    await expectAccessible(page, 'the notification centre')
    await page.getByRole('dialog', { name: 'Notifications' }).getByRole('button', { name: /Your project export is ready/ }).click()
    await expect(panel.getByRole('link', { name: 'Download' })).toBeVisible()
    await panel.getByRole('button', { name: 'Close' }).click()
    await expect(page.getByRole('button', { name: 'Notifications', exact: true })).toBeVisible()
  })

  await test.step("leave the account's menu by the keyboard, and choose the workspace's settings, read them in its audit log and download it", async () => {
    // From the projects page: a menu closes when anything scrolls, as the passage still may.
    await page.getByRole('link', { name: 'Emudoc', exact: true }).click()
    await expect(page.getByRole('heading', { name: 'Projects' })).toBeVisible()
    const account = page.getByRole('button', { name: /, your account$/ })
    await account.click()
    // Tab leaves a menu, closing it, and goes on from its button.
    await expect(page.getByRole('menuitem').first()).toBeFocused()
    await page.keyboard.press('Shift+Tab')
    await expect(page.getByRole('menu')).toHaveCount(0)
    await expect(account).toBeFocused()
    // The header names the workspace, which opens its page.
    await page.getByRole('link', { name: 'Analytical Engines' }).click()
    await expect(page.getByRole('heading', { name: 'Analytical Engines', level: 1 })).toBeVisible()
    // Its settings: the language its documents are searched in, and chats kept for less time than
    // until they are deleted, which says what it deletes before it is saved.
    const language = page.getByLabel('Language of its documents')
    const changed = page.waitForResponse((response) => response.url().endsWith('/api/organization') && response.request().method() === 'PATCH')
    await language.selectOption('german')
    expect((await changed).status()).toBe(200)
    await expect(language).toHaveValue('german')
    await page.getByLabel('Chats kept').selectOption('7')
    await expect(page.getByText(/more than 7 days old are deleted within six hours/)).toBeVisible()
    await expectAccessible(page, "an organisation's page")
    // The page saves a sign-in's days by a button of the same name: this is the one beside the select.
    const save = page.getByRole('button', { name: 'Save', exact: true })
    const chatDays = page.locator('div').filter({ has: page.getByLabel('Chats kept') }).filter({ has: save }).last()
    const kept = page.waitForResponse((response) => response.url().endsWith('/api/organization') && response.request().method() === 'PATCH')
    await chatDays.getByRole('button', { name: 'Save', exact: true }).click()
    expect((await kept).status()).toBe(200)
    await expect(page.getByLabel('Chats kept')).toHaveValue('7')
    await expect(chatDays.getByRole('button', { name: 'Save', exact: true })).toBeDisabled()
    // Each change is in its audit log, which an owner reads by what was done, and downloads.
    await page.getByRole('link', { name: 'Audit log' }).click()
    await expect(page.getByRole('heading', { name: 'Audit log' })).toBeVisible()
    await page.getByLabel('What').selectOption('settings_changed')
    const changes = page.getByRole('listitem').filter({ hasText: 'Changed a setting' })
    await expect(changes).toHaveCount(2)
    await expect(changes.first()).toContainText('Chats kept: until deleted → 7 days after the last question')
    await expect(changes.last()).toContainText('Language: the default → German')
    await expectAccessible(page, 'the audit log')
    const downloading = page.waitForEvent('download')
    await page.getByRole('link', { name: 'Download CSV' }).click()
    const csv = await readFile(await (await downloading).path(), 'utf8')
    // With the byte order mark Excel needs to read its names, which trim() takes off.
    expect(csv.startsWith('\ufeffoccurred_at,action,')).toBe(true)
    const [header, ...rows] = csv.trim().split(/\r?\n/)
    expect(header).toBe('occurred_at,action,user_id,user_name,user_email,ip,project_id,project_name,target_type,target_id,detail')
    expect(rows.map((row) => row.split(',')[1])).toEqual(['settings_changed', 'settings_changed'])
    // The language back to the default, so that the steps after search the documents as before.
    await page.goto('/organization')
    const restored = page.waitForResponse((response) => response.url().endsWith('/api/organization') && response.request().method() === 'PATCH')
    await page.getByLabel('Language of its documents').selectOption('')
    expect((await restored).status()).toBe(200)
  })

  await test.step('invite a viewer, who makes an account from the link, reads, and is made a member', async () => {
    // From the workspace's page, which the step before leaves open.
    const invitations = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Invitations', exact: true }) })
    const colleague = `grace.${Date.now()}@example.com`
    await invitations.getByLabel('Email', { exact: true }).fill(colleague)
    await invitations.getByLabel('Role', { exact: true }).selectOption('viewer')
    await invitations.getByRole('button', { name: 'Invite', exact: true }).click()
    // The run mails nothing, so the link is handed on: copied from the field that holds it.
    await expect(invitations).toContainText(`This Emudoc sends no mail, so nothing went to ${colleague}`)
    const invitation = await invitations.getByLabel(`Invitation link for ${colleague}`).inputValue()
    expect(invitation).toMatch(/\/invitation#token=/)
    await expectAccessible(page, "an invitation's link to copy")
    await expect(invitations.getByRole('listitem').filter({ hasText: colleague })).toContainText('Viewer')

    // Another browser, signed in as nobody, as the colleague's is.
    theirs = await browser.newContext()
    grace = await theirs.newPage()
    await grace.goto(invitation)
    await expect(grace.getByRole('heading', { name: "Join Analytical Engines" })).toBeVisible()
    await expectAccessible(grace, "an invitation's page")
    await grace.getByLabel('Name', { exact: true }).fill('Grace Hopper')
    await grace.getByLabel('Password', { exact: true }).fill('another sentence to remember')
    await grace.getByRole('button', { name: 'Create account' }).click()
    await expect(grace.getByText('Supplier contracts')).toBeVisible()
    await expect(grace.getByRole('link', { name: 'Analytical Engines' })).toBeVisible()
    // A viewer reads and asks: the pages offer no change the server would refuse.
    await expect(grace.getByRole('button', { name: 'New project' })).toHaveCount(0)
    await grace.getByText('Supplier contracts').click()
    // Her first project's tour, which Escape ends; a viewer uploads nothing, so it has no card.
    await expect(grace.getByRole('dialog')).toContainText('1 of 6')
    const seen = grace.waitForResponse((response) => response.url().endsWith('/api/auth/onboarding'))
    await grace.keyboard.press('Escape')
    expect((await seen).status()).toBe(200)
    await expect(grace.getByRole('dialog')).toHaveCount(0)
    await expect(grace.getByRole('img', { name: /^Ready/ })).toBeVisible()
    await expect(grace.getByRole('button', { name: 'Upload', exact: true })).toHaveCount(0)
    // Uploading is the project's files' menu's, a right-click on their empty space: a viewer's has none.
    const files = grace.getByRole('tree', { name: 'Files' })
    const filesMenu = async () => {
      await expect(async () => {
        const box = await files.boundingBox()
        await files.click({ button: 'right', position: { x: 12, y: (box?.height ?? 40) - 12 } })
        await expect(grace.getByRole('menuitem', { name: 'Select all' })).toBeVisible({ timeout: 2_000 })
      }).toPass({ timeout: 60_000 })
    }
    await filesMenu()
    await expect(grace.getByRole('menuitem', { name: 'Upload files' })).toHaveCount(0)
    await grace.keyboard.press('Escape')

    await page.reload()
    await expect(page.getByText('Nobody is invited at the moment.')).toBeVisible()
    const role = page.getByLabel('Role of Grace Hopper')
    await expect(role).toHaveValue('viewer')
    // The select shows the role at once; the colleague's page waits until it is saved.
    const saved = page.waitForResponse((response) => response.url().includes('/api/organization/members/') && response.request().method() === 'PATCH')
    await role.selectOption('member')
    expect((await saved).status()).toBe(200)
    await expect(role).toHaveValue('member')
    await grace.reload()
    await filesMenu()
    await expect(grace.getByRole('menuitem', { name: 'Upload files' })).toBeVisible()
    await grace.keyboard.press('Escape')
  })

  await test.step('read what the organisation holds and has used', async () => {
    await page.getByRole('link', { name: 'See what it holds and has used' }).click()
    await expect(page.getByRole('heading', { name: 'Usage', exact: true })).toBeVisible()
    const section = (title: string) => page.locator('section').filter({ has: page.getByRole('heading', { name: title, exact: true }) })
    // Both members; the sample not counted; the two questions asked, with no limit beside them.
    await expect(section('Held now').getByRole('definition').nth(0)).toContainText('Not counted: the sample project.')
    await expect(section('Held now').getByRole('definition').nth(3)).toHaveText(/^2Open invitations counted\.$/)
    await expect(page.getByText(/ of [\d,]+/)).toHaveCount(0)
    await expect(section('Day by day').getByRole('figure', { name: /^Questions/ })).toContainText('2 questions in all')
    await expectAccessible(page, 'the usage page')
  })

  await test.step("find in the help menu, the run mailing nothing, the workspace's admin to write to, by a mail that carries the page, and the code's source", async () => {
    // This run names no SUPPORT_EMAIL and no relay, so the menu sends nothing and names the workspace's owners and admins: Ada.
    await grace.getByRole('button', { name: 'Help', exact: true }).click()
    const help = grace.getByRole('dialog', { name: 'Help' })
    await expect(help.getByText("Write to your workspace's admins", { exact: true })).toBeVisible()
    await expect(help.getByText('This workspace sends no email, so the help menu cannot send your message.', { exact: false })).toBeVisible()
    await expect(help.getByRole('button', { name: 'Send' })).toHaveCount(0)
    const ada = help.getByRole('link', { name: `Ada Lovelace, ${address}`, exact: true })
    // The mail from her own carries the page she wrote from, as the form's message did.
    const href = (await ada.getAttribute('href')) ?? ''
    expect(href.startsWith(`mailto:${address}?subject=`)).toBe(true)
    expect(decodeURIComponent(href)).toMatch(/Page: http:\/\/[^/]+\/projects\/\d+/)
    // The code's source, which the AGPL has the deployment offer: a checkout's is the public repository.
    await expect(help.getByRole('link', { name: 'Source code', exact: true })).toHaveAttribute('href', 'https://github.com/adityaviki/emudoc')
    await expectAccessible(grace, 'the help menu')
    await grace.keyboard.press('Escape')
    await expect(help).toHaveCount(0)
    await expect(grace.getByRole('button', { name: 'Help', exact: true })).toBeFocused()
  })

  await test.step('read the help pages from the help menu, find one by a word and follow its link into the app', async () => {
    await page.getByRole('button', { name: 'Help', exact: true }).click()
    const menu = page.getByRole('dialog', { name: 'Help' })
    await menu.getByRole('link', { name: 'Help pages', exact: true }).click()
    await expect(page).toHaveURL(/\/help$/)
    await expect(menu).toHaveCount(0)
    await expect(page.getByRole('heading', { level: 1, name: 'Help', exact: true })).toBeVisible()
    await expectAccessible(page, 'the help pages')
    await page.getByLabel('Search the help').fill('zip file')
    await expect(page.getByRole('link', { name: /^Getting started/ })).toHaveCount(0)
    await page.getByRole('link', { name: /^Your data/ }).click()
    const article = page.getByRole('article')
    await expect(article.getByRole('heading', { name: 'Your data: exports, the trash and deletion' })).toBeVisible()
    // Its figures are the deployment's own (backend/app/help).
    await expect(article).toContainText('kept 7 days for whoever asked for it')
    await expectAccessible(page, 'a help page')
    // What the run does not do it does not promise: it mails nothing, and has a status page.
    await page.getByRole('link', { name: 'All help' }).click()
    await page.getByRole('link', { name: /^Getting help/ }).click()
    await expect(article).toContainText("This workspace sends no email, so the menu sends nothing: it names the workspace's owners and admins")
    await expect(article.getByRole('link', { name: 'its status page' })).toHaveAttribute('href', 'https://status.example.com')
    await page.goBack()
    await page.getByRole('link', { name: /^Your data/ }).click()
    await article.getByRole('link', { name: 'account page' }).click()
    await expect(page).toHaveURL(/\/account$/)
  })

  await test.step('make a read key, which a program lists the projects with and changes nothing by, beside the upload client, and read the contract the API publishes', async () => {
    await page.goto('/organization')
    const keys = page.locator('section').filter({ has: page.getByRole('heading', { name: 'API keys', exact: true }) })
    await keys.getByLabel('Name', { exact: true }).fill('Invoices sync')
    await keys.getByRole('button', { name: 'Make a key' }).click()
    // The key, shown once; the section's sentence names the variable a program reads it from in code too.
    const shown = keys.locator('code', { hasText: /^emk_/ })
    const key = (await shown.textContent()) ?? ''
    expect(key).toMatch(/^emk_/)
    // A program sends the key alone, with no cookie and none of the app's headers, to the versioned API.
    const bearer = { Authorization: `Bearer ${key}` }
    const listed = await request.get('/api/v1/projects', { headers: bearer })
    expect(listed.status()).toBe(200)
    expect((await listed.json()).data.map((project: { name: string }) => project.name)).toContain('Supplier contracts')
    expect((await request.post('/api/v1/projects', { headers: bearer, data: { name: 'Mine' } })).status()).toBe(403)
    expect((await request.get('/api/organization/keys', { headers: bearer })).status()).toBe(403)
    // The upload client the section offers, which the app serves for download.
    const offered = await keys.getByRole('link', { name: 'download the upload client' }).getAttribute('href')
    const script = await request.get(offered ?? '')
    expect(script.headers()['content-disposition']).toBe('attachment; filename="emudoc_upload.py"')
    expect(await script.text()).toMatch(/^#!\/usr\/bin\/env python3\n/)
    await keys.getByRole('button', { name: 'I have saved it' }).click()
    await expect(shown).toHaveCount(0)
    await page.reload()
    await expect(keys.getByRole('listitem').filter({ hasText: 'Invoices sync' })).toContainText('last used')
    // The contract a program's tools read, which names this deployment.
    const contract = await request.get('/api/v1/openapi.json')
    expect(contract.status()).toBe(200)
    expect((await contract.json()).servers).toEqual([{ url: new URL(page.url()).origin }])
  })

  await test.step('add a webhook at a program of its own, whose secret is shown once', async () => {
    await webhooks.getByLabel('Address').fill('hooks.example.com')
    await expect(webhooks.getByText('Give the webhook an address that starts with https://.')).toBeVisible()
    await expect(webhooks.getByRole('button', { name: 'Add the webhook' })).toBeDisabled()
    // The stand-in is the program; development sends to its own machine over HTTP.
    await webhooks.getByLabel('Address').fill(`${STANDIN}/hooks`)
    await expect(webhooks.getByLabel(/^A document is indexed/)).toBeChecked()
    await webhooks.getByRole('button', { name: 'Add the webhook' }).click()
    // The button went disabled with the form cleared: the keyboard goes on from the secret's.
    await expect(webhooks.getByRole('button', { name: 'Copy the secret' })).toBeFocused()
    const secret = webhooks.locator('code', { hasText: /^whsec_/ })
    hookSecret = (await secret.textContent()) ?? ''
    expect(hookSecret).toMatch(/^whsec_/)
    await expectAccessible(page, 'a webhook added')
    await webhooks.getByRole('button', { name: 'I have saved it' }).click()
    await expect(secret).toHaveCount(0)
    await expect(webhooks.getByRole('listitem')).toContainText('nothing sent yet')
  })

  await test.step('make the project private, which a member not chosen no longer finds, then choose her', async () => {
    await page.goto('/')
    const card = page.locator('div.group').filter({ hasText: 'Supplier contracts' })
    await card.getByRole('button', { name: 'Who sees it' }).click()
    await page.getByLabel('Only the members chosen').check()
    await expect(page.getByText('Sees every project')).toBeVisible()
    await expectAccessible(page, "a project's access")
    await page.getByRole('button', { name: 'Save', exact: true }).click()
    await expect(page.getByText('Private', { exact: true })).toBeVisible()
    await grace.goto('/')
    await expect(grace.getByText(SAMPLE)).toBeVisible()
    await expect(grace.getByText('Supplier contracts')).toHaveCount(0)

    await card.getByRole('button', { name: 'Who sees it' }).click()
    await page.getByLabel(/Grace Hopper/).check()
    await page.getByRole('button', { name: 'Save', exact: true }).click()
    await expect(page.getByRole('heading', { name: 'Who sees Supplier contracts' })).toHaveCount(0)
    await grace.reload()
    await expect(grace.getByText('Supplier contracts')).toBeVisible()
    await expect(grace.getByText('Private', { exact: true })).toBeVisible()
  })

  await test.step('share the chat with the project, which a colleague reads but does not ask in', async () => {
    await page.getByText('Supplier contracts').click()
    await expect(page.getByRole('button', { name: /^How much notice/ })).toBeVisible()
    await page.getByRole('button', { name: 'Share', exact: true }).click()
    await expect(page.getByRole('button', { name: 'Make private', exact: true })).toBeVisible()

    // Her own chat opens by default, so a new one, which offers the chats others shared.
    await grace.getByText('Supplier contracts').click()
    const shared = grace.getByRole('button', { name: /How much notice .*Ada Lovelace ·/ })
    await shared.click()
    await expect(grace.getByText("Either party may end this contract by giving ninety days' written notice to the other.")).toBeVisible()
    await expect(grace.getByText('Ada Lovelace shared this chat with the project.', { exact: false })).toBeVisible()
    await expect(grace.getByPlaceholder('Ask a question about these documents')).toHaveCount(0)
    await expect(grace.getByRole('button', { name: 'Share', exact: true })).toHaveCount(0)
    await theirs.close()
  })

  await test.step("read when the members were last seen, open the Administration page, and switch a provider", async () => {
    // Its owner's audit log, read whole, holds every kind of row the workspace has, its export's among them.
    await page.goto('/organization/audit')
    await expect(page.getByRole('listitem').filter({ hasText: 'Exported a project' })).toContainText('Supplier contracts')
    // The members, each with when they were last seen.
    await page.goto('/organization')
    const members = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Members', exact: true }) })
    await expect(members.getByRole('listitem').filter({ hasText: 'Grace Hopper' })).toContainText('last seen')
    // The administration, from the account's menu, which offers it to owners and admins.
    await page.getByRole('button', { name: /, your account$/ }).click()
    await page.getByRole('menuitem', { name: 'Administration' }).click()
    await expect(page).toHaveURL(/\/admin\/jobs$/)
    await expect(page.getByRole('heading', { name: 'Jobs', exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Ask again' })).toBeVisible()
    // The worker the server runs in its own process says it runs, so the page warns of none.
    const workers = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Workers' }) })
    await expect(workers.getByRole('row').filter({ hasText: 'running, seen' })).toHaveCount(1)
    await expect(page.getByText('No worker has been seen running')).toHaveCount(0)
    await expectAccessible(page, "the Administration page's jobs")
    const views = page.getByRole('navigation', { name: 'Administration' })
    await views.getByRole('link', { name: 'Costs' }).click()
    await expect(page.getByRole('heading', { name: 'By kind of work and model' })).toBeVisible()
    await expectAccessible(page, "the Administration page's costs")
    await views.getByRole('link', { name: 'Services' }).click()
    await expect(page.getByRole('heading', { name: 'What the watch sees' })).toBeVisible()
    await expectAccessible(page, "the Administration page's services")
    // The stand-in's provider switched off, which asks first, and on again: last, since nothing asks a model after it.
    const ollama = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Providers' }) }).getByRole('row').filter({ hasText: 'ollama' })
    await ollama.getByRole('button', { name: 'Switch ollama off' }).click()
    await page.getByRole('dialog', { name: 'Switch ollama off?' }).getByRole('button', { name: 'Switch it off' }).click()
    await expect(ollama).toContainText('switched off since')
    await expect(page.getByText('is switched off: an admin switched its provider, ollama, off').first()).toBeVisible()
    await ollama.getByRole('button', { name: 'Switch ollama on' }).click()
    await expect(ollama.getByRole('cell').nth(2)).toHaveText('on')
    await expect(ollama.getByRole('button', { name: 'Switch ollama off' })).toBeVisible()
    // Each switch is a row of the workspace's audit log.
    await page.goto('/organization/audit')
    const switched = page.getByRole('listitem').filter({ hasText: 'Switched a model provider' })
    await expect(switched.filter({ hasText: 'ollama off' })).toHaveCount(1)
    await expect(switched.filter({ hasText: 'ollama on again' })).toHaveCount(1)
  })

  await test.step('upload an invoice, whose readiness the webhook hears of', async () => {
    await page.goto('/')
    await page.getByRole('link', { name: /^Supplier contracts/ }).click()
    await page.locator('input[name="files"]').setInputFiles(INVOICE)
    await expect(page.getByRole('treeitem', { name: 'invoice.md' })).toHaveAccessibleDescription(/^Ready/, { timeout: 60_000 })
  })

  await test.step("find the invoice's event posted to the webhook, signed with its secret, and its delivery in the log", async () => {
    await expect.poll(async () => (await hooksReceived(request)).length, { timeout: 30_000 }).toBeGreaterThan(0)
    const [hooked] = await hooksReceived(request)
    expect(hooked.event).toBe('document.ready')
    const event = JSON.parse(hooked.body)
    expect([event.type, event.data.filename]).toEqual(['document.ready', 'invoice.md'])
    // What a receiver checks: the HMAC of the time and the body as sent, with the secret.
    const { t, v1 } = Object.fromEntries(hooked.signature.split(',').map((part) => part.split('=')))
    expect(createHmac('sha256', hookSecret).update(`${t}.${hooked.body}`).digest('hex')).toBe(v1)
    await page.goto('/organization')
    await webhooks.getByRole('button', { name: 'Deliveries' }).click()
    const log = webhooks.getByRole('region', { name: 'Deliveries to 127.0.0.1:18001' })
    await expect(log.getByRole('listitem').first()).toContainText('Delivered')
    await expect(log).toContainText(event.id)
    await expectAccessible(page, "a webhook's deliveries")
  })

  await test.step('upload a second invoice of the same order', async () => {
    await page.goto('/')
    await page.getByRole('link', { name: /^Supplier contracts/ }).click()
    await page.locator('input[name="files"]').setInputFiles(SECOND_INVOICE)
    await expect(page.getByRole('treeitem', { name: 'second-invoice.md' })).toHaveAccessibleDescription(/^Ready/, { timeout: 60_000 })
  })

  await test.step('ask in a chat for a Word document, which the agent writes into My files, and open it from its step', async () => {
    await page.goto('/')
    await page.getByRole('link', { name: /^Supplier contracts/ }).click()
    await expect(page).not.toHaveURL(/[?&]chat=/)
    const composer = page.getByPlaceholder('Ask a question about these documents')
    await composer.fill('Write a Word document about the invoice.')
    await composer.press('Enter')
    // The step names the file, written as it went, and opens it.
    const open = page.getByRole('button', { name: 'Open Notes on the invoice.docx' })
    await expect(open).toBeVisible({ timeout: 30_000 })
    await expect(page.getByText('Wrote "Notes on the invoice" into My files')).toBeVisible()
    await expect(page.getByRole('paragraph').filter({ hasText: 'It is in your files.' })).toBeVisible()
    await expectAccessible(page, 'a step that wrote a file of My files')
    await open.click()

    // In a tab of the middle pane, where the project's documents open, its heading focused: the member's alone, written
    // by the agent.
    const tabs = page.getByRole('tablist', { name: 'Open documents' })
    // Its name says whose it is (its parts, flex items, read with a space between them).
    const tab = tabs.getByRole('tab', { name: /^Notes on the invoice\.docx ?, in My files$/ })
    await expect(tab).toHaveAttribute('aria-selected', 'true')
    await expect(page).toHaveURL(/\/my-files\/\d+/)
    await expect(page.getByRole('heading', { name: 'Notes on the invoice.docx' })).toBeFocused()
    await expect(page.getByText('In My files: only you see it.')).toBeVisible()
    await expect(page.getByRole('list', { name: 'Versions' })).toContainText('Written by the agent · From a chat')
    await expectAccessible(page, 'a file of My files the agent wrote')
    // The library's My files, under its search and above the project's files, lists it, and its row shows the same tab.
    const listed = page.getByRole('list', { name: 'My files' })
    await expect(listed).toContainText('Notes on the invoice.docx')
    await expectAccessible(page, 'My files')
    // Each section's heading closes and opens it (the button's name is its title alone: the file's tab holds the words).
    const mine = page.getByRole('button', { name: 'My files', exact: true })
    await expect(mine).toHaveAttribute('aria-expanded', 'true')
    await mine.click()
    await expect(mine).toHaveAttribute('aria-expanded', 'false')
    await expect(listed).toBeHidden()
    await expectAccessible(page, 'My files closed')
    const projectFiles = page.getByRole('button', { name: 'Project files', exact: true })
    await projectFiles.click()
    await expect(projectFiles).toHaveAttribute('aria-expanded', 'false')
    await expect(page.getByRole('tree', { name: 'Files' })).toBeHidden()
    // Both open again, since the browser keeps them as left for the steps after.
    await projectFiles.click()
    await expect(page.getByRole('tree', { name: 'Files' })).toBeVisible()
    await mine.click()
    await expect(listed).toBeVisible()
    await listed.getByRole('button', { name: /^Notes on the invoice\.docx/ }).click()
    await expect(tabs.getByRole('tab', { name: /^Notes on the invoice\.docx/ })).toHaveCount(1)
    await expect(tab).toHaveAttribute('aria-selected', 'true')
    await page.goto('/organization/audit')
    await page.getByLabel('What').selectOption('my_file_added')
    await expect(page.getByRole('listitem').filter({ hasText: 'by the agent in a chat' })).toContainText('a Word document, by the agent in a chat')
  })

  await test.step('ask in a chat for an Excel workbook, which the agent makes in My files, and open it from its step', async () => {
    await page.goto('/')
    await page.getByRole('link', { name: /^Supplier contracts/ }).click()
    await expect(page).not.toHaveURL(/[?&]chat=/)
    const composer = page.getByPlaceholder('Ask a question about these documents')
    await composer.fill('Make an Excel workbook of the invoice.')
    await composer.press('Enter')
    const open = page.getByRole('button', { name: 'Open Lines of the invoice.xlsx' })
    await expect(open).toBeVisible({ timeout: 30_000 })
    await expect(page.getByText('Made the workbook "Lines of the invoice" in My files')).toBeVisible()
    await open.click()
    await expect(page.getByRole('tablist', { name: 'Open documents' }).getByRole('tab', { name: /^Lines of the invoice\.xlsx ?, in My files$/ })).toHaveAttribute(
      'aria-selected',
      'true',
    )
    await expect(page.getByRole('heading', { name: 'Lines of the invoice.xlsx' })).toBeFocused()
    await expect(page.getByRole('list', { name: 'Versions' })).toContainText('Written by the agent · From a chat')
    await expect(page.getByRole('list', { name: 'My files' })).toContainText('Lines of the invoice.xlsx')
    // Its row's right-click menu offers what a row of the project's files does, for a file of one's own.
    const row = page.getByRole('list', { name: 'My files' }).getByRole('button', { name: /^Lines of the invoice\.xlsx/ })
    await expect(async () => {
      await row.click({ button: 'right' })
      await expect(page.getByRole('menuitem', { name: 'Upload a new version…' })).toBeVisible({ timeout: 2_000 })
    }).toPass({ timeout: 60_000 })
    for (const item of ['Open', 'Download', 'Rename', 'Copy to project files…', 'Delete']) {
      await expect(page.getByRole('menuitem', { name: item })).toBeVisible()
    }
    await expectAccessible(page, "a file of My files' menu")
    await page.getByRole('menuitem', { name: 'Rename' }).click()
    const name = page.getByRole('textbox', { name: 'New name' })
    await expect(name).toBeFocused()
    await name.fill('Invoice lines')
    await name.press('Enter')
    // Its kind's extension kept, the keyboard back on its row, which Enter did not open again.
    await expect(page.getByRole('list', { name: 'My files' }).getByRole('button', { name: /^Invoice lines\.xlsx/ })).toBeFocused()
  })

  await test.step('keep My files in a folder made from its heading, a file moved and one uploaded into it, then renamed and deleted with them', async () => {
    const list = page.getByRole('list', { name: 'My files' })
    const name = page.getByRole('textbox', { name: 'New name' })
    // The workbook downloaded, to be uploaded again into the folder under its own name.
    const downloading = page.waitForEvent('download')
    await list.getByRole('button', { name: /^Invoice lines\.xlsx/ }).click({ button: 'right' })
    await page.getByRole('menuitem', { name: 'Download' }).click()
    const workbook = join(await mkdtemp(join(tmpdir(), 'emudoc-e2e-')), 'Invoice lines.xlsx')
    await (await downloading).saveAs(workbook)
    // The heading's right-click, as the project files' empty space, offers a new folder and uploads.
    const mine = page.getByRole('button', { name: 'My files', exact: true })
    await mine.click({ button: 'right' })
    for (const item of ['Upload files', 'Upload a folder']) await expect(page.getByRole('menuitem', { name: item })).toBeVisible()
    await expectAccessible(page, "My files' menu")
    await page.getByRole('menuitem', { name: 'New folder' }).click()
    await expect(name).toBeFocused()
    await name.fill('Letters')
    await name.press('Enter')
    const letters = list.getByRole('button', { name: 'Letters', exact: true })
    await expect(letters).toBeFocused()
    await expect(letters).toHaveAttribute('aria-expanded', 'false')
    // "Move to…" from the Word document's row puts it into the folder, which opens to show it.
    await list.getByRole('button', { name: /^Notes on the invoice\.docx/ }).click({ button: 'right' })
    await page.getByRole('menuitem', { name: 'Move to…' }).click()
    const moving = page.getByRole('dialog', { name: 'Move “Notes on the invoice.docx”' })
    await expect(moving.getByLabel('Folder')).toBeFocused()
    await expectAccessible(page, 'a file of My files moved')
    await moving.getByLabel('Folder').selectOption({ label: 'Letters' })
    await moving.getByRole('button', { name: 'Move' }).click()
    const inLetters = list.getByRole('list', { name: 'Letters' })
    await expect(letters).toHaveAttribute('aria-expanded', 'true')
    await expect(inLetters.getByRole('button', { name: /^Notes on the invoice\.docx/ })).toBeVisible()
    // The folder's own menu uploads into it; the one file chosen opens in a tab of its own, beside the workbook's.
    const workbookTabs = page.getByRole('tablist', { name: 'Open documents' }).getByRole('tab', { name: /^Invoice lines\.xlsx/ })
    const before = await workbookTabs.count()
    await letters.click({ button: 'right' })
    const chooser = page.waitForEvent('filechooser')
    await page.getByRole('menuitem', { name: 'Upload files' }).click()
    await (await chooser).setFiles(workbook)
    await expect(inLetters.getByRole('button', { name: /^Invoice lines\.xlsx/ })).toBeVisible()
    await expect(workbookTabs).toHaveCount(before + 1)
    await expectAccessible(page, 'a folder of My files')
    // F2 renames the folder; Delete asks before deleting it with what it holds, whose tabs go with it.
    await letters.press('F2')
    await expect(name).toBeFocused()
    await name.fill('Mail')
    await name.press('Enter')
    const mail = list.getByRole('button', { name: 'Mail', exact: true })
    await expect(mail).toBeFocused()
    await mail.press('Delete')
    const question = page.getByRole('dialog', { name: 'Delete this folder?' })
    await expect(question).toContainText('Delete “Mail” and the 2 files in it for good?')
    await expectAccessible(page, "a folder of My files' deletion")
    await question.getByRole('button', { name: 'Delete for good' }).click()
    await expect(mail).toHaveCount(0)
    await expect(mine).toBeFocused()
    await expect(page.getByRole('tablist', { name: 'Open documents' }).getByRole('tab', { name: /^Notes on the invoice\.docx/ })).toHaveCount(0)
    await expect(workbookTabs).toHaveCount(before)
    // The workbook it was uploaded from stays at the top.
    await expect(list.getByRole('button', { name: /^Invoice lines\.xlsx/ })).toHaveCount(1)
  })

  await test.step("give the project an address, list a supplier's domain, whose mail becomes documents, and read what became of a stranger's", async () => {
    await page.goto('/')
    await page.getByRole('link', { name: /^Supplier contracts/ }).click()
    await page.getByRole('button', { name: 'Email in' }).click()
    await expect(page.getByRole('heading', { name: 'Email in' })).toBeVisible()
    await page.getByRole('button', { name: 'Give it an address' }).click()
    const shown = page.getByText(new RegExp(`^[a-z2-7]{16}@${INBOUND_DOMAIN.replaceAll('.', '\\.')}$`))
    await expect(shown).toBeVisible()
    const projectAddress = (await shown.textContent())!.trim()
    const sender = page.getByLabel('An address or a domain')
    await sender.fill('gmail.com')
    await page.getByRole('button', { name: 'Add', exact: true }).click()
    await expect(page.getByText('Anyone can have an address at gmail.com')).toBeVisible()
    await sender.fill('Northwind.example')
    await page.getByRole('button', { name: 'Add', exact: true }).click()
    await expect(page.getByRole('list', { name: 'Senders' })).toContainText('northwind.example')
    await expectAccessible(page, "a project's address")

    // A supplier at the listed domain mails a delivery note, and a stranger an invoice; the
    // operator's command hands each over as the provider's way in would.
    const saved = await mkdtemp(join(tmpdir(), 'emudoc-e2e-mail-'))
    const mail = (from: string, subject: string, file: string, content: string) =>
      [
        `From: ${from}`,
        `To: ${projectAddress}`,
        `Subject: ${subject}`,
        'Date: Fri, 23 Oct 2026 09:30:00 +0000',
        'MIME-Version: 1.0',
        'Content-Type: multipart/mixed; boundary="part"',
        '',
        '--part',
        'Content-Type: text/plain; charset=utf-8',
        '',
        `${subject} is attached.`,
        '--part',
        `Content-Type: text/markdown; name="${file}"`,
        `Content-Disposition: attachment; filename="${file}"`,
        '',
        content,
        '--part--',
        '',
      ].join('\r\n')
    await writeFile(
      join(saved, 'supplier.eml'),
      mail('Lena Vogt <lena@northwind.example>', 'Delivery note DN-118', 'delivery-note-118.md', '# Delivery note DN-118\n\nPO-7701: 40 pallets delivered on 22 October 2026.'),
    )
    await writeFile(join(saved, 'stranger.eml'), mail('mallory@elsewhere.example', 'Pay this invoice', 'urgent.md', '# Pay now'))
    expect(receiveMail(join(saved, 'supplier.eml'))).toEqual([expect.stringMatching(/: kept 2 documents\.$/)])
    expect(receiveMail(join(saved, 'stranger.eml'))[0]).toMatch(/: turned away\. mallory@elsewhere\.example is not among the senders/)

    await page.reload()
    const messages = page.getByRole('list', { name: 'Messages received' })
    await expect(messages.getByRole('listitem').filter({ hasText: 'Delivery note DN-118' })).toContainText('2 documents kept')
    await expect(messages.getByRole('listitem').filter({ hasText: 'Pay this invoice' })).toContainText('Turned away')
    await expectAccessible(page, 'the messages a project received')
    await page.getByRole('button', { name: 'Back to the library' }).click()
    await expect(page.getByRole('treeitem', { name: /Delivery note DN-118\.txt/ })).toBeVisible()
    await expect(page.getByRole('treeitem', { name: /delivery-note-118\.md/ })).toBeVisible()
    await page.goto('/organization/audit')
    await page.getByLabel('What').selectOption('mailbox_changed')
    await expect(page.getByRole('listitem').filter({ hasText: 'Changed a project’s address' })).toContainText('from 1 sender and its members, 1 added')

    // And a folder of the member's Google Drive, the stand-in's, kept in step with the project:
    // its consent page sends the browser straight back, where the folder is chosen.
    await page.goto('/')
    await page.getByRole('link', { name: /^Supplier contracts/ }).click()
    await page.getByRole('button', { name: 'Connected folders' }).click()
    await expect(page.getByRole('heading', { name: 'Connected folders' })).toBeVisible()
    await expectAccessible(page, 'a project without connected folders')
    await page.getByRole('button', { name: 'Connect Google Drive' }).click()
    await expect(page.getByRole('list', { name: 'Where Google Drive starts' })).toBeVisible()
    await page.getByRole('button', { name: 'My Drive' }).click()
    await page.getByRole('list', { name: 'Folders in My Drive' }).getByRole('button', { name: 'Supplier files' }).click()
    await expect(page.getByRole('list', { name: 'Folders in Supplier files' })).toContainText('Archive')
    await expectAccessible(page, "the member's folders at the service")
    await page.getByRole('button', { name: 'Sync Supplier files' }).click()
    const connected = page.getByRole('region', { name: 'Google Drive: Supplier files' })
    await expect(connected).toContainText('Synced ', { timeout: 30_000 })
    await expect(connected).toContainText('2 documents in the project’s root, synced every 15 minutes.')
    await expect(connected).toContainText('You connected it as member@drive.example.')
    await expectAccessible(page, 'a connected folder')
    await page.getByRole('button', { name: 'Back to the library' }).click()
    await expect(page.getByRole('treeitem', { name: /credit-note-CN-12\.md/ })).toBeVisible()
    await expect(page.getByRole('treeitem', { name: /^Archive/ })).toBeVisible()
    await page.goto('/organization/audit')
    await page.getByLabel('What').selectOption('connector_changed')
    await expect(page.getByRole('listitem').filter({ hasText: 'Changed a connected folder' })).toContainText(
      'Google Drive, Supplier files, into the project’s root',
    )
  })

  await test.step('share the invoice by a link with a password, which someone with no account opens, then revoke it, and share an answer about it', async () => {
    await page.goto('/')
    await page.getByRole('link', { name: /^Supplier contracts/ }).click()
    await page.getByRole('treeitem', { name: 'invoice.md', exact: true }).click()
    const share = page.getByRole('button', { name: 'Share by link' })
    await share.click()
    const dialog = page.getByRole('dialog', { name: 'Share “invoice.md”' })
    await expect(dialog.getByLabel('Runs for')).toBeFocused()
    await expect(dialog.getByText('No link to this document is running.')).toBeVisible()
    await expectAccessible(page, "a document's share links")
    await dialog.getByLabel('Password, if it should have one').fill('harbour lights')
    await dialog.getByRole('button', { name: 'Make a link' }).click()
    // Its address is shown once, focused to copy.
    const field = dialog.getByLabel('The link')
    await expect(field).toBeFocused()
    const address = await field.inputValue()
    expect(address).toMatch(/^http:\/\/127\.0\.0\.1:\d+\/shared#token=[\w-]{43}$/)
    await expectAccessible(page, 'a share link just made')
    await dialog.getByRole('button', { name: 'Done' }).click()
    await expect(dialog.getByRole('listitem')).toContainText('with a password · not opened yet')

    // Someone with no account opens it in a browser of their own, with its password alone.
    const elsewhere = await browser.newContext()
    const visitor = await elsewhere.newPage()
    await visitor.goto(address)
    await expect(visitor.getByRole('heading', { name: 'This link has a password' })).toBeVisible()
    await expect(visitor.getByLabel('Password', { exact: true })).toBeFocused()
    await expectAccessible(visitor, "a share link's password")
    await visitor.getByLabel('Password', { exact: true }).fill('harbour light')
    await visitor.getByRole('button', { name: 'Open the link' }).click()
    await expect(visitor.getByRole('alert')).toHaveText("That is not this link's password. Check it with whoever sent the link.")
    await visitor.getByLabel('Password', { exact: true }).fill('harbour lights')
    await visitor.getByRole('button', { name: 'Open the link' }).click()
    await expect(visitor.getByText(/^Shared by Ada Lovelace of Analytical Engines, until /)).toBeVisible()
    await expect(visitor.getByText('Invoice number: INV-2026-0412')).toBeVisible()
    await expectAccessible(visitor, 'a shared document')
    // Printed, it is the invoice alone, under its name: not the page's header, who shared it or its buttons.
    await visitor.emulateMedia({ media: 'print' })
    for (const part of [visitor.getByRole('banner'), visitor.getByText(/^Shared by /), visitor.getByRole('link', { name: 'Download the file' })]) {
      await expect(part).toBeHidden()
    }
    await expect(visitor.getByText('Invoice number: INV-2026-0412')).toBeVisible()
    await visitor.emulateMedia({ media: null })
    expect((await printed(visitor)).title).toBe(await visitor.getByRole('heading', { level: 1 }).textContent())
    const downloading = visitor.waitForEvent('download')
    await visitor.getByRole('link', { name: 'Download the file' }).click()
    expect((await downloading).suggestedFilename()).toBe('invoice.md')

    // Its member sees it opened, and revokes it: it stops at once, the keyboard going on from the heading.
    await page.keyboard.press('Escape')
    await expect(share).toBeFocused()
    await share.click()
    const link = dialog.getByRole('listitem')
    await expect(link).toContainText('opened once')
    await link.getByRole('button', { name: 'Revoke', exact: true }).click()
    await link.getByRole('button', { name: 'Revoke the link' }).click()
    await expect(dialog.getByText('No link to this document is running.')).toBeVisible()
    await expect(dialog.getByRole('heading', { name: 'Share “invoice.md”' })).toBeFocused()
    await visitor.reload()
    await expect(visitor.getByRole('heading', { name: 'The link does not open' })).toBeVisible()
    await expectAccessible(visitor, 'a share link that stopped')

    // An answer too, which its link shows with its question and the passages it cites, each citation leading to its passage.
    await page.goto('/')
    await page.getByRole('link', { name: /^Supplier contracts/ }).click()
    // A chat of its own, the project's last one holding answers of their own to share.
    await page.getByRole('button', { name: 'New chat' }).click()
    const composer = page.getByPlaceholder('Ask a question about these documents')
    await composer.fill('What is the total amount of the invoice?')
    await composer.press('Enter')
    const shareAnswer = page.getByRole('button', { name: 'Share the answer' })
    await expect(shareAnswer).toBeVisible({ timeout: 30_000 })
    await shareAnswer.click()
    const answerDialog = page.getByRole('dialog', { name: 'Share this answer' })
    await answerDialog.getByRole('button', { name: 'Make a link' }).click()
    const answerAddress = await answerDialog.getByLabel('The link').inputValue()
    await page.keyboard.press('Escape')
    await expect(shareAnswer).toBeFocused()
    // The answer downloads as its Markdown headed by its question, its citation a footnote, and the whole chat as a PDF.
    const downloadAnswer = page.getByRole('button', { name: 'Download the answer' })
    await downloadAnswer.click()
    const answerDownload = page.waitForEvent('download')
    await page.getByRole('menu').getByRole('menuitem', { name: 'Markdown, as it is written' }).click()
    await expect(downloadAnswer).toBeFocused()
    const answerFile = await answerDownload
    expect(answerFile.suggestedFilename()).toBe('What is the total amount of the invoice.md')
    const answerMarkdown = await readFile(await answerFile.path(), 'utf8')
    expect(answerMarkdown).toMatch(/^# What is the total amount of the invoice\?\n\n\*Asked by .+ in Supplier contracts on /)
    expect(answerMarkdown).toMatch(/\[\^1\][^]*\n\[\^1\]: \S/)
    await page.getByRole('button', { name: 'Download the chat' }).click()
    const chatDownload = page.waitForEvent('download')
    await page.getByRole('menu').getByRole('menuitem', { name: 'PDF, to send as it is' }).click()
    expect((await chatDownload).suggestedFilename()).toBe('What is the total amount of the invoice.pdf')
    await visitor.goto(answerAddress)
    await expect(visitor.getByRole('heading', { level: 1 })).toHaveText(/^An answer about /)
    await expect(visitor.getByText('What is the total amount of the invoice?')).toBeVisible()
    await visitor.getByRole('button', { name: '1', exact: true }).first().click()
    await expect(visitor.getByRole('listitem', { name: /^Passage 1: / })).toBeFocused()
    await expectAccessible(visitor, 'a shared answer')
    await elsewhere.close()

    // The organisation's owners and admins see its links, and read each opening in the audit log, by nobody it knows.
    await page.goto('/organization')
    await expect(page.getByRole('listitem').filter({ hasText: 'An answer of a chat in Supplier contracts' })).toContainText('opened once')
    await expect(page.getByLabel(/^Let members share documents and answers by link/)).toBeChecked()
    await expectAccessible(page, "the organisation's share links")
    await page.goto('/organization/audit')
    await page.getByLabel('What').selectOption('share_link_viewed')
    await expect(page.getByRole('listitem').filter({ hasText: 'Opened a share link' }).filter({ hasText: 'an answer' })).toContainText(
      'Someone with the link',
    )
    // The downloads are kept by what the files held, never their words.
    await page.getByLabel('What').selectOption('kind:chats')
    await expect(page.getByRole('listitem').filter({ hasText: 'Downloaded an answer' })).toContainText('as Markdown')
    await expect(page.getByRole('listitem').filter({ hasText: 'Downloaded a chat' })).toContainText('1 answer, as a PDF')
  })

  await test.step("summarise a folder in a chat about it alone, write its answer again and edit its question, then compare two documents", async () => {
    await page.goto('/')
    await page.getByRole('link', { name: /^Supplier contracts/ }).click()
    // The connected folder's Archive holds one credit note, the only one against INV-2026-090: the answer reads it alone.
    // The chat scrolls into place as it loads, and a menu closes when anything scrolls: the menu is opened and its
    // item clicked again until the click lands, which a click checks before it clicks.
    await expect(async () => {
      await page.getByRole('treeitem', { name: /^Archive/ }).click({ button: 'right' })
      await page.getByRole('menuitem', { name: 'Summarise this folder' }).click({ timeout: 2_000 })
    }).toPass({ timeout: 60_000 })
    // A question is its bubble's paragraph: the chat's header names the chat by its first one too.
    const question = (words: string) => page.getByRole('paragraph').filter({ hasText: words })
    const summary = 'Summarise the documents in this folder: what each one is, and what they say together.'
    await expect(question(summary)).toBeVisible()
    const chooser = page.getByRole('button', { name: /^About: / })
    await expect(chooser).toHaveText('About: Archive')
    const regenerate = page.getByRole('button', { name: 'Regenerate' })
    await expect(regenerate).toBeVisible({ timeout: 30_000 })
    await expect(page.getByText(/INV-2026-090/).first()).toBeVisible()
    await expectAccessible(page, 'a chat about a folder')

    // Written again in its place, which counts as a question.
    const written = page.waitForResponse((response) => response.url().endsWith('/regenerate') && response.ok())
    await regenerate.click()
    await written
    await expect(regenerate).toBeVisible({ timeout: 30_000 })
    await expect(question(summary)).toHaveCount(1)

    // The question edited in place: Escape keeps it, and the edit is answered in its place and names the chat.
    const edit = page.getByRole('button', { name: 'Edit the question' })
    await edit.click()
    const editor = page.getByRole('textbox', { name: 'Edit the question' })
    await expect(editor).toBeFocused()
    await expectAccessible(page, 'a question being edited')
    await page.keyboard.press('Escape')
    await expect(edit).toBeFocused()
    await edit.click()
    await editor.fill('What does the credit note in the archive say?')
    await editor.press('Enter')
    await expect(page.getByPlaceholder('Ask a question about these documents')).toBeFocused()
    await expect(question('What does the credit note in the archive say?')).toBeVisible()
    await expect(question(summary)).toHaveCount(0)
    await expect(regenerate).toBeVisible({ timeout: 30_000 })
    await expect(page.getByRole('button', { name: 'What does the credit note in the archive say?', exact: true })).toBeVisible()

    // What the chat is about, changed from above the box: the menu opens at the folder chosen.
    await chooser.click()
    await expect(page.getByRole('menuitemradio', { name: 'Archive' })).toBeFocused()
    await expect(page.getByRole('menuitemradio', { name: 'Archive' })).toHaveAttribute('aria-checked', 'true')
    await expectAccessible(page, 'what a chat is about, to choose')
    const scoped = page.waitForResponse((response) => /\/api\/chats\/\d+$/.test(response.url()) && response.request().method() === 'PATCH')
    await page.getByRole('menuitemradio', { name: 'All documents' }).click()
    await scoped
    await expect(chooser).toHaveText('About: All documents')

    // Two invoices chosen together in the library are compared in a chat about them alone.
    await page.getByRole('treeitem', { name: 'invoice.md', exact: true }).click({ modifiers: ['ControlOrMeta'] })
    await page.getByRole('treeitem', { name: 'second-invoice.md', exact: true }).click({ modifiers: ['ControlOrMeta'] })
    await page.getByRole('treeitem', { name: 'second-invoice.md', exact: true }).click({ button: 'right' })
    await page.getByRole('menuitem', { name: 'Compare the two documents' }).click()
    await expect(question('Compare invoice.md and second-invoice.md: where do they agree, and where do they differ?')).toBeVisible()
    await expect(chooser).toHaveText('About: invoice.md and second-invoice.md')
    await expect(regenerate).toBeVisible({ timeout: 30_000 })
  })

  await test.step('pin a chat to the top of the list, and start a chat, reach the box and stop an answer by the keyboard', async () => {
    // Pinned, a chat leads the list under "Pinned", the focus following its row there, and stays so after a reload.
    const chats = page.getByRole('button', { name: /^Compare invoice\.md and second-invoice\.md/ })
    await chats.click()
    const credit = page.getByRole('listitem').filter({ hasText: 'What does the credit note in the archive say?' })
    await credit.getByRole('button', { name: 'Pin chat' }).click()
    await expect(credit.getByRole('button', { name: 'Unpin chat' })).toBeFocused()
    await expect(page.getByText('Pinned', { exact: true })).toBeVisible()
    await expectAccessible(page, 'the chat list with a pinned chat')
    await page.reload()
    await chats.click()
    await expect(page.getByText('Pinned', { exact: true })).toBeVisible()
    await credit.getByRole('button', { name: 'Unpin chat' }).click()
    await expect(credit.getByRole('button', { name: 'Pin chat' })).toBeFocused()
    await expect(page.getByText('Pinned', { exact: true })).toHaveCount(0)
    await page.keyboard.press('Escape')
    await expect(chats).toBeFocused()

    // The shortcuts, listed by Ctrl+/ (⌘ on a Mac) from anywhere, the focus going back where it was.
    const tree = page.getByRole('tree')
    await page.getByRole('treeitem', { name: 'invoice.md', exact: true }).click()
    await expect(tree).toBeFocused()
    await page.keyboard.press('ControlOrMeta+/')
    const shortcuts = page.getByRole('dialog', { name: 'Keyboard shortcuts' })
    await expect(shortcuts.getByRole('heading', { name: 'Keyboard shortcuts' })).toBeFocused()
    await expect(shortcuts).toContainText('Start a new chat')
    await expectAccessible(page, 'the keyboard shortcuts')
    await page.keyboard.press('Escape')
    await expect(shortcuts).toHaveCount(0)
    await expect(tree).toBeFocused()

    // Shift+Esc goes to the box a question is asked in, and Ctrl+Shift+O starts a new chat there.
    const composer = page.getByPlaceholder('Ask a question about these documents')
    await page.keyboard.press('Shift+Escape')
    await expect(composer).toBeFocused()
    await tree.focus()
    await page.keyboard.press('ControlOrMeta+Shift+O')
    await expect(page).toHaveURL(/[?&]chat=new/)
    await expect(composer).toBeFocused()

    // Escape in the box stops the answer being written.
    await holdNextTurn(request)
    await composer.fill('Which supplier sent the invoices?')
    await composer.press('Enter')
    await expect.poll(() => heldTurns(request)).toBe(1)
    await composer.press('Escape')
    await expect(page.getByText('Stopped.', { exact: true })).toBeVisible()
    await releaseTurns(request)
  })

  await test.step('list the favourites and the recent ones from the icons above the files, in another order and as a grid, then move a new invoice into a folder', async () => {
    const invoice = page.getByRole('treeitem', { name: 'invoice.md', exact: true })
    // What the library shows, a place an icon, the one shown pressed.
    const places = page.getByRole('group', { name: 'Show', exact: true })
    const allFiles = places.getByRole('button', { name: 'All files' })
    const favourites = places.getByRole('button', { name: 'Favourites' })
    await expect(allFiles).toHaveAttribute('aria-pressed', 'true')
    await expect(favourites).toHaveAttribute('aria-pressed', 'false')
    await expectAccessible(page, "the library's places")

    // A favourite, wherever it is, and the documents opened lately, the latest first. A scroll closes the menu, as above.
    await expect(async () => {
      await invoice.click({ button: 'right' })
      await page.getByRole('menuitem', { name: 'Add to favourites' }).click({ timeout: 2_000 })
    }).toPass({ timeout: 60_000 })
    await favourites.click()
    await expect(favourites).toHaveAttribute('aria-pressed', 'true')
    await expect(allFiles).toHaveAttribute('aria-pressed', 'false')
    // The path follows the heading "Project files", below the places, so that choosing one moves nothing under it.
    const path = page.getByRole('navigation', { name: 'Folder path' })
    await expect(path).toHaveText('Favourites')
    await expect(page.getByRole('treeitem')).toHaveCount(1)
    await expect(invoice).toHaveAccessibleDescription(/Favourite/)
    await expectAccessible(page, 'the favourites')
    await invoice.click()
    await places.getByRole('button', { name: 'Recent' }).click()
    await expect(page.getByRole('treeitem').first()).toHaveAccessibleName('invoice.md')
    await expect(page.getByRole('button', { name: 'Order: the latest opened first' })).toBeDisabled()
    // Measured with the invoice open, whose tab narrows the library: its buttons may take two lines.
    const top = (await places.boundingBox())?.y

    // Every file, by name from Z, then as a grid of tiles that the arrows move through.
    await allFiles.click()
    await expect(allFiles).toHaveAttribute('aria-pressed', 'true')
    await expect(path).toHaveCount(0)
    expect((await places.boundingBox())?.y).toBe(top)
    const order = page.getByRole('button', { name: /^Order: / })
    await order.click()
    await page.getByRole('menuitemradio', { name: 'Z to A' }).click()
    await expect(order).toHaveAccessibleName('Order: Name, z to a')
    const invoices = page.getByRole('treeitem', { name: /^(second-)?invoice\.md$/ })
    await expect(invoices.nth(0)).toHaveAccessibleName('second-invoice.md')
    await expect(invoices.nth(1)).toHaveAccessibleName('invoice.md')
    await page.getByRole('button', { name: 'Grid' }).click()
    const grid = page.getByRole('grid', { name: 'Files' })
    const tile = grid.getByRole('gridcell', { name: 'second-invoice.md', exact: true })
    // A click puts the keyboard on the tile without selecting it, and the arrows move it on.
    const tileId = (await tile.getAttribute('id')) ?? ''
    await tile.click()
    await expect(grid).toBeFocused()
    await expect(grid).toHaveAttribute('aria-activedescendant', tileId)
    await expect(tile).toHaveAttribute('aria-selected', 'false')
    await page.keyboard.press('ArrowRight')
    await expect(grid).not.toHaveAttribute('aria-activedescendant', tileId)
    await expectAccessible(page, 'the library as a grid')
    await page.getByRole('button', { name: 'List' }).click()
    await order.click()
    await page.getByRole('menuitemradio', { name: 'A to Z' }).click()

    // The next invoice, uploaded at the top, dragged into a folder of its own, which the tree then shows it under.
    await newFolder(page, 'Invoices')
    // The folder first: a drag pressed while it is still being made finds the rows moved under it.
    const invoicesFolder = page.getByRole('treeitem', { name: 'Invoices', exact: true })
    await expect(invoicesFolder).toBeVisible()
    await page.locator('input[name="files"]').setInputFiles(THIRD_INVOICE)
    const third = page.getByRole('treeitem', { name: 'third-invoice.md', exact: true })
    await expect(third).toHaveAttribute('aria-level', '1')
    await third.dragTo(invoicesFolder)
    await expect(page.getByText('“third-invoice.md” is now in “Invoices”.')).toBeVisible()
    await invoicesFolder.getByRole('button', { name: 'Expand' }).click()
    await expect(third).toHaveAttribute('aria-level', '2')
  })

  await test.step("set the invoice's date in its details, write it a note, and find its copy in another project among the duplicates", async () => {
    // Another project that holds the same invoice, which the invoice's tab and the projects page then name.
    await page.goto('/')
    await page.getByRole('button', { name: 'New project' }).click()
    await page.getByLabel('Name', { exact: true }).fill('Supplier archive')
    await page.getByRole('button', { name: 'Create project' }).click()
    await expect(page.locator('input[name="files"]')).toBeAttached()
    await page.locator('input[name="files"]').setInputFiles(INVOICE)
    await expect(page.getByRole('treeitem', { name: 'invoice.md', exact: true })).toBeVisible()

    await page.goto('/')
    await page.getByRole('link', { name: /^Supplier contracts/ }).click()
    await page.getByRole('treeitem', { name: 'invoice.md', exact: true }).click()
    // Its date as the member sets it, which later readings keep, what the reading found said beside it.
    const edit = page.getByRole('button', { name: 'Edit details' })
    await edit.click()
    const details = page.getByRole('dialog', { name: 'Details of “invoice.md”' })
    await expect(details.getByLabel('Title')).toBeFocused()
    await details.getByLabel('Date').fill('2026-03-31')
    await expectAccessible(page, "a document's details")
    await details.getByRole('button', { name: 'Save' }).click()
    await expect(details).toBeHidden()
    await expect(edit).toBeFocused()
    await edit.click()
    await expect(details.getByText(/^You set this\. The reading found/)).toBeVisible()
    await expect(details.getByRole('button', { name: 'Use the reading' })).toHaveCount(1)
    await expectAccessible(page, "a document's details a member set")
    await page.keyboard.press('Escape')
    await expect(edit).toBeFocused()

    // A note the project's members share, with who wrote it.
    await page.getByRole('button', { name: 'Add a note' }).click()
    // Exactly, since the tree's delivery and credit notes are named by "note" too.
    const writing = page.getByRole('textbox', { name: 'Note', exact: true })
    await expect(writing).toBeFocused()
    await writing.fill('Paid by transfer on 3 April.\nThe signed copy is in the safe.')
    await page.getByRole('button', { name: 'Save the note' }).click()
    const note = page.getByRole('region', { name: 'Note', exact: true })
    await expect(note).toContainText('The signed copy is in the safe.')
    await expect(note).toContainText(/Changed by .+ on /)
    await expectAccessible(page, "a document's note")

    // Its copy in the other project, from its tab and from the projects page's duplicates.
    await expect(page.getByRole('link', { name: 'Supplier archive', exact: true })).toBeVisible()
    await page.goto('/')
    await page.getByRole('link', { name: 'See the duplicates' }).click()
    await expect(page.getByRole('heading', { name: 'Duplicates' })).toBeVisible()
    await expect(page.getByText(/in more than one of the projects you see/)).toBeVisible()
    await expectAccessible(page, 'the duplicates')
    await page.getByRole('link', { name: 'Supplier archive', exact: true }).click()
    await expect(page).toHaveURL(/\/projects\/\d+\/documents\/\d+/)
    await expect(page.getByRole('link', { name: 'Supplier contracts', exact: true })).toBeVisible()
  })

  await test.step('index two invoices chosen together again and export them alone', async () => {
    await page.getByRole('link', { name: 'Supplier contracts', exact: true }).click()
    const invoice = page.getByRole('treeitem', { name: 'invoice.md', exact: true })
    const second = page.getByRole('treeitem', { name: 'second-invoice.md', exact: true })
    // Each chosen by its box, with no key held (a plain click selects nothing), and the bar under the files counting them.
    await invoice.click()
    await expect(invoice).toHaveAttribute('aria-selected', 'false')
    await invoice.getByTitle('Select', { exact: true }).click()
    await second.hover()
    await second.getByTitle('Select', { exact: true }).click()
    await expect(second).toHaveAttribute('aria-selected', 'true')
    await expect(invoice).toHaveAttribute('aria-selected', 'true')
    await expect(page.getByText('2 selected', { exact: true })).toBeVisible()
    await expectAccessible(page, 'the library with two files selected')
    // A scroll closes the menu, as above.
    await expect(async () => {
      await page.getByRole('button', { name: 'Actions', exact: true }).click()
      await page.getByRole('menuitem', { name: 'Index 2 documents again' }).click({ timeout: 2_000 })
    }).toPass({ timeout: 60_000 })
    await expect(page.getByText('Indexing 2 documents again.')).toBeVisible()

    // An export of the two alone, which the projects page lists with the project's own. The right-click moves the cursor
    // to a row at the tree's edge, under My files, which scrolls it into view and closes the menu: opened again, as above.
    await expect(async () => {
      await second.click({ button: 'right' })
      await page.getByRole('menuitem', { name: 'Export 2 documents' }).click({ timeout: 2_000 })
    }).toPass({ timeout: 60_000 })
    await expect(page.getByText(/^An export of 2 documents is being made\./)).toBeVisible()
    await page.getByRole('link', { name: 'Emudoc' }).click()
    const card = page.locator('div').filter({ has: page.getByRole('link', { name: /^Supplier contracts/ }) }).last()
    await card.getByRole('button', { name: 'Export' }).click()
    const panel = page.getByRole('region', { name: 'Export Supplier contracts' })
    await expect(panel.getByText(/^Chosen documents: 2 documents, [\d.]+ kB, until /)).toBeVisible({ timeout: 30_000 })
  })

  await test.step("search the library's names and text, filter its passages by a type and a folder, and search within what they found", async () => {
    // The project's card is a link named by everything on it.
    await page.getByRole('link', { name: /^Supplier contracts/ }).click()
    // The library's box finds the files whose names hold the words, in any case, from every folder, above the passages.
    const box = page.getByLabel('Search the project')
    await box.fill('INVOICE')
    await expect(page).toHaveURL(/[?&]q=INVOICE/)
    const filed = page.getByRole('treeitem', { name: 'third-invoice.md', exact: true })
    await expect(filed).toHaveAccessibleDescription(/in Invoices/)
    await expect(page.getByRole('treeitem', { name: 'invoice.md', exact: true })).toBeVisible()
    await expect(page.getByRole('treeitem', { name: 'Invoices', exact: true })).toHaveCount(0)
    await expect(page.getByRole('heading', { name: 'Passages' })).toBeVisible()
    await expectAccessible(page, 'the library searched, its files above its passages')
    await box.press('Escape')
    await expect(page).not.toHaveURL(/[?&]q=/)
    await expect(page.getByRole('treeitem', { name: 'Invoices', exact: true })).toBeVisible()

    // Words no file's name holds find the passages alone.
    const hit = (number: string) => page.getByRole('button', { name: new RegExp(`Invoice ${number}`) })
    await box.fill('total amount')
    await expect(page).toHaveURL(/[?&]q=total\+amount/)
    await expect(page.getByText('No file’s name holds “total amount”.')).toBeVisible()
    await expect(hit('INV-2026-0412').first()).toBeVisible()
    await expect(hit('INV-2026-0413').first()).toBeVisible()
    await expect(hit('INV-2026-0502').first()).toBeVisible()

    // Narrowed by a type and a folder in its dialog; the folder taken off, the first invoice is found again.
    const filter = page.getByRole('button', { name: 'Filter', exact: true })
    await filter.click()
    const filtering = page.getByRole('dialog', { name: 'Filter the search' })
    await filtering.getByRole('checkbox', { name: 'invoice', exact: true }).check()
    await filtering.getByLabel('In', { exact: true }).selectOption({ label: 'Invoices and the folders in it' })
    await expect(filtering).toContainText('Documents of type invoice, in Invoices.')
    await expectAccessible(page, 'a search being filtered')
    await filtering.getByRole('button', { name: 'Apply' }).click()
    await expect(filter).toBeFocused()
    await expect(page).toHaveURL(/[?&]in=\d+/)
    await expect(hit('INV-2026-0502').first()).toBeVisible()
    await expect(hit('INV-2026-0412')).toHaveCount(0)
    await page.getByRole('button', { name: 'Take off: in Invoices' }).click()
    await expect(filter).toBeFocused()
    await expect(page.getByRole('button', { name: 'Take off: of type invoice' })).toBeVisible()
    await expect(hit('INV-2026-0412').first()).toBeVisible()

    // Within what "March" found: the next words search only the invoice that mentions it.
    await box.fill('March')
    await expect(page).toHaveURL(/[?&]q=March/)
    await page.getByRole('button', { name: 'Search within these' }).click()
    await expect(box).toBeFocused()
    await expect(box).toHaveValue('')
    await expect(box).toHaveAttribute('placeholder', 'Search within these documents')
    await expect(page.getByText('What should be searched for?')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Take off: mentioning “March”' })).toBeVisible()
    await expectAccessible(page, 'a search within what another found')
    await box.fill('total amount')
    await expect(page).toHaveURL(/[?&]within=March/)
    await expect(page).toHaveURL(/[?&]q=total\+amount/)
    await expect(hit('INV-2026-0412').first()).toBeVisible()
    await expect(hit('INV-2026-0413')).toHaveCount(0)
    await expect(hit('INV-2026-0502')).toHaveCount(0)
    // With words in the box, whose "Clear search" is then a target axe measures.
    await expectAccessible(page, 'a search within what another found, with its words')
  })

  await test.step('search every project from the projects page, filter it to one of them and open a hit at its passage', async () => {
    await page.getByRole('link', { name: 'Emudoc', exact: true }).click()
    await page.getByLabel('Search all projects').fill('total amount')
    await page.getByLabel('Search all projects').press('Enter')
    await expect(page).toHaveURL(/\/search\?q=total\+amount$/)
    await expect(page.getByRole('heading', { name: 'Search', exact: true })).toBeVisible()
    const box = page.getByLabel('Search all projects')
    await expect(box).toBeFocused()
    await expect(box).toHaveValue('total amount')
    // One ranking over every project, each hit naming its project: the invoice is in both.
    const hit = (project: string) => page.getByRole('button').filter({ hasText: `${project} · ` }).filter({ hasText: 'INV-2026-0412' })
    await expect(hit('Supplier archive').first()).toBeVisible()
    await expect(hit('Supplier contracts').first()).toBeVisible()
    await expectAccessible(page, 'a search of every project')

    // Narrowed to one project in its dialog, which the address keeps.
    const filter = page.getByRole('button', { name: 'Filter', exact: true })
    await filter.click()
    const filtering = page.getByRole('dialog', { name: 'Filter the search' })
    await filtering.getByRole('checkbox', { name: 'Supplier archive', exact: true }).check()
    await expect(filtering).toContainText('Documents in “Supplier archive”.')
    await expectAccessible(page, 'a search of every project being filtered')
    await filtering.getByRole('button', { name: 'Apply' }).click()
    await expect(filter).toBeFocused()
    await expect(page).toHaveURL(/[?&]project=\d+/)
    await expect(page.getByRole('button', { name: 'Take off: in “Supplier archive”' })).toBeVisible()
    await expect(hit('Supplier archive').first()).toBeVisible()
    await expect(hit('Supplier contracts')).toHaveCount(0)
    await expectAccessible(page, 'a search of every project narrowed to one')

    // A hit opens its document in its own project, at the passage: the copy whose tab names the other.
    await hit('Supplier archive').first().click()
    await expect(page).toHaveURL(/\/projects\/\d+\/documents\/\d+\?(.*&)?passage=\d+/)
    await expect(page.getByText(/^Showing: /)).toBeVisible()
    await expect(page.getByRole('link', { name: 'Supplier contracts', exact: true })).toBeVisible()
  })
})
