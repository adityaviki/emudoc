import { execFileSync } from 'node:child_process'
import { join } from 'node:path'

// The run's database, as e2e/serve.sh makes it.
const DATABASE_URL = 'postgresql+psycopg://emudoc:emudoc@127.0.0.1:5437/emudoc_e2e'
const BACKEND = join(import.meta.dirname, '..', '..', 'backend')

/** Run a maintenance command against the run's database, as the operator does in a shell. */
function maintenance(...args: string[]) {
  execFileSync('uv', ['--directory', BACKEND, 'run', 'python', '-m', 'app.maintenance', ...args], {
    env: { ...process.env, DATABASE_URL },
    stdio: 'pipe',
  })
}

/**
 * Put an organisation on a plan, as its operator does with the maintenance command, with the
 * command's options after it (`--seats 2`): the Free plan a sign-up starts on has room for one
 * member, so a test that invites another needs a larger one.
 */
export function movePlan(slug: string, plan: string, ...options: string[]) {
  maintenance('plan', slug, plan, ...options)
}

/** Turn a feature flag on or off for an organisation, or back to its rollout, as the `flags` command does. */
export function chooseFlag(slug: string, flag: string, choice: 'on' | 'off' | 'rollout') {
  maintenance('flags', slug, flag, choice)
}

/** Let the account with the address open the operator's area, as the `operators` command does. */
export function makeOperator(address: string) {
  maintenance('operators', 'add', address)
}
