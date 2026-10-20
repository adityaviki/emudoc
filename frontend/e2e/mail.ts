import { readFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

// Where e2e/serve.sh keeps everything the server says (playwright.config.ts names it there). The
// run has no mail relay, so the app's log mailer writes each message to the log ("Email to
// <address>: <subject>" and the body), and the test follows a sign-up's link from there.
export const SERVER_LOG = join(tmpdir(), 'emudoc-e2e-server.log')

/** The first link of the newest message to `address` in the server's log, once it is there. */
export async function linkMailedTo(address: string, timeout = 10_000): Promise<string> {
  const deadline = Date.now() + timeout
  for (;;) {
    const log = await readFile(SERVER_LOG, 'utf8').catch(() => '')
    const mail = log.lastIndexOf(`Email to ${address}: `)
    const link = mail === -1 ? null : /https?:\/\/\S+/.exec(log.slice(mail))
    if (link) return link[0]
    if (Date.now() > deadline) throw new Error(`No message to ${address} in ${SERVER_LOG}.`)
    await new Promise((resolve) => setTimeout(resolve, 100))
  }
}
