// Each page's scripts against its budget, after `pnpm build`: the files under dist/assets that
// the page's HTML loads, gzipped as the reverse proxy sends them (deploying.md, "TLS at a reverse
// proxy"), since that is what a browser downloads at the first visit and after each release.
// CI's Frontend job runs it, and it fails the job past a budget, or for a script no page loads,
// which a code split would leave (frontend conventions).
import { readFileSync, readdirSync } from 'node:fs'
import { gzipSync } from 'node:zlib'

// 224 kB: 200 kB, with room for some growth above the 184 kB of 2026-10-20, was passed the same
// day by the notification centre (202.4 kB), whose bell is on every page, 205 kB on 2026-10-21
// by the first steps and the tour of a workspace (205.8 kB), which every new member is shown,
// made by hand since no tour library fits, 210 kB the same day by the organisation's settings
// (210.0 kB), its language, how long it keeps a chat and how its members are told, and 215 kB
// the same day by its API keys (216.3 kB), and 218 kB by a member's leave for support and the
// banner of support's view of an account (218.4 kB), which every page shows, and 220 kB by the
// help menu, whose question mark writes to support from every page, and the help pages (220.4
// kB), whose text the API sends, each of which a code split could not hold back without a
// reload after a release (frontend conventions). A
// change that passes it makes the scripts smaller (a dependency left out, a page loaded only
// when it is opened) or raises this number, saying why in its commit.
const BUDGET = 224_000
// The operator's area (operator.html), a build of its own that only operators are sent: React
// and the rest again, 102.0 kB on 2026-10-21. Its size costs no member anything, so its budget
// only catches a dependency pulled in by mistake.
const OPERATOR_BUDGET = 125_000

const PAGES = [
  { file: 'index.html', name: "The members' page", budget: BUDGET },
  { file: 'operator.html', name: "The operator's page", budget: OPERATOR_BUDGET },
]

const dist = new URL('../dist/', import.meta.url)
const kB = (bytes: number) => `${(bytes / 1000).toFixed(1)} kB`

function read(name: string): string | null {
  try {
    return readFileSync(new URL(name, dist), 'utf8')
  } catch {
    return null
  }
}

let built: string[] = []
try {
  built = readdirSync(new URL('assets/', dist)).filter((name) => name.endsWith('.js'))
} catch {
  // No build: said below.
}
if (built.length === 0) {
  console.error('There are no scripts in dist/assets to measure: run pnpm build first.')
  process.exit(1)
}

let failed = false
const loaded = new Set<string>()
for (const page of PAGES) {
  const html = read(page.file)
  if (html === null) {
    console.error(`dist/${page.file} is missing: run pnpm build, which builds both pages.`)
    failed = true
    continue
  }
  // The entry's <script src> and the <link rel="modulepreload"> of what it imports.
  const scripts = [...html.matchAll(/(?:src|href)="\/assets\/([^"]+\.js)"/g)].map((match) => match[1])
  let total = 0
  for (const name of scripts) {
    loaded.add(name)
    const bytes = readFileSync(new URL(`assets/${name}`, dist))
    const gzipped = gzipSync(bytes).length
    total += gzipped
    console.log(`${page.file}: ${name}, ${kB(bytes.length)}, ${kB(gzipped)} gzipped`)
  }
  if (total > page.budget) {
    console.error(
      `${page.name}'s scripts are ${kB(total)} gzipped, past their budget of ${kB(page.budget)}. ` +
        'Make them smaller, or raise its budget in scripts/size.ts and say why in the commit.',
    )
    failed = true
  } else {
    console.log(`${page.name}'s scripts are ${kB(total)} gzipped, within their budget of ${kB(page.budget)}.`)
  }
}
const stray = built.filter((name) => !loaded.has(name))
if (stray.length > 0) {
  console.error(
    `No page loads ${stray.join(', ')}: a script loaded later, as a code split makes, would be asked for ` +
      'by a page loaded before a release and be gone from the new image (frontend conventions).',
  )
  failed = true
}
if (failed) process.exit(1)
