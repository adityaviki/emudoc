import { readFile } from 'node:fs/promises'
import { join } from 'node:path'
import { expect, test } from '@playwright/test'
import type { BrowserContext, Page } from '@playwright/test'
import { linkMailedTo } from './mail.ts'
import { makeOperator, movePlan } from './operator.ts'

// The member's way through the app, end to end: sign up through the link the app mails, find the
// organisation's first steps listed, skip the tour the first project shows, ask the sample
// project the organisation starts with one of its questions, make a project, upload a contract,
// wait for it to be indexed, find it by searching, ask about it, open the passage the answer
// cites, which the Free plan's chat follows with the way to a plan for a team, export the project,
// download it and open the bell's notice that it is ready, start a second
// organisation, choose its settings, read them in its audit log and download it, hide its first
// steps, delete it and keep it, and switch back, and, on Team, invite a colleague as a
// viewer from under that answer, who makes an account from the invitation's link, ends her tour, finds
// the project and is offered no change to it until made a member, then make the project
// private, which she no longer finds until chosen for it, and share the chat, which the
// colleague then reads but does not ask in, and last ask the question that uses the month's
// questions, which the bell tells, and open that notice from the other organisation; then, made
// an operator, read the organisation in the operator's area, which nobody else is sent, raise its
// agreed questions there, turn a feature flag on for it, which its audit log keeps, view the
// colleague's account, which she let support see, and switch the model's provider off and on
// again. The answer
// is the stand-in's (e2e/standin.py): the first sentence of the passage its search found best,
// with that passage's reference.
const CONTRACT = join(import.meta.dirname, 'fixtures', 'service-contract.md')
// The project every sign-up's organisation starts with (backend/app/sample).
const SAMPLE = 'Sample: Acme supplier contract'

test('a new member uploads a contract, finds it, asks about it and opens the cited passage', async ({ page, browser, request }) => {
  test.setTimeout(120_000)
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
    await page.goto('/signup')
    await page.getByLabel('Email', { exact: true }).fill(address)
    await page.getByRole('button', { name: 'Continue' }).click()
    await expect(page.getByRole('heading', { name: 'Check your email' })).toBeVisible()
    await page.goto(await linkMailedTo(address))
    await expect(page.getByRole('heading', { name: 'Finish creating your account' })).toBeVisible()
    await expect(page.getByText(address)).toBeVisible()
    await page.getByLabel('Name', { exact: true }).fill('Ada Lovelace')
    await page.getByLabel('Password', { exact: true }).fill('a sentence to remember')
    await page.getByLabel(/I accept the terms of service/).check()
    await page.getByRole('button', { name: 'Create account' }).click()
    await expect(page.getByRole('heading', { name: 'Projects' })).toBeVisible()
    await expect(firstSteps).toContainText('0 of 3 done')
  })

  await test.step('skip the tour, and ask the sample project one of its questions', async () => {
    await page.getByText(SAMPLE).click()
    await expect(page).toHaveURL(/\/projects\/\d+$/)
    // The first project opened shows the tour: the sample takes no uploads, so it has no card.
    const tour = page.getByRole('dialog')
    await expect(tour).toContainText('1 of 6')
    await expect(tour).toContainText('The library')
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
    await page.getByRole('link', { name: 'Emudoc' }).click()
    await expect(page.getByRole('heading', { name: 'Projects' })).toBeVisible()
    await expect(firstSteps).toContainText('1 of 3 done')
  })

  await test.step('make a project', async () => {
    await page.getByRole('button', { name: 'New project' }).click()
    await page.getByLabel('Name', { exact: true }).fill('Supplier contracts')
    await page.getByRole('button', { name: 'Create project' }).click()
    await expect(page).toHaveURL(/\/projects\/\d+$/)
  })

  await test.step('upload the contract and wait for it to be indexed', async () => {
    await page.locator('input[type="file"]').setInputFiles(CONTRACT)
    // Every stage done but the malware scan, which has no scanner here.
    const status = page.getByRole('img', { name: /^Ready/ })
    await expect(status).toBeVisible({ timeout: 60_000 })
    for (const stage of ['Parse', 'Outline', 'Summaries and facts', 'Embeddings']) {
      await expect(status).toHaveAccessibleName(new RegExp(`${stage}: done`))
    }
    await expect(status).toHaveAccessibleName(/Malware scan: skipped/)
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

  await test.step('ask, and get an answer that cites the contract', async () => {
    const composer = page.getByPlaceholder('Ask a question about these documents')
    await composer.fill('How much notice does ending the contract take?')
    await composer.press('Enter')
    await expect(page).toHaveURL(/[?&]chat=\d+/)
    const citation = page.getByRole('button', { name: '1', exact: true })
    await expect(citation).toBeVisible({ timeout: 30_000 })
    await expect(citation).toHaveAttribute('title', 'Service contract · 4 Termination')
    await expect(page.getByText(/Searched for "How much notice/)).toBeVisible()
    await expect(page.getByText("Either party may end this contract by giving ninety days' written notice to the other.")).toBeVisible()
    await expect(page.getByText('Thinking')).toHaveCount(0)
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
  })

  await test.step('rename the contract and move it into a folder, undoing each', async () => {
    const contract = page.getByRole('treeitem', { name: /service-contract\.md/ })
    const undo = page.getByRole('button', { name: 'Undo', exact: true })
    await contract.click()
    await page.keyboard.press('F2')
    await page.getByLabel('New name').fill('contract')
    await page.getByLabel('New name').press('Enter')
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
  })

  await test.step('delete the contract, and take it back from the trash', async () => {
    const contract = page.getByRole('treeitem', { name: /service-contract\.md/ })
    await contract.click()
    await page.keyboard.press('Delete')
    await expect(contract).toHaveCount(0)
    await expect(page.getByText('“service-contract.md” is in the trash.')).toBeVisible()
    await page.getByRole('button', { name: 'Trash', exact: true }).click()
    await expect(page).toHaveURL(/[?&]trash/)
    await page.getByRole('button', { name: 'Restore service-contract.md' }).click()
    await expect(page.getByText('The trash is empty')).toBeVisible()
    await page.getByRole('button', { name: 'Back to the library' }).click()
    await expect(contract).toBeVisible()
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
    const downloading = page.waitForEvent('download')
    await file.click()
    const zip = await readFile(await (await downloading).path())
    expect(zip.subarray(0, 4).toString('latin1')).toBe('PK\u0003\u0004')
    await panel.getByRole('button', { name: 'Close' }).click()
    await expect(panel).toHaveCount(0)
    // The bell, which asks again as the page loads, tells it is ready, and its notice opens the panel.
    await page.reload()
    await page.getByRole('button', { name: 'Notifications, 1 unread' }).click()
    await page.getByRole('dialog', { name: 'Notifications' }).getByRole('button', { name: /Your project export is ready/ }).click()
    await expect(panel.getByRole('link', { name: 'Download' })).toBeVisible()
    await panel.getByRole('button', { name: 'Close' }).click()
    await expect(page.getByRole('button', { name: 'Notifications', exact: true })).toBeVisible()
  })

  await test.step('start a second organisation, choose its settings, delete it and keep it, and switch back while another tab shows it', async () => {
    // From the projects page: a menu closes when anything scrolls, as the passage still may.
    await page.getByRole('link', { name: 'Emudoc', exact: true }).click()
    await expect(page.getByRole('heading', { name: 'Projects' })).toBeVisible()
    await page.getByRole('button', { name: "Ada Lovelace's workspace" }).click()
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
    // A feature being rolled out to none, behind the run's own flag (e2e/served.py), turned on for it alone.
    const flags = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Feature flags', exact: true }) })
    const preview = flags.getByRole('row').filter({ hasText: 'e2e_preview' })
    await expect(preview).toContainText('off, as rolled out')
    const chosen = page.waitForResponse((response) => response.url().endsWith('/flags/e2e_preview') && response.ok())
    await preview.getByLabel('Feature flag e2e_preview').selectOption('on')
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
    const rollout = page.getByRole('row').filter({ hasText: 'e2e_preview' })
    await expect(rollout.getByRole('link', { name: "Ada Lovelace's workspace" })).toBeVisible()
    await page.goto('/organization/audit')
    // The whole log first, which holds every kind of row this organisation has, its export's among them.
    await expect(page.getByRole('listitem').filter({ hasText: 'Exported a project' })).toContainText('Supplier contracts')
    await page.getByLabel('What').selectOption('flag_changed')
    await expect(page.getByRole('listitem').filter({ hasText: 'Feature flag changed' })).toContainText("e2e preview: on · by Emudoc's operators")
    await page.goto('/operator/jobs')
    await expect(page.getByRole('heading', { name: 'Jobs', exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Ask again' })).toBeVisible()
    await page.getByRole('navigation', { name: 'Views' }).getByRole('link', { name: 'Services' }).click()
    await expect(page.getByRole('heading', { name: 'What the watch sees' })).toBeVisible()
    // The stand-in's provider switched off, which asks first, and on again: last, since nothing asks a model after it.
    const ollama = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Providers' }) }).getByRole('row').filter({ hasText: 'ollama' })
    page.once('dialog', (dialog) => dialog.accept())
    await ollama.getByRole('button', { name: 'Switch ollama off' }).click()
    await expect(ollama).toContainText('switched off since')
    await expect(page.getByText('is switched off: an operator switched its provider, ollama, off').first()).toBeVisible()
    await ollama.getByRole('button', { name: 'Switch ollama on' }).click()
    await expect(ollama.getByRole('cell').nth(2)).toHaveText('on')
    await expect(ollama.getByRole('button', { name: 'Switch ollama off' })).toBeVisible()
  })
})
