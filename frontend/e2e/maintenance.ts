import { execFileSync } from 'node:child_process'
import { join } from 'node:path'

// The run's database, as e2e/serve.sh makes it.
const DATABASE_URL = 'postgresql+psycopg://emudoc:emudoc@127.0.0.1:5437/emudoc_e2e'
const BACKEND = join(import.meta.dirname, '..', '..', 'backend')

// The run's files and the domain of its projects' addresses, as e2e/serve.sh sets them.
const STORAGE_URL = `file://${join(process.env.TMPDIR || '/tmp', 'emudoc-e2e-files')}`
export const INBOUND_DOMAIN = 'in.emudoc.test'

/** Run a maintenance command against the run's database and files, as whoever runs the server does in a shell; what it printed. */
function maintenance(...args: string[]): string {
  return execFileSync('uv', ['--directory', BACKEND, 'run', 'python', '-m', 'app.maintenance', ...args], {
    env: { ...process.env, DATABASE_URL, STORAGE_URL, INBOUND_DOMAIN },
    stdio: 'pipe',
    encoding: 'utf8',
  })
}

/**
 * Hand a message saved as it was sent to the projects it is addressed to, as the `receive-mail`
 * command does for whoever runs the server, its sender taken as proven; the lines that say what became of it.
 */
export function receiveMail(path: string): string[] {
  return maintenance('receive-mail', path).trim().split('\n')
}
