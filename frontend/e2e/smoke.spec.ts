import { join } from 'node:path'
import { expect, test } from '@playwright/test'
import type { BrowserContext, Page } from '@playwright/test'
import { linkMailedTo } from './mail.ts'
import { movePlan } from './operator.ts'

// The member's way through the app, end to end: sign up through the link the app mails, make a
// project, upload a contract, wait for it to be indexed, find it by searching, ask about it,
// open the passage the answer cites, start a second organisation and switch back, and invite a
// colleague as a viewer, who makes an account from the invitation's link, finds the project and
// is offered no change to it until made a member, then make the project private, which she no
// longer finds until chosen for it, and share the chat, which the colleague then reads but does
// not ask in. The answer is the stand-in's (e2e/standin.py): the first sentence of the passage
// its search found best, with that passage's reference.
const CONTRACT = join(import.meta.dirname, 'fixtures', 'service-contract.md')

test('a new member uploads a contract, finds it, asks about it and opens the cited passage', async ({ page, browser }) => {
  test.setTimeout(120_000)
  // The colleague's browser, from the invitation on.
  let theirs: BrowserContext
  let grace: Page

  await test.step('sign up', async () => {
    // A new address every run, so that a server kept from an earlier run takes it too.
    const address = `ada.${Date.now()}@example.com`
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
  })

  await test.step('open the cited passage', async () => {
    await page.getByRole('button', { name: '1', exact: true }).click()
    await expect(page).toHaveURL(/\/documents\/\d+\?(.*&)?passage=\d+/)
    await expect(page.getByRole('heading', { name: 'Service contract', exact: true })).toBeVisible()
    await expect(page.getByText('Showing: 4 Termination')).toBeVisible()
    await expect(page.getByText(/^Either party may end this contract .* on the first page\.$/)).toBeInViewport()
  })

  await test.step('start a second organisation, and switch back while another tab shows it', async () => {
    // From the projects page: a menu closes when anything scrolls, as the passage still may.
    await page.getByRole('link', { name: 'Emudoc' }).click()
    await expect(page.getByRole('heading', { name: 'Projects' })).toBeVisible()
    await page.getByRole('button', { name: "Ada Lovelace's workspace" }).click()
    await page.getByRole('menuitem', { name: 'New organisation' }).click()
    await page.getByLabel('Name', { exact: true }).fill('Lovelace Legal')
    await page.getByRole('button', { name: 'Create organisation' }).click()
    await expect(page.getByText('No projects yet')).toBeVisible()
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
    const slug = await page.locator('code').filter({ hasText: /^ada-lovelace/ }).textContent()
    movePlan(slug ?? '', 'team', '--seats', '2')
    await page.reload()
    // The plan's figures, with the seats bought for its members.
    const plan = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Plan', exact: true }) })
    await expect(plan.getByRole('definition').first()).toHaveText('Team')
    await expect(plan.getByRole('definition').filter({ hasText: 'seats' })).toHaveText('2 seats')
    const colleague = `grace.${Date.now()}@example.com`
    await page.getByLabel('Email', { exact: true }).fill(colleague)
    await page.getByLabel('Role', { exact: true }).selectOption('viewer')
    await page.getByRole('button', { name: 'Invite', exact: true }).click()
    await expect(page.getByText(`An invitation is on its way to ${colleague}.`)).toBeVisible()
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
    await expect(grace.getByRole('img', { name: /^Ready/ })).toBeVisible()
    await expect(grace.getByRole('button', { name: 'Upload', exact: true })).toHaveCount(0)

    await page.reload()
    await expect(page.getByText('Nobody is invited at the moment.')).toBeVisible()
    const role = page.getByLabel('Role of Grace Hopper')
    await expect(role).toHaveValue('viewer')
    await role.selectOption('member')
    await expect(role).toHaveValue('member')
    await grace.reload()
    await expect(grace.getByRole('button', { name: 'Upload', exact: true })).toBeVisible()
  })

  await test.step('make the project private, which a member not chosen no longer finds, then choose her', async () => {
    await page.goto('/')
    await page.getByRole('button', { name: 'Who sees it' }).click()
    await page.getByLabel('Only the members chosen').check()
    await expect(page.getByText('Sees every project')).toBeVisible()
    await page.getByRole('button', { name: 'Save', exact: true }).click()
    await expect(page.getByText('Private', { exact: true })).toBeVisible()
    await grace.goto('/')
    await expect(grace.getByText('No projects yet')).toBeVisible()
    await expect(grace.getByText('Supplier contracts')).toHaveCount(0)

    await page.getByRole('button', { name: 'Who sees it' }).click()
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
})
