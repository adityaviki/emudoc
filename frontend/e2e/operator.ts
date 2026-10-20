import { execFileSync } from 'node:child_process'
import { join } from 'node:path'

// The run's database, as e2e/serve.sh makes it.
const DATABASE_URL = 'postgresql+psycopg://emudoc:emudoc@127.0.0.1:5437/emudoc_e2e'
const BACKEND = join(import.meta.dirname, '..', '..', 'backend')

/**
 * Put an organisation on a plan, as its operator does with the maintenance command: the Free plan
 * a sign-up starts on has room for one member, so a test that invites another needs a larger one.
 */
export function movePlan(slug: string, plan: string) {
  execFileSync('uv', ['--directory', BACKEND, 'run', 'python', '-m', 'app.maintenance', 'plan', slug, plan], {
    env: { ...process.env, DATABASE_URL },
    stdio: 'pipe',
  })
}
