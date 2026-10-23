import { readFile } from 'node:fs/promises'
import { join } from 'node:path'
import { expect, test } from '@playwright/test'
import type { BrowserContext, Page } from '@playwright/test'
import { expectAccessible } from './axe.ts'
import { linkMailedTo, messageTo } from './mail.ts'
import { makeOperator, movePlan } from './operator.ts'
import { holdNextTurn, refuseEmbeddings, releaseTurns } from './standin.ts'

// The member's way through the app, end to end: sign up through the link the app mails, find the
// organisation's first steps listed, skip the tour the first project shows, ask the sample
// project the organisation starts with one of its questions, make a project, upload a contract,
// begun offline and waiting for the connection, wait for it to be indexed, find it by searching, ask about it, stop the
// answer while the model thinks and try it again, open the passage the answer
// cites, which the Free plan's chat follows with the way to a plan for a team, upload a folder,
// whose panel follows each file while its request is held, and put it in the trash, export the project,
// download it and open the bell's notice that it is ready, start a second
// organisation, choose its settings, read them in its audit log and download it, hide its first
// steps, delete it and keep it, and switch back, and, on Team, invite a colleague as a
// viewer from under that answer, who makes an account from the invitation's link, ends her tour, finds
// the project and is offered no change to it until made a member, write to support from the
// help menu, which mails the page written from, read the help pages and what's new, then make the project
// private, which she no longer finds until chosen for it, and share the chat, which the
// colleague then reads but does not ask in, and last ask the question that uses the month's
// questions, which the bell tells, and open that notice from the other organisation; then, made
// an operator, read the organisation in the operator's area, which nobody else is sent, raise its
// agreed questions there, turn its extraction templates on, which its audit log keeps, view the
// colleague's account, which she let support see, and switch the model's provider off and on
// again; and last, make the project's own invoice template from the built-in one, a field
// added, and delete it, then upload an invoice, read for the built-in one, and find its facts in
// the project's facts table, sorted, kept by a condition and opened where they were found, one
// checked and another corrected, which the table's rows to review, its CSV file and the audit
// log keep, and then a field added to the project's own invoice template and the invoice read again for it,
// what the reading changed listed, the corrected total kept. The answer
// is the stand-in's (e2e/standin.py): the first sentence of the passage its search found best,
// with that passage's reference. Along the way each kind of page, panel and dialog is checked
// against axe-core's rules for WCAG 2.2 AA in both themes (e2e/axe.ts), and the keyboard's focus
// is followed where something it was on goes away.
const CONTRACT = join(import.meta.dirname, 'fixtures', 'service-contract.md')
// An invoice whose fields are lines of their own, which the stand-in reads for the invoice template,
// and a delivery period, which only a template that asks for it reads.
const INVOICE = join(import.meta.dirname, 'fixtures', 'invoice.md')
const PURCHASE_ORDER = join(import.meta.dirname, 'fixtures', 'purchase-order.md')
// A folder of two delivery notes, one of them in a folder of its own.
const DELIVERIES = join(import.meta.dirname, 'fixtures', 'deliveries')
// The project every sign-up's organisation starts with (backend/app/sample).
const SAMPLE = 'Sample: Acme supplier contract'

test('a new member uploads a contract, finds it, asks about it and opens the cited passage', async ({ page, browser, request }) => {
  // About 90 s on a machine of its own; CI's runners share one (ci-runner/), where it has taken
  // over 120 s beside the backend suites and an image build.
  test.setTimeout(300_000)
  // The colleague's browser, from the invitation on.
  let theirs: BrowserContext
  let grace: Page
  // The first organisation's, for the operator's command, and the first member's address.
  let slug = ''
  let address = ''
  // The projects page's list of the organisation's first steps.
  const firstSteps = page.getByRole('region', { name: 'Get started' })
  // The chat's suggestion to invite the team, under an answer about the organisation's documents.
  const inviteTeam = page.getByRole('region', { name: 'Invite your team' })

  await test.step('sign up', async () => {
    // A new address every run, so that a server kept from an earlier run takes it too.
    address = `ada.${Date.now()}@example.com`
    await page.goto('/login')
    await expectAccessible(page, 'the sign-in page')
    await page.goto('/signup')
    await expectAccessible(page, 'the sign-up page')
    await page.getByLabel('Email', { exact: true }).fill(address)
    await page.getByRole('button', { name: 'Continue' }).click()
    await expect(page.getByRole('heading', { name: 'Check your email' })).toBeVisible()
    await page.goto(await linkMailedTo(address))
    await expect(page.getByRole('heading', { name: 'Finish creating your account' })).toBeVisible()
    await expect(page.getByText(address)).toBeVisible()
    await expectAccessible(page, 'the page that finishes a sign-up')
    await page.getByLabel('Name', { exact: true }).fill('Ada Lovelace')
    await page.getByLabel('Password', { exact: true }).fill('a sentence to remember')
    await page.getByLabel(/I accept the terms of service/).check()
    await page.getByRole('button', { name: 'Create account' }).click()
    await expect(page.getByRole('heading', { name: 'Projects' })).toBeVisible()
    await expect(firstSteps).toContainText('0 of 3 done')
    await expectAccessible(page, 'the projects page with its first steps')
  })

  await test.step('skip the tour, and ask the sample project one of its questions', async () => {
    await page.getByText(SAMPLE).click()
    await expect(page).toHaveURL(/\/projects\/\d+$/)
    // The first project opened shows the tour: the sample takes no uploads, so it has no card.
    const tour = page.getByRole('dialog')
    await expect(tour).toContainText('1 of 6')
    await expect(tour).toContainText('The library')
    await expect(page.getByRole('button', { name: 'Next', exact: true })).toBeFocused()
    await expectAccessible(page, 'the tour')
    await page.getByRole('button', { name: 'Next', exact: true }).click()
    await expect(tour).toContainText('Search')
    const seen = page.waitForResponse((response) => response.url().endsWith('/api/auth/onboarding'))
    await page.getByRole('button', { name: 'Skip the tour' }).click()
    expect((await seen).status()).toBe(200)
    await expect(tour).toHaveCount(0)
    await expect(page.getByText(/^Emudoc’s sample documents, which count against nothing/)).toBeVisible()
    await expect(page.getByRole('button', { name: 'Upload', exact: true })).toHaveCount(0)
    // Indexed already: the job each document's copy queued makes its vectors alone.
    const ready = page.getByRole('img', { name: /Embeddings: done/ })
    await expect(ready).toHaveCount(8, { timeout: 30_000 })
    await expect(ready.first()).toHaveAccessibleName(/Summaries and facts: done/)
    await page.getByRole('button', { name: /^Does invoice INV-2026-0503 apply the volume discount/ }).click()
    await expect(page).toHaveURL(/[?&]chat=\d+/)
    await expect(page.getByRole('button', { name: '1', exact: true })).toBeVisible({ timeout: 30_000 })
    await expect(page.getByText('Thinking')).toHaveCount(0)
    // The sample's answer suggests no invitation: its documents are no work a team shares.
    await expect(inviteTeam).toHaveCount(0)
    await expectAccessible(page, 'a project with an answer')
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
    await expect(firstSteps).toContainText('1 of 3 done')
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
    await expect(page.getByRole('button', { name: 'Waiting for the connection' })).toBeDisabled()
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

  await test.step('search the project', async () => {
    await page.getByLabel('Search', { exact: true }).fill('written notice')
    const hit = page.getByRole('button', { name: /Service contract/ })
    await expect(hit.first().locator('mark', { hasText: /^notice$/i }).first()).toBeVisible()
    await page.getByRole('button', { name: 'Clear search' }).click()
    await expect(page).not.toHaveURL(/[?&]q=/)
  })

  await test.step('ask, stop the answer while the model thinks, try it again and get an answer that cites the contract', async () => {
    const composer = page.getByPlaceholder('Ask a question about these documents')
    await holdNextTurn(request)
    await composer.fill('How much notice does ending the contract take?')
    await composer.press('Enter')
    await expect(page).toHaveURL(/[?&]chat=\d+/)
    await expect(page.getByText('Thinking')).toBeVisible()
    await page.getByRole('button', { name: 'Stop', exact: true }).click()
    await expect(page.getByText('Stopped.', { exact: true })).toBeVisible()
    // Tried again in its place, which counts no question: the month's third, at the end, is the
    // one that uses them all. The held turn answers after, into a try that is no longer its own.
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
    // The Free plan is for one: the suggestion says so, and offers no form.
    await expect(inviteTeam).toContainText('Its plan has room for you alone.')
    await expect(inviteTeam).toContainText('Your organisation is on the Free plan.')
    await expect(inviteTeam.getByLabel('Email', { exact: true })).toHaveCount(0)
  })

  await test.step('open the cited passage', async () => {
    await page.getByRole('button', { name: '1', exact: true }).click()
    await expect(page).toHaveURL(/\/documents\/\d+\?(.*&)?passage=\d+/)
    await expect(page.getByRole('heading', { name: 'Service contract', exact: true })).toBeVisible()
    await expect(page.getByText('Showing: 4 Termination')).toBeVisible()
    await expect(page.getByText(/^Either party may end this contract .* on the first page\.$/)).toBeInViewport()
    await expectAccessible(page, 'a document beside its chat')
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

    await page.getByRole('button', { name: 'New folder' }).click()
    await page.getByLabel('New name').fill('Contracts')
    await page.getByLabel('New name').press('Enter')
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
    await expect(page.getByRole('button', { name: 'Upload a folder' })).toBeDisabled()
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
    const tab = page.getByRole('tabpanel')
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

  await test.step('start a second organisation, choose its settings, delete it and keep it, and switch back while another tab shows it', async () => {
    // From the projects page: a menu closes when anything scrolls, as the passage still may.
    await page.getByRole('link', { name: 'Emudoc', exact: true }).click()
    await expect(page.getByRole('heading', { name: 'Projects' })).toBeVisible()
    const switcher = page.getByRole('button', { name: "Ada Lovelace's workspace" })
    await switcher.click()
    // Tab leaves a menu, closing it, and goes on from its button.
    await expect(page.getByRole('menuitem', { name: "Ada Lovelace's workspace" })).toBeFocused()
    await page.keyboard.press('Shift+Tab')
    await expect(page.getByRole('menu')).toHaveCount(0)
    await expect(switcher).toBeFocused()
    await switcher.click()
    await page.getByRole('menuitem', { name: 'New organisation' }).click()
    await page.getByLabel('Name', { exact: true }).fill('Lovelace Legal')
    await page.getByRole('button', { name: 'Create organisation' }).click()
    // A new organisation's first steps, which hidden leave the empty page its own way on.
    await expect(firstSteps).toContainText('0 of 3 done')
    await expect(page.getByText('No projects yet')).toHaveCount(0)
    await page.getByRole('button', { name: 'Hide the first steps' }).click()
    await expect(firstSteps).toHaveCount(0)
    await expect(page.getByText('No projects yet')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Create a project' })).toBeVisible()
    // Its settings: the language its documents are searched in, and chats kept for less time
    // than its plan keeps them, which says what it deletes before it is saved.
    await page.goto('/organization')
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
    await chatDays.getByRole('button', { name: 'Save', exact: true }).click()
    await expect(page.getByText('The organisation keeps them 7 days, in its settings.')).toBeVisible()
    await expect(page.getByLabel('Chats kept')).toHaveValue('7')
    // Each change is in its audit log, which an owner reads by what was done, and downloads.
    await page.getByRole('link', { name: 'Audit log' }).click()
    await expect(page.getByRole('heading', { name: 'Audit log' })).toBeVisible()
    await page.getByLabel('What').selectOption('settings_changed')
    const changes = page.getByRole('listitem').filter({ hasText: 'Changed a setting' })
    await expect(changes).toHaveCount(2)
    await expect(changes.first()).toContainText('Chats kept: as long as the plan → 7 days after the last question')
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
    // An owner deletes it, confirmed by the password, which every page then says, until an owner keeps it.
    await page.goto('/organization')
    const deletion = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Delete the organisation' }) })
    await deletion.getByRole('button', { name: 'Delete…' }).click()
    await deletion.getByLabel('Your password').fill('a sentence to remember')
    await deletion.getByRole('button', { name: 'Delete Lovelace Legal' }).click()
    await expect(page.getByText(/^Lovelace Legal is set to be deleted on .+; an owner can keep it\.$/)).toHaveCount(2)
    await page.getByRole('link', { name: 'Emudoc' }).click()
    await expect(page.getByRole('link', { name: 'Keep it' })).toBeVisible()
    await page.getByRole('link', { name: 'Keep it' }).click()
    await deletion.getByRole('button', { name: 'Keep Lovelace Legal' }).click()
    await expect(page.getByText(/is set to be deleted/)).toHaveCount(0)
    await page.goto('/')
    const other = await page.context().newPage()
    await other.goto('/')
    await expect(other.getByRole('button', { name: 'Lovelace Legal' })).toBeVisible()

    await page.getByRole('button', { name: 'Lovelace Legal' }).click()
    await page.getByRole('menuitem', { name: "Ada Lovelace's workspace" }).click()
    await expect(page.getByText('Supplier contracts')).toBeVisible()
    // The other tab still shows the new organisation: what it asks is refused, and it starts
    // again in the one the session works in, with nothing made in the wrong one.
    await other.getByRole('button', { name: 'New project' }).click()
    await other.getByLabel('Name', { exact: true }).fill('Lost')
    await other.getByRole('button', { name: 'Create project' }).click()
    await expect(other.getByText('Supplier contracts')).toBeVisible()
    await expect(other.getByText('Lost')).toHaveCount(0)
  })

  await test.step('invite a viewer, who makes an account from the link, reads, and is made a member', async () => {
    await page.goto('/organization')
    slug = (await page.locator('code').filter({ hasText: /^ada-lovelace/ }).textContent()) ?? ''
    movePlan(slug, 'team', '--seats', '2')
    await page.reload()
    // The plan's figures, with the seats bought for its members.
    const plan = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Plan', exact: true }) })
    await expect(plan.getByRole('definition').first()).toHaveText('Team')
    await expect(plan.getByRole('definition').filter({ hasText: 'seats' })).toHaveText('2 seats')
    // With room on the plan, the answer's suggestion invites from the chat, which opens the member's latest.
    await page.getByRole('link', { name: 'Emudoc' }).click()
    await page.getByText('Supplier contracts').click()
    await expect(page.getByRole('button', { name: /^How much notice/ })).toBeVisible()
    const colleague = `grace.${Date.now()}@example.com`
    await inviteTeam.getByLabel('Email', { exact: true }).fill(colleague)
    await inviteTeam.getByLabel('Role', { exact: true }).selectOption('viewer')
    await inviteTeam.getByRole('button', { name: 'Invite', exact: true }).click()
    await expect(inviteTeam).toContainText(`An invitation is on its way to ${colleague}.`)
    await inviteTeam.getByRole('link', { name: 'See the invitations' }).click()
    await expect(page).toHaveURL(/\/organization#invitations$/)
    await expect(page.getByRole('listitem').filter({ hasText: colleague })).toContainText('Viewer')

    // Another browser, signed in as nobody, as the colleague's is.
    theirs = await browser.newContext()
    grace = await theirs.newPage()
    await grace.goto(await linkMailedTo(colleague))
    await expect(grace.getByRole('heading', { name: "Join Ada Lovelace's workspace" })).toBeVisible()
    await expectAccessible(grace, "an invitation's page")
    await grace.getByLabel('Name', { exact: true }).fill('Grace Hopper')
    await grace.getByLabel('Password', { exact: true }).fill('another sentence to remember')
    await grace.getByLabel(/I accept the terms of service/).check()
    await grace.getByRole('button', { name: 'Create account' }).click()
    await expect(grace.getByText('Supplier contracts')).toBeVisible()
    await expect(grace.getByRole('button', { name: "Ada Lovelace's workspace" })).toBeVisible()
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
    await expect(grace.getByRole('button', { name: 'Upload', exact: true })).toBeVisible()
  })

  await test.step('read what the organisation holds and has used of its plan', async () => {
    await page.getByRole('link', { name: 'See what it holds and has used' }).click()
    await expect(page.getByRole('heading', { name: 'Usage', exact: true })).toBeVisible()
    const section = (title: string) => page.locator('section').filter({ has: page.getByRole('heading', { name: title, exact: true }) })
    // The two questions asked, of the Team plan's month; both seats taken; the sample not counted.
    await expect(section('This month').getByRole('definition').nth(1)).toContainText('2 of 5,000')
    await expect(section('Held now').getByRole('definition').nth(0)).toContainText('Not counted: the sample project.')
    await expect(section('Held now').getByRole('definition').nth(3)).toContainText('2 of 2')
    await expect(section('Day by day').getByRole('figure', { name: /^Questions/ })).toContainText('2 questions in all')
    await expectAccessible(page, 'the usage page')
  })

  await test.step('write to support from the help menu, which mails the page the member wrote from', async () => {
    await page.getByRole('button', { name: 'Help', exact: true }).click()
    const help = page.getByRole('dialog', { name: 'Help' })
    await expectAccessible(page, 'the help menu')
    await help.getByRole('button', { name: 'A question' }).click()
    await help.getByLabel('Message').fill('Can one project hold two price lists?')
    await help.getByRole('button', { name: 'Send' }).click()
    await expect(help.getByRole('status')).toContainText(`Sent to support@example.com. The answer comes to ${address}`)
    const mail = await messageTo('support@example.com')
    expect(mail).toContain('A member asks a question, reference ')
    expect(mail).toContain('Can one project hold two price lists?')
    expect(mail).toContain('/organization/usage')
    await page.keyboard.press('Escape')
    await expect(help).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Help', exact: true })).toBeFocused()
  })

  await test.step('read the help pages from the help menu, find one by a word and follow its link into the app', async () => {
    await page.getByRole('button', { name: 'Help', exact: true }).click()
    const menu = page.getByRole('dialog', { name: 'Help' })
    // The status page is outside the deployment (STATUS_PAGE_URL in e2e/serve.sh).
    await expect(menu.getByRole('link', { name: 'Service status' })).toHaveAttribute('href', 'https://status.example.com')
    await menu.getByRole('link', { name: 'Help pages' }).click()
    await expect(page.getByRole('heading', { name: 'Help', exact: true })).toBeVisible()
    await page.getByLabel('Search the help').fill('zip file')
    await expect(page.getByRole('link', { name: /^Getting started/ })).toHaveCount(0)
    await page.getByRole('link', { name: /^Your data/ }).click()
    const article = page.getByRole('article')
    await expect(article.getByRole('heading', { name: 'Your data: exports, the trash and deletion' })).toBeVisible()
    // Its figures are the deployment's own (backend/app/help).
    await expect(article).toContainText('kept 7 days for whoever asked for it')
    await expectAccessible(page, 'a help page')
    await article.getByRole('link', { name: 'account page' }).click()
    await expect(page).toHaveURL(/\/account$/)
    await expectAccessible(page, 'the account page')
  })

  await test.step("read what's new from the help menu: the changelog's newest entries for members", async () => {
    const changelog = await readFile(join(import.meta.dirname, '..', '..', 'CHANGELOG.md'), 'utf8')
    // The newest entry's first words, before any code in it, which the page sets apart.
    const newest = /^Members:\n\n- ([^`\n]+)/m.exec(changelog)?.[1].split(' ').slice(0, 6).join(' ') ?? ''
    expect(newest).not.toBe('')
    await page.getByRole('button', { name: 'Help', exact: true }).click()
    await page.getByRole('dialog', { name: 'Help' }).getByRole('link', { name: /^What's new/ }).click()
    await expect(page.getByRole('heading', { name: "What's new", exact: true })).toBeVisible()
    await expect(page.getByRole('region').first()).toContainText(newest)
    await expectAccessible(page, "what's new")
    // Read, so the help menu marks nothing, whatever it marked before.
    await page.getByRole('link', { name: 'Emudoc', exact: true }).click()
    await expect(page.getByRole('button', { name: 'Help', exact: true })).not.toHaveAttribute('aria-describedby')
  })

  await test.step('make a read key, which a program lists the projects with and changes nothing by', async () => {
    await page.goto('/organization')
    const keys = page.locator('section').filter({ has: page.getByRole('heading', { name: 'API keys', exact: true }) })
    await keys.getByLabel('Name', { exact: true }).fill('Invoices sync')
    await keys.getByRole('button', { name: 'Make a key' }).click()
    const key = (await keys.locator('code').first().textContent()) ?? ''
    expect(key).toMatch(/^emk_/)
    // A program sends the key alone, with no cookie and none of the app's headers.
    const bearer = { Authorization: `Bearer ${key}` }
    const listed = await request.get('/api/projects', { headers: bearer })
    expect(listed.status()).toBe(200)
    expect((await listed.json()).map((project: { name: string }) => project.name)).toContain('Supplier contracts')
    expect((await request.post('/api/projects', { headers: bearer, data: { name: 'Mine' } })).status()).toBe(403)
    expect((await request.get('/api/organization/keys', { headers: bearer })).status()).toBe(403)
    await keys.getByRole('button', { name: 'I have saved it' }).click()
    await expect(keys.locator('code')).toHaveCount(0)
    await page.reload()
    await expect(keys.getByRole('listitem').filter({ hasText: 'Invoices sync' })).toContainText('last used')
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
    // The team is in: the chat suggests inviting it no more.
    await expect(page.getByRole('button', { name: 'Share', exact: true })).toBeVisible()
    await expect(inviteTeam).toHaveCount(0)
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

    // She asks for help, and lets Emudoc's support see her account here for a day.
    await grace.goto('/account')
    const support = grace.locator('section').filter({ has: grace.getByRole('heading', { name: "Emudoc's support" }) })
    await expect(support).toContainText("Emudoc's support cannot see your account in Ada Lovelace's workspace.")
    await support.getByRole('button', { name: 'Let support in for a day' }).click()
    await expect(support).toContainText("Emudoc's support may see your account in Ada Lovelace's workspace until")
    await expect(support.getByRole('button', { name: 'Withdraw' })).toBeVisible()
    await theirs.close()
  })

  await test.step("be told the month's questions are used, and open the notice from the other organisation", async () => {
    // Three questions a month: the third uses them all, which the owners are told as it is asked.
    movePlan(slug, 'team', '--set', 'questions_per_month=3')
    const asked = page.waitForResponse((response) => response.request().method() === 'POST' && /\/messages$/.test(response.url()))
    const composer = page.getByPlaceholder('Ask a question about these documents')
    await composer.fill('Who may end the contract?')
    await composer.press('Enter')
    expect((await asked).status()).toBe(201)

    await page.getByRole('link', { name: 'Emudoc' }).click()
    await page.getByRole('button', { name: "Ada Lovelace's workspace" }).click()
    await page.getByRole('menuitem', { name: 'Lovelace Legal' }).click()
    await expect(page.getByText('No projects yet')).toBeVisible()
    await page.getByRole('button', { name: 'Notifications, 1 unread' }).click()
    const notice = page.getByRole('dialog', { name: 'Notifications' }).getByRole('button', { name: /used all its questions for the month/ })
    await expect(notice).toContainText("Ada Lovelace's workspace has used all the 3 questions it may ask")
    // Opened in the organisation it is about, read.
    await notice.click()
    await expect(page).toHaveURL(/\/organization\/usage$/)
    await expect(page.getByRole('button', { name: "Ada Lovelace's workspace" })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Notifications', exact: true })).toBeVisible()
  })

  await test.step("be made an operator, read the organisation in the operator's area, change its plan and its flags, and switch a provider", async () => {
    // A member is sent the members' page at the area's address, as for any page there is not.
    await page.goto('/operator')
    await expect(page.getByText('Page not found.')).toBeVisible()

    makeOperator(address)
    await page.goto('/operator')
    await expect(page.getByRole('heading', { name: 'Organisations', exact: true })).toBeVisible()
    await page.getByLabel('Find organisations').fill(slug)
    await page.getByLabel('Find organisations').press('Enter')
    await page.getByRole('link', { name: "Ada Lovelace's workspace" }).click()
    await expect(page.getByRole('heading', { name: "Ada Lovelace's workspace" })).toBeVisible()
    const members = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Members', exact: true }) })
    await expect(members.getByRole('row').filter({ hasText: address })).toContainText('owner')
    await expect(members.getByRole('row').filter({ hasText: 'Grace Hopper' })).toContainText('member')
    // The month's three questions, which the figure agreed with it allows, raised from the page.
    const plan = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Plan', exact: true }) })
    const questions = plan.getByRole('row').filter({ hasText: 'questions_per_month' })
    await expect(questions).toContainText('agreed')
    await plan.getByRole('button', { name: 'Change the plan' }).click()
    await expect(plan.getByLabel('questions_per_month')).toHaveValue('3')
    await plan.getByLabel('questions_per_month').fill('10')
    await plan.getByRole('button', { name: 'Save' }).click()
    await expect(questions).toContainText('10')
    await expect(questions).toContainText('agreed')
    // A feature being rolled out to none, the extraction templates, turned on for it alone.
    const flags = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Feature flags', exact: true }) })
    const preview = flags.getByRole('row').filter({ hasText: 'extraction_templates' })
    await expect(preview).toContainText('off, as rolled out')
    await expectAccessible(page, "an organisation in the operator's area")
    const chosen = page.waitForResponse((response) => response.url().endsWith('/flags/extraction_templates') && response.ok())
    await preview.getByLabel('Feature flag extraction_templates').selectOption('on')
    await chosen
    await expect(preview).toContainText('on, chosen here')

    // Grace let support in: her account, as she sees it, read only, until the view ends.
    await members.getByRole('button', { name: 'View as Grace Hopper' }).click()
    await expect(page.getByText(/^You are viewing Grace Hopper's account in Ada Lovelace's workspace as Emudoc's support, read only/)).toBeVisible()
    await expect(page.getByText('Supplier contracts')).toBeVisible()
    await page.getByRole('button', { name: 'End the view' }).click()
    await expect(page.getByRole('heading', { name: 'Organisations', exact: true })).toBeVisible()

    // The flag across the deployment: on for this organisation alone, which its owner reads in its audit log.
    await page.getByRole('navigation', { name: 'Views' }).getByRole('link', { name: 'Flags' }).click()
    const rollout = page.getByRole('row').filter({ hasText: 'extraction_templates' })
    await expect(rollout.getByRole('link', { name: "Ada Lovelace's workspace" })).toBeVisible()
    await page.goto('/organization/audit')
    // The whole log first, which holds every kind of row this organisation has, its export's among them.
    await expect(page.getByRole('listitem').filter({ hasText: 'Exported a project' })).toContainText('Supplier contracts')
    await page.getByLabel('What').selectOption('flag_changed')
    await expect(page.getByRole('listitem').filter({ hasText: 'Feature flag changed' })).toContainText("extraction templates: on · by Emudoc's operators")
    await page.goto('/operator/jobs')
    await expect(page.getByRole('heading', { name: 'Jobs', exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Ask again' })).toBeVisible()
    // The worker the server runs in its own process says it runs, so the page warns of none.
    const workers = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Workers' }) })
    await expect(workers.getByRole('row').filter({ hasText: 'running, seen' })).toHaveCount(1)
    await expect(page.getByText('No worker has been seen running')).toHaveCount(0)
    await expectAccessible(page, "the operator's jobs")
    await page.getByRole('navigation', { name: 'Views' }).getByRole('link', { name: 'Services' }).click()
    await expect(page.getByRole('heading', { name: 'What the watch sees' })).toBeVisible()
    await expectAccessible(page, "the operator's services")
    // The stand-in's provider switched off, which asks first, and on again: last, since nothing asks a model after it.
    const ollama = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Providers' }) }).getByRole('row').filter({ hasText: 'ollama' })
    await ollama.getByRole('button', { name: 'Switch ollama off' }).click()
    await page.getByRole('dialog', { name: 'Switch ollama off?' }).getByRole('button', { name: 'Switch it off' }).click()
    await expect(ollama).toContainText('switched off since')
    await expect(page.getByText('is switched off: an operator switched its provider, ollama, off').first()).toBeVisible()
    await ollama.getByRole('button', { name: 'Switch ollama on' }).click()
    await expect(ollama.getByRole('cell').nth(2)).toHaveText('on')
    await expect(ollama.getByRole('button', { name: 'Switch ollama off' })).toBeVisible()
  })

  await test.step("make the project's own invoice template from the built-in one, then delete it", async () => {
    // The flag turned on above shows the library's extraction templates, the built-in ones among them.
    await page.goto('/')
    await page.getByRole('link', { name: /^Supplier contracts/ }).click()
    await page.getByRole('button', { name: 'Extraction templates' }).click()
    const heading = page.getByRole('heading', { name: 'Extraction templates' })
    const invoice = page.getByRole('list', { name: 'Templates' }).locator(':scope > li').filter({ hasText: /^Invoice/ })
    await expect(invoice).toContainText('Built in')
    await expect(invoice).toContainText('For invoice documents · 16 fields, 5 required')
    await invoice.getByText('Its fields').click()
    await expect(invoice).toContainText('invoice_number · Text, required · The invoice\'s own number.')
    await expectAccessible(page, 'the extraction templates')

    // Customised, it is the project's own, a field added, whose name its facts take as the server makes it.
    await invoice.getByRole('button', { name: 'Customise Invoice' }).click()
    const form = page.getByRole('form', { name: 'Your own Invoice template' })
    await expect(form.getByLabel('Name', { exact: true }).first()).toBeFocused()
    await form.getByRole('button', { name: 'Add a field' }).click()
    const added = form.getByRole('group', { name: 'Field 17' })
    await expect(added.getByLabel('Name')).toBeFocused()
    await added.getByLabel('Name').fill('VAT number')
    await expect(added).toContainText('Its facts are named vat_number.')
    await added.getByLabel('Every document of the kind states it').check()
    await expectAccessible(page, 'an extraction template being written')
    await form.getByRole('button', { name: 'Save' }).click()
    await expect(page.getByText('Saved “Invoice”.')).toBeVisible()
    await expect(heading).toBeFocused()
    await expect(invoice).toContainText('Replaces the built-in one')
    await expect(invoice).toContainText('17 fields, 6 required')
    // What is indexed from now on is read for it; no invoice is here yet to read again.
    await expect(page.getByText('Documents uploaded from now on are read for the templates as they are.')).toBeVisible()
    await expect(page.getByRole('button', { name: /^Read (it|them) again$/ })).toHaveCount(0)

    // Deleted for good after a question, the built-in one applies again.
    await invoice.getByRole('button', { name: 'Delete Invoice' }).click()
    const question = page.getByRole('dialog', { name: 'Delete the template “Invoice”?' })
    await expect(question).toContainText("The project's invoice documents are read for the built-in invoice template")
    await expectAccessible(page, 'the question before a template is deleted')
    await question.getByRole('button', { name: 'Delete it' }).click()
    await expect(heading).toBeFocused()
    await expect(invoice).toContainText('Built in')

    // Both are the organisation's to ask about.
    await page.goto('/organization/audit')
    await page.getByLabel('What').selectOption('template_created')
    await expect(page.getByRole('listitem').filter({ hasText: 'Made an extraction template' })).toContainText(
      'Invoice · for invoice documents, 17 fields',
    )
  })

  await test.step("upload an invoice, read for its template, and find its facts in the project's facts table", async () => {
    // Uploaded now that the templates are on, it is read for the built-in invoice template.
    await page.goto('/')
    await page.getByRole('link', { name: /^Supplier contracts/ }).click()
    await page.locator('input[name="files"]').setInputFiles(INVOICE)
    await expect(page.getByRole('treeitem', { name: 'invoice.md' })).toHaveAccessibleDescription(/^Ready/, { timeout: 60_000 })
    await page.getByRole('button', { name: 'Extraction templates' }).click()
    const invoice = page.getByRole('list', { name: 'Templates' }).locator(':scope > li').filter({ hasText: /^Invoice/ })
    await expect(invoice).toContainText('1 document read for it')
    await invoice.getByRole('button', { name: 'Facts table of Invoice' }).click()
    // The button went with the view: the keyboard goes on from the table's heading.
    await expect(page.getByRole('heading', { name: 'Invoice facts' })).toBeFocused()
    const grid = page.getByRole('grid', { name: 'Facts of invoice documents' })
    const row = grid.getByRole('row').filter({ hasText: 'INV-2026-0412' })
    await expect(row).toContainText('EUR 1,250.00')
    await expect(row).toContainText('Acme Logistics Ltd')
    await expect(page.getByText('1 document, 1 to review', { exact: true })).toBeVisible()
    const total = grid.getByRole('columnheader', { name: 'total amount', exact: true })
    await total.getByRole('button').click()
    await expect(total).toHaveAttribute('aria-sort', 'ascending')

    // One stop of Tab, the arrows between the cells; a cell's quote shows on focus, and Enter opens its facts, each to open where it was found.
    await grid.getByRole('columnheader', { name: 'Document', exact: true }).getByRole('button').focus()
    await page.keyboard.press('ArrowDown')
    await page.keyboard.press('ArrowRight')
    const number = grid.getByRole('button', { name: 'INV-2026-0412', exact: true })
    await expect(number).toBeFocused()
    await expect(page.getByText('Quoted as “Invoice number: INV-2026-0412”.')).toBeVisible()
    await expectAccessible(page, 'the facts table')
    await page.keyboard.press('Enter')
    const facts = page.getByRole('dialog', { name: /^invoice number of / })
    await expect(facts.getByRole('button', { name: 'Check', exact: true })).toBeFocused()
    await expect(facts).toContainText('Not checked.')
    await expectAccessible(page, "a cell's facts")
    await facts.getByRole('button', { name: 'Open in the document' }).click()
    await expect(facts).toBeHidden()
    await expect(page).toHaveURL(/\/documents\/\d+\?(.*&)?block=\d+/)
    await expect(page.getByRole('region', { name: 'Text of invoice.md' }).getByText('Invoice number: INV-2026-0412')).toBeInViewport()
    await expect(number).toBeFocused()

    // Kept by a condition the invoice does not meet, then by none.
    const condition = page.getByRole('button', { name: 'Condition', exact: true })
    await condition.click()
    const form = page.getByRole('form', { name: 'Keep the rows whose…' })
    await expect(form.getByLabel('Field')).toBeFocused()
    await form.getByLabel('Field').selectOption('total_amount')
    await form.getByLabel('Condition').selectOption('min')
    await form.getByLabel('Number').fill('2000')
    await expectAccessible(page, 'a condition of the facts table being chosen')
    await form.getByRole('button', { name: 'Add' }).click()
    await expect(page.getByText('No document meets these conditions')).toBeVisible()
    await expect(condition).toBeFocused()
    await page.getByRole('button', { name: 'Remove the condition total amount is at least 2000' }).click()
    await expect(row).toBeVisible()
    await expect(condition).toBeFocused()

    // The invoice number checked as read, the button that did it then taking the check back.
    await number.click()
    await facts.getByRole('button', { name: 'Check', exact: true }).click()
    await expect(facts).toContainText(/Checked by .+ on /)
    await expect(facts.getByRole('button', { name: 'Take back the check' })).toBeFocused()
    await page.keyboard.press('Escape')
    await expect(number).toBeFocused()

    // The total corrected: its own value in the cell, which the model's reading follows.
    await row.getByRole('button', { name: 'EUR 1,250.00', exact: true }).click()
    const totals = page.getByRole('dialog', { name: /^total amount of / })
    const correct = totals.getByRole('button', { name: 'Correct…' })
    await correct.click()
    const correction = totals.getByRole('form', { name: 'Correct EUR 1,250.00' })
    await expect(correction.getByLabel('Amount')).toBeFocused()
    await expect(correction.getByLabel('Currency')).toHaveValue('EUR')
    await correction.getByLabel('Amount').fill('1205')
    await expectAccessible(page, "a fact's correction")
    await correction.getByRole('button', { name: 'Save' }).click()
    await expect(totals).toContainText(/Corrected by .+ on .+; read as “EUR 1,250.00”\./)
    await expect(correct).toBeFocused()
    await page.keyboard.press('Escape')
    await expect(row).toContainText('1,205.00 EUR')

    // The rows to review keep it while a fact of it is unchecked.
    const toReview = page.getByRole('button', { name: 'To review' })
    await toReview.click()
    await expect(toReview).toHaveAttribute('aria-pressed', 'true')
    await expect(page.getByText('1 of 1 document, 1 to review', { exact: true })).toBeVisible()
    await expect(row).toBeVisible()
    await toReview.click()

    // Every row it keeps downloads as a CSV file of the table as it shows, the correction in its cell.
    const download = page.getByRole('button', { name: 'Download', exact: true })
    await download.click()
    const downloads = page.getByRole('menu')
    await expect(downloads.getByRole('menuitem').first()).toBeFocused()
    await expectAccessible(page, "the facts table's downloads")
    const downloading = page.waitForEvent('download')
    await downloads.getByRole('menuitem', { name: 'CSV file, as the table shows' }).click()
    await expect(download).toBeFocused()
    const csv = await readFile(await (await downloading).path(), 'utf8')
    // With the byte order mark Excel needs to read its names, which trim() takes off.
    expect(csv.startsWith('\ufeffDocument,invoice_number,issue_date,')).toBe(true)
    const [, line, ...more] = csv.trim().split(/\r?\n/)
    expect(more).toEqual([])
    expect(line).toContain('INV-2026-0412')
    expect(line).toContain('"1,205.00 EUR"')

    // All three are the organisation's to ask about, by the field and the file, never the value.
    await page.goto('/organization/audit')
    await page.getByLabel('What').selectOption('fact_corrected')
    await expect(page.getByRole('listitem').filter({ hasText: 'Corrected a fact' })).toContainText('invoice.md · total amount')
    await page.getByLabel('What').selectOption('facts_exported')
    await expect(page.getByRole('listitem').filter({ hasText: 'Downloaded a facts table' })).toContainText(
      'invoice facts as a CSV file, sorted by total amount',
    )
  })

  await test.step('add a field to the invoice template, read the invoice again for it, and read what changed', async () => {
    await page.goto('/')
    await page.getByRole('link', { name: /^Supplier contracts/ }).click()
    await page.getByRole('button', { name: 'Extraction templates' }).click()
    const invoice = page.getByRole('list', { name: 'Templates' }).locator(':scope > li').filter({ hasText: /^Invoice/ })
    // The delivery period the invoice states, which the built-in template does not ask for.
    await invoice.getByRole('button', { name: 'Customise Invoice' }).click()
    const form = page.getByRole('form', { name: 'Your own Invoice template' })
    await form.getByRole('button', { name: 'Add a field' }).click()
    await form.getByRole('group', { name: 'Field 17' }).getByLabel('Name').fill('Delivery period')
    await form.getByRole('button', { name: 'Save' }).click()
    await expect(page.getByText('The invoice document here keeps the facts read before until read again.')).toBeVisible()

    // Read again after a question that names it; the button that asked goes, the one that shows what changed takes the focus.
    await page.getByRole('button', { name: 'Read it again' }).click()
    const question = page.getByRole('dialog', { name: 'Read the invoice document again?' })
    await expectAccessible(page, 'the question before documents are read again')
    await question.getByRole('button', { name: 'Read it again' }).click()
    await expect(page.getByText('1 document is being read again.')).toBeVisible()
    const whatChanged = page.getByRole('button', { name: 'What changed', exact: true })
    await expect(whatChanged).toBeFocused()
    await whatChanged.click()
    await expect(page.getByRole('heading', { name: 'What changed in the Invoice facts' })).toBeFocused()

    // Followed while it is read, then the field it found; the total its member corrected was read as before, so it stands, unnamed.
    const changed = page.getByRole('list', { name: 'Documents read differently' }).getByRole('listitem')
    await expect(changed).toContainText('delivery period', { timeout: 60_000 })
    await expect(changed).toContainText('Found “March 2026”.')
    await expect(changed).not.toContainText('total amount')
    await expect(page.getByRole('status').filter({ hasText: 'read again' })).toHaveText('1 document read again: 1 changed.')
    await expectAccessible(page, 'what a reading changed')

    // The template says so, and its facts table has the field's column, beside the correction.
    await page.getByRole('button', { name: 'Back to the templates' }).click()
    await expect(invoice).toContainText('1 changed when read again')
    await invoice.getByRole('button', { name: 'Facts table of Invoice' }).click()
    const row = page.getByRole('grid', { name: 'Facts of invoice documents' }).getByRole('row').filter({ hasText: 'INV-2026-0412' })
    await expect(row).toContainText('March 2026')
    await expect(row).toContainText('1,205.00 EUR')
  })

  await test.step("check the invoice against its purchase order by a suggested cross-check and one of the member's own, each side opening where it is written", async () => {
    // A purchase order of the PO number the invoice states, read for the built-in template.
    await page.goto('/')
    await page.getByRole('link', { name: /^Supplier contracts/ }).click()
    await page.locator('input[name="files"]').setInputFiles(PURCHASE_ORDER)
    await expect(page.getByRole('treeitem', { name: 'purchase-order.md' })).toHaveAccessibleDescription(/^Ready/, { timeout: 60_000 })
    await page.getByRole('button', { name: 'Cross-checks' }).click()
    const heading = page.getByRole('heading', { name: 'Cross-checks', exact: true })
    await expect(heading).toBeFocused()
    await expect(page.getByText('No cross-checks yet: add a suggested one, or make your own.')).toBeVisible()
    const suggested = page.getByRole('list', { name: 'Suggested cross-checks' })
    await expect(suggested).toContainText(
      "Each invoice's total amount is at most the total amount of the purchase order documents with the same po number.",
    )
    await expectAccessible(page, 'the cross-checks')

    // Added as suggested, it finds the total as the member corrected it, 5 more than the order's.
    await suggested.getByRole('button', { name: 'Add Total within its purchase order' }).click()
    await expect(page.getByText('Added “Total within its purchase order”.')).toBeVisible()
    await expect(heading).toBeFocused()
    const rules = page.getByRole('list', { name: 'Cross-checks', exact: true })
    const total = rules.getByRole('listitem').filter({ hasText: 'Total within its purchase order' })
    await expect(total).toContainText('1 differs · 0 miss a value · 0 hold')
    await total.getByRole('button', { name: 'What Total within its purchase order finds' }).click()
    await expect(page.getByRole('heading', { name: 'What “Total within its purchase order” finds' })).toBeFocused()
    const finding = page.getByRole('list', { name: 'Findings' }).getByRole('listitem')
    await expect(finding).toContainText('5 EUR more than purchase-order.md.')
    await expect(finding).toContainText('1,205.00 EUR · corrected')
    await expectAccessible(page, 'what a cross-check finds')
    await finding.getByRole('button', { name: 'EUR 1,200.00', exact: true }).click()
    await expect(page).toHaveURL(/\/documents\/\d+\?(.*&)?block=\d+/)
    await expect(page.getByRole('region', { name: 'Text of purchase-order.md' }).getByText('Total amount: EUR 1,200.00')).toBeInViewport()

    // One of the member's own: the invoice issued no earlier than its order, paired by the PO number each states.
    await page.getByRole('button', { name: 'Back to the cross-checks' }).click()
    await page.getByRole('button', { name: 'New cross-check' }).click()
    const form = page.getByRole('form', { name: 'New cross-check' })
    await expect(form.getByLabel('Name', { exact: true })).toBeFocused()
    await form.getByLabel('Name', { exact: true }).fill('Issued after its order')
    const checks = form.getByRole('group', { name: 'What it checks' })
    await checks.getByLabel('Kind of document').selectOption('invoice')
    await checks.getByLabel('Field').selectOption('issue_date')
    const against = form.getByRole('group', { name: 'Compared with' })
    await against.getByLabel('Kind of document').selectOption('purchase order')
    await against.getByLabel('Field').selectOption('issue_date')
    await form.getByLabel('Compared how').selectOption('at_least')
    await form.getByLabel('Those that state the same key').check()
    await form.getByLabel('Key of the invoice').selectOption('po_number')
    await expect(form.getByLabel('Key of the purchase order')).toHaveValue('po_number')
    await expect(form).toContainText(
      "Each invoice's issue date is no earlier than the issue date of the purchase order documents with the same po number.",
    )
    await expectAccessible(page, 'a cross-check being written')
    await form.getByRole('button', { name: 'Save' }).click()
    await expect(page.getByText('Saved “Issued after its order”.')).toBeVisible()
    await expect(heading).toBeFocused()
    await expect(rules.getByRole('listitem').filter({ hasText: 'Issued after its order' })).toContainText('0 differ · 0 miss a value · 1 holds')

    // Both are the organisation's to ask about, by the kinds and fields they compare.
    await page.goto('/organization/audit')
    await page.getByLabel('What').selectOption('cross_check_created')
    await expect(page.getByRole('listitem').filter({ hasText: 'Total within its purchase order' })).toContainText(
      'invoice total amount against purchase order total amount',
    )
  })
})
