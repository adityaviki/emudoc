import { join } from 'node:path'
import { expect, test } from '@playwright/test'
import { linkMailedTo } from './mail.ts'

// The member's way through the app, end to end: sign up through the link the app mails, make a
// project, upload a contract, wait for it to be indexed, find it by searching, ask about it,
// open the passage the answer cites, and start a second organisation and switch back. The
// answer is the stand-in's (e2e/standin.py): the first sentence of the passage its search
// found best, with that passage's reference.
const CONTRACT = join(import.meta.dirname, 'fixtures', 'service-contract.md')

test('a new member uploads a contract, finds it, asks about it and opens the cited passage', async ({ page }) => {
  test.setTimeout(120_000)

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
})
