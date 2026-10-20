// The page's scripts against their budget, after `pnpm build`: every file Vite wrote under
// dist/assets, gzipped as the reverse proxy sends them (deploying.md, "TLS at a reverse
// proxy"), since that is what a member's browser downloads at the first visit and after each
// release. CI's Frontend job runs it, and it fails the job past the budget.
import { readdirSync, readFileSync } from 'node:fs'
import { gzipSync } from 'node:zlib'

// 205 kB: 200 kB, with room for some growth above the 184 kB of 2026-10-20, was passed the same
// day by the notification centre (202.4 kB), whose bell is on every page. A change that passes
// it makes the scripts smaller (a dependency left out, a page loaded only when it is opened) or
// raises this number, saying why in its commit.
const BUDGET = 205_000

const assets = new URL('../dist/assets/', import.meta.url)
const kB = (bytes: number) => `${(bytes / 1000).toFixed(1)} kB`

let scripts: string[] = []
try {
  scripts = readdirSync(assets).filter((name) => name.endsWith('.js'))
} catch {
  // No build: said below.
}
if (scripts.length === 0) {
  console.error('There are no scripts in dist/assets to measure: run pnpm build first.')
  process.exit(1)
}

let total = 0
for (const name of scripts) {
  const bytes = readFileSync(new URL(name, assets))
  const gzipped = gzipSync(bytes).length
  total += gzipped
  console.log(`${name}: ${kB(bytes.length)}, ${kB(gzipped)} gzipped`)
}
if (total > BUDGET) {
  console.error(
    `The page's scripts are ${kB(total)} gzipped, past their budget of ${kB(BUDGET)}. ` +
      'Make them smaller, or raise BUDGET in scripts/size.ts and say why in the commit.',
  )
  process.exit(1)
}
console.log(`The page's scripts are ${kB(total)} gzipped, within their budget of ${kB(BUDGET)}.`)
