// Each page's scripts against its budget, after `pnpm build`: the files under dist/assets that
// the page's HTML loads, gzipped as the reverse proxy sends them (deploying.md, "TLS at a reverse
// proxy"), since that is what a browser downloads at the first visit and after each release.
// CI's Frontend job runs it, and it fails the job past a budget, or for a script no page loads,
// which a code split would leave (frontend conventions).
import { readFileSync, readdirSync } from 'node:fs'
import { gzipSync } from 'node:zlib'

// 231 kB: 200 kB, with room for some growth above the 184 kB of 2026-10-20, was passed the same
// day by the notification centre (202.4 kB), whose bell is on every page, 205 kB on 2026-10-21
// by the first steps and the tour of a workspace (205.8 kB), which every new member is shown,
// made by hand since no tour library fits, 210 kB the same day by the organisation's settings
// (210.0 kB), its language, how long it keeps a chat and how its members are told, and 215 kB
// the same day by its API keys (216.3 kB), and 218 kB by a member's leave for support and the
// banner of support's view of an account (218.4 kB), which every page shows, and 220 kB by the
// help menu, whose question mark writes to support from every page, and the help pages (220.4
// kB), whose text the API sends, and 224 kB on 2026-10-22 by the accessibility pass (224.9 kB):
// menus, panels, tabs and the library's listing worked by the keyboard alone, and the focus
// given back where what held it goes, in the parts of every page, each of which a code split
// could not hold back without a reload after a release (frontend conventions), then 228 kB
// (225.6 kB), and 231 kB the same day by an upload's progress and folders uploaded (228.5 kB):
// the panel that follows each file, the requests a folder is sent in and its reading, in the
// library of every project, then 234 kB on 2026-10-23 by the same panel following the upload's
// indexing (231.8 kB): how many of its documents are ready and those that could not be, and why,
// then 238 kB the same day by a project's extraction templates (235.5 kB): the view that lists
// them and the form that writes one, field by field, the first of tier 3's features, which the
// rest will add to (a facts table next), and 242 kB the same day by that facts table (239.5 kB):
// a grid the keyboard moves through, its sort, its conditions and each cell's quote, then each
// cell's checks and corrections (241.5 kB), and 246 kB the same day by what a template's change
// did once its documents were read again (243.0 kB): the view of each reading's changes, and
// 252 kB the same day by the cross-checks (249.3 kB): the rules that compare one kind's facts with
// another's, the form that writes one with its sentence, and the view of what each finds, and
// 256 kB the same day by the runs of them (252.3 kB): the runs' list, the view of a run with what
// each rule found as it was found, and the model's discrepancy report with its citations, and
// 262 kB on 2026-10-24 by documents' reviews (257.6 kB): the reviews view, the controls that move a
// review and assign it in the document's tab and the view, and each row's mark and menu, and
// 266 kB the same day by drafts (262.2 kB): the drafts' list with the form that asks for one, a
// draft's view with its versions, its editor and preview, and the dispute letter of a review, and
// 271 kB the same day by a project's address (268.8 kB): the Email in view, with its senders'
// list, its folder and what became of the messages it received, and 274 kB the same day by the
// connected folders (271.8 kB): their view, with the folder chooser that browses a member's
// Google Drive, each folder's standing and the files it left out, and 277 kB on 2026-10-25 by
// the webhooks (274.5 kB): the organisation page's section that adds, changes, pauses, rolls and
// removes them, with each one's delivery log; 281 kB on 2026-10-25 by a document's share links
// (277.8 kB): the dialog that makes, shows and revokes them, the organisation page's section, and
// the page a link opens for its visitor; 283 kB on 2026-10-25 by a document's versions (281.8
// kB): the dialog on its tab that uploads a new version and lists the earlier ones, each to
// download or to read its text, and the library's "Upload a new version…"; 287 kB on 2026-10-25
// by a chat about part of the project (284.7 kB): the chooser above the box a member asks in, the
// last question edited in place, "Regenerate", and the library's chats about a folder or documents;
// 298 kB on 2026-10-26 by the library's places (297.2 kB): tags with their dialog and page, smart
// folders and their form, favourites and recent documents, the orders, the grid of thumbnails, and
// the filing rules' page and form; 302 kB on 2026-10-26 by a document's details, note and copies
// (300.3 kB): the dialog that sets its title, type and date beside what the reading found, the note
// on its tab, the links to its copies in other projects, and the duplicates page; 306 kB on
// 2026-10-26 by the search's filters (304.1 kB): the dialog that narrows a search, the chips that
// take each part off, "Search within these", and a smart folder saved with the search's words;
// 314 kB on 2026-10-31 by a project's question sets (310.4 kB): the library's place of the sets
// with their form, run now, turned off and on, and their reports, and a report's answers with the
// passages they cite. The figures up to here are CI's: this laptop's Node (24.22.1) gzips the
// same file 2.5 kB larger (312.9 kB). CI no longer runs this check on the developer's changes
// (2026-10-31), so the figures from here on are this laptop's.
// A change that passes it makes the scripts smaller (a dependency left out, a page loaded only
// when it is opened) or raises this number, saying why in its commit.
const BUDGET = 314_000
// The operator's area (operator.html), a build of its own that only operators are sent: React
// and the rest again, 102.0 kB on 2026-10-21. Its size costs no member anything, so its budget
// only catches a dependency pulled in by mistake.
const OPERATOR_BUDGET = 125_000
// The API reference (reference.html), a build of its own that anyone reads at /api-reference: React
// and the page drawn from the contract, 80.0 kB on 2026-10-25. Read by whoever writes a program,
// once, so its budget too only catches a dependency pulled in by mistake.
const REFERENCE_BUDGET = 95_000

const PAGES = [
  { file: 'index.html', name: "The members' page", budget: BUDGET },
  { file: 'operator.html', name: "The operator's page", budget: OPERATOR_BUDGET },
  { file: 'reference.html', name: 'The API reference', budget: REFERENCE_BUDGET },
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
    console.error(`dist/${page.file} is missing: run pnpm build, which builds every page.`)
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
