# Changelog

What changed for the people who use Emudoc (**Members**) and for those who run it
(**Operators**), newest first, by the day the change reached `main`. A release is a commit: its
full SHA is the image's tag, the version `/api/health` reports and the user menu shows, so
`git log <released>..<to release>` lists the commits a release adds, and this file says what
they change. A commit that changes what a member or an operator sees adds its line here, under
its day, in the same commit.

The **Members** entries are written for members, as Markdown. The file keeps one shape, which
the suite holds it to (`backend/tests/test_changelog.py`): `## YYYY-MM-DD` a day (or `## YYYY-MM-DD to YYYY-MM-DD`), the newest first, then `Members:`
and `Operators:`, each a list of `- ` entries whose lines go on indented by two spaces, a day's
newest entry first.

## 2026-11-13

Members:

- Every part of Emudoc is now on for every workspace: extraction templates and the facts table,
  cross-checks, their runs and automations, reviews, drafts, My files, question sets, comments
  and the project's activity, and share links. Every new document's facts are read for its kind's
  template. Email in and connected folders show once whoever runs Emudoc has set them up.

- Nothing in Emudoc is held to a plan any more, and nothing is sold: the workspace's page has no
  Plan section, no banner says the workspace is past a limit, and no project, upload, invitation,
  question, link, key or connected folder is refused for one. The usage page shows what the
  workspace holds, and its pages indexed and questions asked by day and by month; owners and
  admins see there what the models cost too.
- Chats are kept until they are deleted, unless the workspace's owners and admins choose a number
  of days on its page. They choose there how long the audit log keeps its entries as well: a year
  until they choose fewer or more days, or for good.

Operators:

- The feature flags are gone: the `flags` command, the operator's area's Flags view and each
  organisation's "Feature flags", and `organizations.flags` (dropped by the migration). Every
  feature is on. Email in shows while `INBOUND_DOMAIN` is set and connected folders while a
  service's client is, which `/api/status` reports as `email_in` and `connectors`. The enrich
  stage lists the project's extraction templates in every document's first request, a few
  thousand tokens more a document. The inbound and connector metrics lose the outcome `off`.
- Plans and billing are gone: the `plan` command, Stripe's routes and webhook, `STRIPE_*`,
  `BILLING_GRACE_DAYS` and `RATE_BILLING` (left in `.env`, they are ignored), the plans' columns,
  the packs and Stripe's events (dropped by the migration), the 402 refusals, the plan's mails and
  the notices at 80 % and 100 % of a month. A day's model spending is held by `DAILY_SPEND_CAP`
  alone, which `costs` and the operator's area read the day against; the operator's area no longer
  shows or changes a plan. The metrics lose `emudoc_organizations`, the `plan` label of
  `emudoc_stored_bytes`, the upload rejection `plan`, the inbound outcome `refused_plan`, and the
  connector's and webhook's plan refusals.
- The audit log's entries go after the workspace's `audit_days` (a year by default, none keeping
  them for good), set on its page, in place of a plan's days.

## 2026-11-12

Members:

- The app calls your team's place in Emudoc the workspace: the user menu's "Workspace" opens its
  page, and its settings, members, invitations, keys, audit log and messages say workspace where
  they said organisation. The invitation's mail asks you to join a workspace.
- Emudoc holds one workspace, your team's. The header names it and opens its page, where its
  menu of organisations was: nobody starts another, switches, leaves it, joins it by their email
  address's domain or deletes it. A member an admin removes keeps their account, and signing in
  then shows a page that says to ask an admin to invite them again; the invitation's link brings
  them back. The bell lists the workspace's notices, and a notice's link opens its page directly.

- When you invite someone, or send an invitation again, you are shown its link to copy and send
  them yourself, beside the email: where this Emudoc sends no mail, the page says so, and the link
  is how the invitation arrives. An owner or admin can also make a member who forgot their
  password a link that sets it, from "Reset password…" in the members list.
- Accounts are made by invitation: the sign-up page says to ask your team's admin. Where
  sign-up is open, signing up joins your team's workspace rather than making one of your own.

- Where My files are on, the agent writes Word documents for you there: ask in a chat for a
  letter, a memo or a report and it writes the file, its sources as numbered notes, and the
  answer's step opens it. Ask it to change one of your files, or a Word document of the project,
  which it copies into My files first, and it edits it in place, keeping the formatting of
  everything it does not change. Each change is a new version you can go back to.
- Where an operator turns them on, each member keeps their own Word documents and Excel workbooks
  in a project, under "My files" in the library, which only they see. A file shows its pages as
  it looks, keeps every version you upload after editing it, downloads any of them, and copies
  into the project's library, as a new document or as the next version of the document it came
  from, when you want the project's members to have it.
- Closing tabs one after another with the mouse is easier: the tabs left keep their width while
  the mouse stays on the row of tabs, so the next tab's cross comes under it, and they widen
  again once the mouse moves away.
- The tabs of open documents narrow as more open, instead of running off the side at once: each
  shows less of its name, then only the file's icon and its cross, and only past that does the
  row of tabs scroll. Pointing at a tab still shows its whole name.
- A project is searched from the library's box again, and the header no longer has one: the
  files whose names hold your words come first, as rows of the library, and the passages the
  words find are listed below them. A passage opens its document beside the library, the search
  staying where it was, and "Filter" narrows the passages as before. Every project is searched
  from the box on the projects page.

Operators:

- An install holds one workspace: `POST /api/organizations` (start one), `POST
  /api/organizations/{id}/switch`, `POST /api/organization/leave`, `POST|DELETE
  /api/organization/deletion`, `PUT /api/organization/joining` and `POST
  /api/organizations/{id}/join` are gone, with `MAX_FREE_ORGANIZATIONS`, the mails of an
  organisation's deletion and the housekeeping's purge of one. A member removed keeps their
  account, in no workspace: `GET /api/auth/me` answers with `organization` and `role` null, every
  other route 403, and `POST /api/auth/invitation/accept` brings them back. `MeOut` no longer
  carries `organizations` or `joinable`, `OrganizationOut` `join_domains`, `join_role` or
  `deletion_due_at`, and a notice `org_id` or `organization`; the notification centre lists the
  workspace's notices. The app's requests no longer send `X-Organization`, and mail links no
  longer carry `?organization=`. Migration 5c1e9a7d3b20 drops `organizations.join_domains`,
  `join_role` and `deletion_due_at`; the audit log keeps its entries of those actions.
- A new install opens on a setup page while it has no account: it makes the admin, with the
  address confirmed, an owner of the workspace named as typed, which starts with the sample
  project, and is refused once any account exists. `maintenance create-admin <address> --name
  … --workspace …` does the same from a shell, without a password, and prints the link that sets
  one. `SIGNUP_OPEN` now defaults to `false`; set to `true`, a sign-up joins the workspace as a
  member. `maintenance invite`, the operator's "Send a sign-up link" and its route `POST
  /api/operator/sign-up-links` are gone. Making or sending an invitation answers with its link
  (`link`, and `mailed`, whether a relay is set), and `POST
  /api/organization/members/{id}/reset-link` and `maintenance reset-password <address>` make a
  link that sets a member's password, working once within a day, kept in the audit log as
  `reset_link_made` (migration b7d4e1f20c93).
- My files, behind the flag `my_files`: each member's own Word and Excel files in a project, in
  the tables `my_files` and `my_file_versions` (migration 562af5b541e8), stored under
  `orgs/{o}/projects/{p}/my-files/{f}/`, whose leftovers the storage walk sweeps once their row is
  gone. A version a member uploads is scanned and converted by the worker's new `view_my_file`
  job; its bytes count toward the plan's storage. The audit log gains `my_file_added`,
  `my_file_version_added`, `my_file_renamed`, `my_file_deleted`, `my_file_downloaded` and
  `my_file_copied`, by the file's kind and id, never its name.
- Answers write Word documents into the asker's My files where `my_files` is on: five tools
  (`list_my_files`, `read_file`, `create_document`, `edit_document`, `copy_to_my_files`) move
  the answers' prompt version. A file the agent wrote names its answer (migration 920709903bf8),
  so an answer written again writes it again as a version. Its rows in the audit log carry
  `by_model` and the chat. The `injection` command also fails an answer that writes a file
  nobody asked for.
- The images are past the high CVEs Audit has found since 8 November: the app's image is built
  on the newer python:3.13 base (apt's CVE-2026-109518) and upgrades the base's Debian packages
  as it builds (zlib's CVE-2026-85091 and CVE-2026-76844, fixed after any base was), and the
  deployment runs Caddy 2.11.9, Prometheus 3.16.0, Alertmanager 0.35.0 and the newest builds of
  ClamAV 1.5 and llama.cpp's server, whose vectors the embeddings check found unchanged.
  golang.org/x/net's CVE-2026-78661 in Caddy, Prometheus and Alertmanager, its HTML parser slow
  on a crafted page, is accepted in `deploy/.trivyignore.yaml` until 26 November: none of them
  parses HTML a peer wrote there.

## 2026-11-11

Members:

- The page of what's new is gone, with its place in the help menu and the dot on the question
  mark; its old address leads to the home page. The help menu keeps the service status and the
  form to write to us.
- A new chat no longer offers questions to start from, in the sample project or your own: ask in
  your own words. "Summarise this folder" and "Compare the two documents" stay in the library's
  menus.
- The home page has a look of its own: a dark hero with the app answering a question, headings
  in the logo's typeface, and citron marking what an answer cites.
- The home page is new: an answer shown beside the passages it cites, the questions Emudoc
  answers across documents, the files it reads, and how your documents are kept. Its button asks
  for a demo, or for access, rather than starting a free plan.
- The plans, the help pages and the API reference are taken down for now, and their old
  addresses lead to the home page. The help menu keeps what's new, the service status and the
  form to write to us; the organisation page still offers the upload client, with how to run
  it, and says how a program checks a webhook's signature.
- Answers no longer suggest questions to ask next under them.

Operators:

- `/whats-new` answers 302 to `/` and leaves the sitemap, and `GET /api/changelog` and `POST
  /api/auth/changelog/read` are gone, with `MeOut.changes_unread`. The migration drops
  `users.changes_seen_on` and `users.changes_seen_entries`, which the account's export no longer
  carries. The image no longer holds `CHANGELOG.md`, and its smoke test no longer asks for it.
- The indexing model no longer writes the questions an empty chat offered: no `suggest_questions`
  job is queued once a project's documents settle, and the migration deletes those jobs and drops
  `projects.suggestions` and `projects.suggested_at`. The usage kind `suggestions` stays for the
  rows already recorded, so the costs of the past days are unchanged.
- The home page offers no sign-up, whether `SIGNUP_OPEN` is on or off: its button is "Book a
  demo" (`BOOKING_URL`), else "Request access", a mail to the support address. `/pricing`,
  `/help`, `/help/…` and `/api-reference` answer 302 to `/` and leave the sitemap, `GET
  /api/help` lists no articles, and `pnpm build` makes three builds, without `reference.html`.
  The versioned API and `/api/v1/openapi.json` stay (deploying.md, "The public site").
- The agent is no longer offered `suggest_follow_ups`, so the answers' prompt version moves. The
  migration drops `chat_messages.follow_ups`.

## 2026-11-08

Members:

- Word files, presentations, spreadsheets, RTF and HTML files open as they look, page by page,
  with their first page as their picture in the library's grid. "Text" above the document shows
  its words with the outline beside them, where a passage an answer cites is marked.
- A document's tab no longer shows the tokens and texts its indexing used, nor that it went
  without a malware scan.

Operators:

- The converter: the image run as `python -m app.converter`, with LibreOffice, which the image
  now carries, turns each Word, PowerPoint, Excel, RTF and HTML upload into the PDF it is shown
  by. `deploy/compose.yml` runs it as `converter`, with no settings, no volume and a network only
  the worker shares, and the AWS module sets `CONVERTER_URL=http://converter:8080`; without the
  setting those documents are shown by their text (deploying.md, "The converter").

## 2026-11-06

Members:

- The home page has a new look: how Emudoc works in three steps, and the cited passage shown
  beside the answer it supports.

Operators:

- `SIGNUP_OPEN=false` closes the sign-up for a private beta: the sign-up form and a first
  sign-in through Google or Microsoft make no account, the home page and the sign-up page offer
  "Book a call" (`BOOKING_URL`, a booking page; unset, a mail to the support address) where they
  offered "Start free", and `/pricing` sends a visitor to the home page. Members still invite
  their colleagues. The AWS module sets it for staging and production (deploying.md, "The
  private beta").
- Send someone a sign-up link from the operator's area (Users, "Send a sign-up link") or with
  `maintenance invite <address>`: it makes an account with an organisation of its own, works for
  14 days, and works while the sign-up is closed.

## 2026-11-05

Members:

- Pin a document's tab from its menu to keep it at the front of your open tabs. A pinned tab
  shows a pin instead of its cross and stays open when you close the others or close all; click
  the pin to unpin it.
- Drag a document's tab to put it elsewhere among your open tabs. From the keyboard, Ctrl+Shift
  and an arrow (⌘+Shift on a Mac) moves the tab you are on, and a tab's menu has Move left and
  Move right.
- The outline around the file you last clicked in the library keeps all its edges when you point
  at the file next to it.
- A click on a file in the library opens it, and a click on a folder opens it, with no
  double-click needed. Ctrl-click, Shift-click and the boxes still select without opening.
- Clicking a file in the library no longer selects it: tick its box, or Ctrl-click or Shift-click
  it. Once one file is selected, every file shows its box and the bar under the files appears. From the
  keyboard the arrows move without selecting and Space selects; F2 renames the file you are on,
  and Delete deletes the selected files, or the one you are on when none is selected.
- Opening a file's menu, by its "…" button or a right-click, no longer selects it: the files you
  selected stay selected, and the file whose menu is open is shaded while it is. Deleting or
  moving a file from its own menu leaves the others selected.
- Clicking an empty part of the library, or clicking there to close a menu, no longer clears the
  files you selected. Clear selection or Esc does.
- Each file and folder in the library has a box at its left to select it, so several can be
  chosen with no key held, on a touch screen too. The boxes show as you point at a file, and on
  every one once two are selected. A bar under the files then says how many are selected, with
  an Actions button for what can be done to them, and Clear selection. From the keyboard, Ctrl and the arrows move without
  selecting, and Space selects the file reached or lets it go.
- The top right of the page no longer shows "AI model" and "Semantic search". When the AI model
  is out of reach the Ask tab still says so, and a search that matched words alone says that.
- The path above the library's files is always there, reading "All files" at the top, so
  choosing Favourites or Recent no longer pushes the files down. The trash icon now sits in the
  same group as All files, Favourites and Recent.
- Tags are gone: a document's menu no longer offers "Tags…", the library no longer lists a tag's
  documents, and a search's "Filter" no longer narrows by tags. The documents stay where they
  are. A search is still narrowed by type, date, review and folder.
- The library shows what it lists with icons above the files in place of the "Show" menu: All
  files, Favourites and Recent, the one shown pressed, and beside them Trash. Filter by name now
  has a line of its own under them, just above the files.
- Filing rules are gone: the "Show" menu above the files no longer offers "Filing rules…", and
  new documents stay where you upload them. The documents your rules moved keep their
  folders. Move a document yourself by dragging it onto a folder or with "Move to…".
- The search box is now at the top of every page. In a project it searches the project, and the
  results open in a Search tab beside the documents you have open, so you can open a result and
  go back to the others. Anywhere else it searches every project you see when you press Enter.
- The box above the library's files now filters them by name: type part of a name to list the
  matching files from every folder, each with the folder it is in. While the library shows a
  tag, your favourites or your recent files, it narrows those.
- The library no longer repeats the project's name above its search, which the header already
  shows. In a folder, or in a tag, your favourites or your recent files, the path above the
  search starts from "All files", which takes you back to every file.
- A document that is indexed shows a green dot beside its name in place of the tick, which
  looked like a box to tick. A partly indexed one shows an amber dot, and the ring still fills in
  while a document is read.
- The sample project's library no longer shows a note about Emudoc's sample documents above the
  files. The projects page still marks the sample as Emudoc's own documents, which count against
  nothing on your plan.

Operators:

- Migration 9e1493ee558b drops the `tags` table with the tags in it, and `documents.tag_ids`
  with its index; a downgrade makes them again, the table empty and every document carrying
  none. `GET|POST /api/projects/{id}/tags`, `PATCH|DELETE /api/tags/{id}`,
  `PUT /api/documents/{id}/tags`, `POST /api/projects/{id}/documents/tags`, the documents
  listing's `tag=`, the search's `tags` and `every_tag`, and the project export's tags are gone.
- Migration ca622aa5d107 drops the `filing_rules` table with the rules in it, and
  `documents.filed_at`; a downgrade makes them again, the table empty and every document marked
  filed. `GET|POST /api/projects/{id}/filing-rules` and `PUT|DELETE /api/filing-rules/{id}` are
  gone. The audit log's earlier `filing_rule_created|changed|deleted` entries stay and still read.

## 2026-11-04

Members:

- The library's buttons beside the search no longer include Trash and New folder. The trash
  opens from "Trash…" in the "Show" menu under the search, and a new folder from the menu you
  get by right-clicking the library's empty space.
- The projects page no longer lists the first steps under "Get started". A new organisation's
  empty projects page still offers "Create a project", and after an answer about your own
  documents the chat still suggests inviting your team.

## 2026-11-03

Members:

- Smart folders and saved searches are gone: the menu above the files no longer offers "New
  smart folder…", and a search no longer offers "Save the search". The documents they listed
  stay where they are. A search is still narrowed by "Filter" and "Search within these", and a
  tag still lists its documents wherever they are.

Operators:

- Migration 45a76579a0ca drops the `smart_folders` table with the smart folders and saved
  searches in it; a downgrade makes it again, empty. `GET|POST /api/projects/{id}/smart-folders`,
  `PUT|DELETE /api/smart-folders/{id}` and the documents listing's `smart=` are gone.

## 2026-11-01

Members:

- Emudoc has a home page for whoever is not signed in, which says what it does with your
  documents, and a page of its plans: what each allows and what it costs. The logo on the sign-in
  pages leads there, and their foot links the plans.
- The assistant no longer does what a document tells it to. A document that says "ignore your
  instructions", claims an invoice is approved or asks for a draft is read as what the document
  says, and where it matters to your question the assistant tells you that the document holds
  instructions addressed to an AI.
- An answer no longer starts with a note the model wrote to itself on its way, such as "Read the
  invoices directly.", run into its first words. What the model writes as it takes a step, such
  as reading a document, shows only until the step starts, and the answer is what it writes
  once it has looked.

Operators:

- A visitor without a session is shown the public site's home page at `/`, while a signed-in
  browser gets the app there as before, and `/pricing` shows every plan's figures from the code
  and Pro's and Team's prices as Stripe sells them, read at most once an hour. Both are plain HTML
  of the build that the app fills in, and a part about a feature behind a flag shows once the
  flag is on for every organisation. `SEARCH_ENGINES=true`, which production's root on AWS sets,
  asks search engines to list the public pages through `robots.txt` and `/sitemap.xml`; off,
  `robots.txt` turns them away and every page says `noindex`, as a member's page and a share
  link's always do.
- `python -m app.maintenance injection` indexes a supplier's documents that each hold an
  instruction planted for whoever reads them, asks the agent about each, and exits 1 when the
  indexing model wrote what an instruction dictated or an answer did what one said. What the
  agent's tools return now reaches the model marked as data, never instructions, and the
  indexing model is told the same of the document it reads.
- Each kind of model work's prompts have a version, a short hash of what the model is told for
  it, which changes with any edit to them and is kept on the usage rows of its calls.
  `python -m app.maintenance costs` adds the cost of each kind of work by the version of its
  prompts, with the days each was seen, and `golden` and `fact-accuracy` name the versions they
  ran.
- `python -m app.maintenance golden` grades and judges each answer as members read it, without
  the note the model wrote beside a step.

## 2026-10-31

Members:

- Dates and amounts are read the way a document's language writes them: in a German invoice
  "03.04.2026" is the 3rd of April and "1.250,00 €" one thousand two hundred and fifty euros, so
  the facts, the facts table and the cross-checks compare what the document says. A date such as
  "03/04/2026" in an English document, which puts the day first in some countries and the month
  first in others, is read by what the document shows of its country, or left without a date
  rather than guessed. Documents uploaded before are read this way when you index them again.

- Search by meaning understands other languages: a question finds the passage that answers it
  whichever language either is written in, so asking in English how much notice ends a
  contract finds the German one's "Frist von drei Monaten" and the Chinese one's "提前九十日".
  Documents uploaded before are matched this way once Emudoc has prepared them again, which it
  does by itself over the following hours.
- Each document is searched in its own language: Emudoc tells which language a document is
  written in as it reads it, so that a German contract's "Lieferungen" is found by "Lieferung"
  in an organisation that works in English. A document whose words are too few to tell, such as
  a log of dates and amounts, is searched in your organisation's language as before. "Edit
  details" on a document's tab shows its language and lets you set another, or your
  organisation's. Documents uploaded before keep your organisation's language until they are
  read again.
- Comments: talk about a document where it is. The "Comments" button on a document's tab opens
  its threads beside the text; choose words in the text, or open a passage from a citation, and
  press "New comment" to start a thread about them, or comment on the whole document. Type "@" to
  mention a member who sees the project: they are told, in the app and by email as they chose,
  who mentioned them and on which document, and the members who wrote in a thread are told of a
  reply. Resolve a thread once it is settled; its quote stays when the document is read again or
  given a new version. Everyone who reads the project comments, viewers too.
- Activity: the library's "Activity" lists what was done in a project lately, its uploads, new
  versions, deletions, reviews, comments, drafts and runs, newest first, each opening what it was
  about. Comments and the activity reach organisations as they are rolled out.
- Question sets: save the questions you ask again and again in a project, and have them asked
  for you every day or one day a week at an hour you choose, once new documents arrive, or both.
  The library's "Question sets" makes them and runs one at once with "Run now"; each run is a
  report of the answers, citing the passages as a chat does, which you, everyone who sees the
  project or nobody is told of, in the app and by email as each of you chose: the set's name and
  how many questions were answered, never an answer. Each question counts as one of the month's
  questions. Question sets come with Pro and above, and reach organisations as they are rolled
  out.
- What arrived this week: a notice you turn on from your account page, which on Monday morning
  names the documents that arrived over the week in each project you see. It is off until you,
  or your organisation's owners and admins for its members, turn it on.

Operators:

- `python -m app.maintenance golden` also has `INDEX_MODEL` judge each answer: whether it is
  right against the question's known answer, and whether each passage it cites states what the
  sentence citing it says. The report prints an answer the judge doubts whole with its reason,
  each citation it finds unsupported with its sentence, and the answers it found right with the
  citations supported, the answers' citation precision. The verdicts fail nothing: the words
  still decide the exit. It adds about a cent a run on Claude Haiku 5.5 (deploying.md, "The
  golden questions").
- `python -m app.maintenance fact-accuracy` scores the facts `INDEX_MODEL` reads: it indexes the
  sample's files with extraction templates on, in an organisation it makes and deletes, and
  compares the facts read from the contract, the invoices and the price list with values
  labelled by hand, a field at a time. It prints each field's recall and precision with what was
  missed or read wrong, records nothing as an organisation's usage, and exits with 1 when a field
  was not read as labelled. A run costs about a cent on Claude Haiku 5.5 (deploying.md, "The
  facts read").

- Claude Opus 5.5 and Sonnet 5.5 answer in full as `AGENT_MODEL`. They return what a reply
  writes beside a tool call as a progress note the API never hands back, and the agent was asked
  to write its answer beside the call that suggests what to ask next, so about half their
  answers came back empty or as one line about the suggestions. The agent now suggests them
  before it writes the answer; the golden questions on Claude Opus 5.5 went from 7 of 13 to all
  13.

- `python -m app.maintenance golden` asks the agent thirteen questions about the sample
  project whose answers are known, over a copy of the sample in an organisation it makes and
  deletes, and grades each answer on the figures and names a right one says and the documents it
  cites. It prints a wrong answer whole with what it lacks, records nothing as an organisation's
  usage, and exits with 1 when an answer was not as known. The Models workflow runs it every
  morning beside `models`, and both once a change to the prompts, the agent, its search or the
  sample is on `main`, once the repository names the models and holds their keys (deploying.md,
  "The golden questions").
- The enrich stage tells the model how a document's own language writes dates and numbers, and
  reads a fact's number or date again from its value where the value's marks, or the language,
  settle it, in the model's place. The migration adds `documents.read_language_code`, the ISO
  code the detection named; the documents parsed before keep null until they are parsed again.
- The default embedding model is Google's EmbeddingGemma 300M (`EMBEDDING_MODEL=embeddinggemma`,
  its prefixes `task: search result | query: ` and `title: none | text: `), in place of
  nomic-embed-text v1.5, which was trained on English: it ranked an English question's passage
  in another language first 34 times of 42 where v1.5 did twice. `deploy/compose.yml` downloads
  its 334 MB of weights at 8 bits from ggml-org, and the server's batches hold 24,576 tokens, so
  that 6,000 characters it reads as their bytes are refused as past the context, which the app
  cuts. Its licence is the Gemma Terms of Use, which an operator reads before running it. The
  vectors keep their size, 768: a deployment on the defaults embeds every document again after
  its next release, at bulk priority, about 38 seconds per 32 passages on two CPUs; to stay on
  v1.5, set its model, prefixes and weights first (deploying.md, "The embeddings endpoint").
  Development runs `ollama pull embeddinggemma` once.
- The worker tells each document's language as it parses it, with py3langid's model (BSD, with
  numpy), which it loads at the first parse and holds, about 75 MB; the API's process never
  loads it. The migration adds two columns to `documents`; a member's change of a document's
  language moves its passages by the language job a change of the organisation's queues.
- The feature flag `comments` (at 0) turns on comments on documents, their mentions and replies
  told as two new kinds of notice, and each project's activity, which reads its rows of the audit
  log. The audit log gains a comment added and deleted and a thread resolved and opened again,
  by the document's name, never a comment's words. The migration adds two tables.
- Each check runs once for a change while the app is in development: the developer's own pull
  requests run nothing and are merged once the suite and the frontend's checks pass on their
  machine; CI runs the browser test, the load test, the image, the secrets and the scripts once
  the change is on `main` (the suite and the frontend's checks by hand and on Dependabot's pull
  requests, which run everything); OpenTofu (`opentofu.yml`) and the embeddings server's check
  (`embeddings.yml`) are workflows of their own, run once a change to what they check is on
  `main`; Pooler runs only then too, no longer every night or on pull requests; Audit runs every
  Monday rather than every morning, and on `main` alone; `main.json` requires no check. Put the
  checks back on every pull request before the app has users (deploying.md, "The repository's
  rules").
- Question sets, behind the flag `question_sets`, rolled out to none, on Pro, Team and
  Enterprise (the plans' `question_sets`): a set's job (`run_question_set`) queues its next
  waking as it ends, and the housekeeper queues one lost; a report is answered by
  `write_question_report` in the answerers' lane, each question counted as one of the month's
  and held to the day's spending, its calls `answer` usage of the report, and kept as long as a
  chat. Programs hear of a report by the webhook event `report.completed` and read it from
  `GET /api/v1/projects/{id}/reports` and `GET /api/v1/reports/{id}`. The weekly digest is told
  by a `send_digests` job that wakes every hour, which the housekeeper queues when there is
  none. Migration 28ce2d5915d8; the privacy policy names the sets, their reports and the new
  notices.
- pyasn1 0.6.5, past four HIGH CVEs Audit found in 0.6.4 (denials of service in its decoder),
  which google-auth's `pyasn1-modules` brings into the image.
- Three findings Audit made in the images beside the app are accepted in
  `deploy/.trivyignore.yaml` until 14 November, when the next rebuilds should carry the fixes:
  go-jose's CVE-2026-116587 in Caddy, which parses a peer's JWK only as an ACME server, which
  the Caddyfile does not run; curl's CVE-2026-92392 in Caddy's and ClamAV's images, an HTTP/2
  upload fault that nothing there reaches (only ClamAV's clamsubmit uploads, and nothing runs
  it); and brotli's CVE-2026-111497 in llama.cpp's server image, whose curl decodes brotli only
  when asked to, which the weights' download does not.

## 2026-10-30

Members:

- A large upload is indexed as quickly at its end as at its start: in a project of thousands of
  documents, each one indexed had taken longer than the one before.
- Search puts first the passages that hold every word you typed, then those that hold all but
  one of them, then the rest, and answers sooner in a large project.
- Search every project: the box on the projects page searches all the projects you see at once,
  the best passages first wherever they are, each with its project's name, and opens the document
  at the passage. "Filter" there narrows the search to some of the projects, to documents of some
  types or to those dated within a span. A private project is searched only by those who see it.

Operators:

- The check after each document indexed of whether its project has settled, to write its
  suggested questions (`_SETTLED` in `app/suggestions.py`), looks the queued index jobs up once
  rather than once for each of the project's documents: since 2026-10-25 it took 22 s a document
  at 22,000, run by every indexer at once, and the load test indexed 30,000 documents 207 a
  minute, one run slowing to 16 a minute at 22,000. It takes about 25 ms, and the 30,000 were
  indexed 601 a minute (deploying.md, "Load").
- A search's keyword ranking ranks the passages that hold every word of the query, then all but
  one, and those that hold any only while the others are too few, so that its cost is the
  passages that hold the words: in a project of 30,000 documents a search took 43 ms at the
  median rather than 222, and 111 at p95 rather than 304 (deploying.md, "Load").
- `GET /api/search` searches every project a member sees in one ranking, and `GET
  /api/document-types` lists the types of their documents; both count against `RATE_SEARCHES`
  with the project's search and facts.

## 2026-10-26

Members:

- Search filters: "Filter" above the library's search results narrows a search to documents of
  some types, dated within a span, with tags, in review or in a folder, each part shown with a
  cross that takes it off; and searching while the library shows a tag's or a smart folder's
  documents searches them alone.
- Search within what you found: "Search within these" keeps the words you searched for, so that
  the next words search only the documents that mention them all.
- Saved searches: "Save the search" keeps a search as a smart folder, which runs it again each
  time it is opened. A smart folder can also list the documents that mention some words.
- Several files at once: choose them together in the library, then "Tags of … documents…" in
  their menu puts a tag on every one or takes it off every one, leaving a tag only some carry as
  it is; "Index … documents again" reads them all again; and "Export … documents" makes a ZIP file
  of those documents alone, which the project's exports on the projects page list.
- A document's title, type and date: where the reading got one wrong, set it yourself with "Edit
  details" on its tab. What you set is kept when the document is read again or given a new
  version, and the dialog shows what the reading found, with "Use the reading" to take it again.
- Notes: "Add a note" on a document's tab writes what its file does not say, which everyone who
  reads the project reads there with who changed it last.
- Copies: a document's tab names the other projects that hold the same file, and the projects page
  leads to every file held in more than one project, with what the copies take.
- Tags: give a document tags from "Tags…" in its menu in the library, making a new one there if
  you like, and list a tag's documents wherever they are from the menu above the files, which reads
  "All files" at first. "Tags…" in that menu renames, recolours and deletes the project's tags.
- Smart folders: "New smart folder…" in the same menu saves a list of the documents that match
  what you choose (tags, types, a span of dates, review states, a folder), which everyone in the
  project sees and which documents join as they are read.
- Favourites and recent documents: "Add to favourites" in a document's menu stars it, and the menu
  above the files lists your favourites, or the last documents you opened in the project. Both are
  yours alone.
- Order the files by name, date added, the document's own date or size, either way, and show them
  as a list or as a grid of pictures of their first pages, from beside that menu.
- Filing rules: under "Filing rules…" a rule files new documents as they are read, by their type
  (such as invoice), by words a fact read from them holds (such as a supplier's name), or all of
  them: into a folder, under a folder by year or by year and month, or with a tag. A document you
  upload into a folder stays where you put it.

Operators:

- Migration 1c8fe42c43c7 adds the table of documents' notes, and to `documents` the columns of
  what a reading found beside the title, type and date a member may set, copied from them, an
  update of each row once, and an index on `sha256` by which a file's copies in other projects
  are found.
- Migration ef4d50a71594 adds the tables of tags, smart folders, filing rules, favourites and
  recent documents, three indexes on `documents` for the library's orders and a GIN index on its
  new `tag_ids`, and marks every document there is as filed, an update of each row of `documents`
  once, so that a filing rule made later files only what arrives after it. The audit log records
  filing rules made, changed and deleted.

## 2026-10-25

Members:

- Pin a chat to the top of your chat list: "Pin chat" beside it in the list keeps it under
  "Pinned", whether you started it or another member shared it with the project, and your pins
  are yours alone. "Unpin chat" puts it back among the others.
- Keyboard shortcuts in a project: Ctrl+Shift+O (⌘+Shift+O on a Mac) starts a new chat,
  Shift+Esc goes to the box you ask in, Esc there stops the answer being written, and Ctrl+/
  (⌘+/) lists them.
- What an answer, a draft, a report or a document cost is now shown to the organisation's owners
  and admins alone. Everyone who reads them still sees the AI model, its calls and its tokens.
- Ask a chat about one folder or a few documents rather than the whole project: "About" above
  the box you ask in chooses all the documents, a folder or a document open in a tab, and its
  answers read only those. A folder's menu in the library offers "Ask about this folder" and
  "Summarise this folder", a document's "Ask about this document", and files chosen together
  "Ask about these documents" or, for two, "Compare the two documents".
- "Regenerate" under the last answer of your chat writes it again from the same question, and
  "Edit the question" beside your last question lets you change it and have it answered again in
  its place. Each counts as a question.
- A new chat now suggests questions written from the project's own documents, once they are
  indexed, rather than the same three for every project. Under the last answer of your chat, "Ask
  next" suggests two or three questions that take it further: click one to ask it.
- A PDF may now have up to 5,000 pages, where it was 500: a long manual, a book or a year of
  statements is read as one document, its outline and its headings over the whole of it, and
  searched and asked about like any other. Its first pages, as many as the AI model reads of one
  document, get section summaries and facts, as before.
- Give a document a new version when its file changes, such as a contract that comes back
  signed: "Upload a new version" in its menu in the library, or "Versions" on its tab. The
  document keeps its place, its chats, its share links, its review and your checks of its facts,
  and reads the new file in place of the old one; an approved or disputed review is opened again.
  Its earlier files stay under "Versions", each to download or to read its text, and in the
  project's export. A file of a connected folder that changes there now becomes its document's
  new version too, rather than a new document with the old one in the trash.
- Scanned pages and photos are read the way their text runs, even when photographed sideways,
  handwriting included: a word the AI model cannot read is marked [illegible], a signature
  [signature] and a ticked box [x], and a table is kept as a table, ruled or not, so its rows
  give facts.
- A PDF set in two or more columns is read a column at a time, so each heading's section holds
  the text below it in its own column, and a table stays in the column it stands in.
- A PDF page whose text runs sideways or upside down, such as a wide table printed turned on a
  portrait page, is now read as words, so it can be searched, asked about and cited, and the
  cited passage is marked where the page shows it. A line running up the margin, such as a
  reference number, is read after the rest of its page.
- Take a photo of a page from your phone or tablet: the camera button beside "Upload a folder"
  opens the camera, and the photo is uploaded into the folder you are in, named by when you took
  it, and read like a scanned page.
- Upload a ZIP archive: its files go into the folders it holds, as an uploaded folder's do, in a
  folder named after the archive unless everything in it is in one folder already. Each file is
  checked as any upload is, and one that cannot be read is listed with why while the others go
  in. An archive locked with a password, or one that would unpack to more than one upload may
  hold, is turned away with what to do.
- Upload an email saved from your mail program (.eml): its text becomes a document, headed by who
  sent it to whom and when, and each file attached to it a document beside it, in the folder you
  uploaded it into, as mail sent to the project's address arrives.
- Upload web pages (.html, .htm) and RTF documents (.rtf). A saved page keeps its headings,
  lists and tables and leaves out its menus, scripts and styles, in whatever encoding it was
  saved in; an RTF document is read paragraph by paragraph.
- Upload PowerPoint presentations (.pptx): each slide is read as a page, its title a section of
  the outline, with its text, tables and speaker notes, so an answer cites the slide it read. A
  presentation saved in the format from before 2007 (.ppt) is turned away at the upload with how
  to save it as .pptx.
- Upload TIFF scans and HEIC photos: a TIFF of several pages, as a scanner or a fax saves it, is
  read page by page like a scanned PDF and shown by its pages, and so is a photo an iPhone saves
  as HEIC. A browser shows neither by itself, so "Download original file" gives you the file as
  you sent it.
- Print a document as it reads: "Print" on its tab, or your browser's own printing, prints the
  document alone, without the library, the chat or the buttons around it, and a long document
  runs on over as many sheets as it needs. A PDF prints its pages, each on a sheet of its own; a
  passage you opened from a citation keeps its highlight. A PDF saved from the print is named
  after the document. A page a share link opens prints the same way.
- Download an answer, or a whole chat: "Download the answer" under an answer saves it with its
  question as a Word document, a PDF or Markdown, and the download button in the chat's header
  saves every question and answer of the chat in one file. Each citation becomes a numbered note
  naming the document, section and pages of its passage, and the file says who asked, in which
  project and on which day.
- Share an answer by a link too: "Share the answer" under a finished answer makes one as for a
  document. Whoever holds it reads the question, the answer and the passages it cites, each
  citation leading to its passage, and nothing else of your documents. The link stops when its
  chat goes into the trash, or when the chat is no longer shared with you.
- Share a document with someone outside the organisation by a link that needs no account: "Share
  by link" on its tab makes one that runs a day, a week or 30 days, with a password if you
  choose one, and shows its address once to copy. Whoever holds it reads the document, its text,
  pages and file, and nothing else; you see how often it was opened, and revoke it at once. A
  link stops when you no longer may make it, the document goes into the trash, or the
  organisation's owners and admins turn links off on the organisation page, where they list and
  revoke every link. Links come with Pro and above, and reach organisations as they are rolled
  out.
- The upload client sends through the versioned API, so a copy downloaded now goes on working
  as Emudoc changes, and files it sends again after the connection dropped before Emudoc
  answered are taken once and counted as sent. A copy downloaded before goes on working too;
  download it again under API keys on the organisation page for this.
- The API reference, at `/api-reference`, describes every route of the versioned API and every
  event your webhooks are sent: what each takes and answers, and a `curl` request to try at your
  Emudoc's own address. Anyone may read it, before they hold a key; the API keys section of the
  organisation page and "Organisations, members and roles" in the help link to it.
- Webhooks tell your programs what happens in the organisation as it happens: owners and admins
  add one under Webhooks on the organisation page, with the address of a program and the events
  it hears of, a document indexed or one that could not be, and a run of cross-checks worked
  out. Each event carries what the versioned API answers for the document or the run, signed
  with the webhook's secret, which is shown once; an event not taken is tried again for about a
  day and a half, and each webhook's deliveries list what was sent and how it was answered.
  "Organisations, members and roles" in the help shows how a program checks the signature.

Operators:

- A chat is about the project, a folder or chosen documents (`app/scopes.py`): every tool of the
  agent reads only the chat's live documents, held by the server, and the prompt names them by id.
  `POST /api/chats/{id}/messages/{id}/regenerate` writes a chat's last answer again, its question
  edited or not, and is held and counted as `ask` is: the questions' rate limit, `MAX_ANSWERS`,
  the day's spending and the month's questions. The migration adds `chats.scope_kind` and
  `scope_folder_id` and the table `chat_scope_documents`.
- A project's suggested questions are a job of their own, `suggest_questions`, with the
  indexers at bulk priority: queued when an index job leaves its project with no document still
  to index, at most one queued a project and due an hour after its last writing at the soonest,
  one request of `INDEX_MODEL` with the project's catalogue (`app/suggestions.py`). Its calls are
  the usage kind `suggestions`, by no member, which `costs` and `usage` list; the day's spending
  limits skip it, and the sample project is never written. An answer's follow-ups are the agent's
  `suggest_follow_ups`, which ends the answer when sent with its last words, so they cost no
  further turn. The migration adds `chat_messages.follow_ups` and `projects.suggested_at`.
- `PARSE_MAX_PAGES` is 5,000 by default, where it was 500. A PDF of more than `PARSE_PART_PAGES`
  (250, new) pages is parsed that many pages a child, each within `PARSE_TIMEOUT` and
  `PARSE_MEMORY_MB`, the pages read kept in the temporary directory (about 2.5 MB for 250 pages
  of prose), and one more child makes the document of the parts, its running headers and
  heading levels the whole file's (`_in_parts` in `app/parsing/limits.py`, `read_pdf` and
  `assemble` in `app/parsing/pdf.py`). The slot of `PARSE_CONCURRENCY` is let go between parts.
  After the first part the month's pages are checked, and after each the scans read so far
  against `OCR_MAX_PAGES`, so a file the stage would refuse is not read to its end; a part that
  runs out of time names its pages. `0` reads every PDF in one child, as before.
- A document's new version (`app/versions.py`, `POST /api/documents/{id}/versions` and
  `/api/v1/documents/{id}/versions`) keeps the document's row and reads the new file from its
  scan on; the earlier file stays at its key, the new one under the document's prefix at
  `versions/{n}/original.<ext>`, and `document_versions` holds the earlier ones with their text
  (migration b244c80cacf0). The plan's storage counts the earlier files, and the new file's pages
  count again. The orphan sweep, the trash's purge and `after-restore` read a document's whole
  prefix; `after-restore` names a document missing one of its versions' originals. A version
  refused while its document is being indexed counts `indexing` in
  `emudoc_upload_rejections_total`, and a connected folder's changed file counts `replaced` as it
  becomes its document's new version.
- A `.zip` the members' page uploads is unpacked in the API's request (`app/unpacking.py`), the
  archives of one upload bringing `MAX_UPLOAD_FILES` files and `MAX_UPLOAD_REQUEST_MB` unpacked
  between them, read from their directories before anything is inflated; what a packer adds
  (`__MACOSX`, dotted names, `Thumbs.db`) is left out, and an archive locked, damaged or packed
  with Deflate64 is refused as `unopened`. A request with an API key is refused one.
- An `.eml` the members' page uploads is opened in the API's request (`app/unpacking.py`, up to
  the 40 MB mail in takes), its text and attachments kept as documents, never the message;
  `emudoc_upload_rejections_total` counts one that cannot be opened as `unopened`. A request
  with an API key is refused one, so `/api/status` and `/api/v1/status` list the same extensions
  as before.
- HTML (`.html`, `.htm`, the standard library's parser) and RTF (`.rtf`, the `striprtf`
  package, BSD) uploads are read in the parse child; an original is served as a download, never
  as a page.
- PowerPoint presentations (`.pptx`) are read by python-pptx in the parse child, a slide a page:
  `PARSE_MAX_PAGES` refuses a deck by its slides, and its slides count as pages of the month.
- TIFF (`.tif`, `.tiff`) and HEIC (`.heic`, `.heif`) uploads: the parse child counts a TIFF's
  pages against `PARSE_MAX_PAGES` as a PDF's, and the render children draw their pages for the
  pane, the thumbnails and the model, so the decoders never run in the API or the worker. HEIC
  is decoded by the `pi-heif` package, whose wheel bundles libheif and libde265 (LGPLv3).
- `RATE_EXPORTS` (10 a minute per member) now holds the downloads of an answer and of a chat
  too (`GET /api/chats/{id}/messages/{id}/export`, `GET /api/chats/{id}/export`), and the audit
  log keeps them as `answer_exported` and `chat_exported` (migration 5937fbc7e744), by the
  chat's id and the format, never what was asked or answered.
- Share links: `RATE_SHARED_LINKS` (30 a minute per client address) holds their openings, and
  `RATE_SHARED_PASSWORDS` (10 an hour per link and per address) their wrong passwords. The flag
  `share_links` turns them on for an organisation (`flags`), its plan's `share_links` figure
  allows them, and `after-restore` ends every link, since one revoked after the moment restored
  to would work again. The privacy policy names their cookie, `emudoc_shared`.
- The upload client sends to `POST /api/v1/projects/{id}/documents`, each request with an
  `Idempotency-Key`, and follows the documents by `GET /api/v1/projects/{id}/documents?ids=`.
  The AWS instance's Caddyfile and deploying.md's Caddy and nginx examples let a large body
  through `^/api/(v1/)?projects/[0-9]+/documents$`, the versioned API's upload as well as the
  page's: widen a proxy of your own the same way, or the client's requests are held to its
  small limit and a larger file is refused. Copies from before call the page's routes, which go
  on answering a key.
- `GET /api/v1/openapi.json` serves the versioned API's contract to anyone, without a key,
  naming `APP_URL` as its server, and `/api-reference` is a page of its own drawn from it
  (`frontend/reference.html`, a third build of `pnpm build`, its scripts' budget apart): a proxy
  that lets only some paths through lets both (deploying.md, "Support"). The contract now names
  its key's scheme (`APIKey`), its groups and example values; nothing a program calls changed.
- The worker posts the organisations' webhooks (`backend/app/webhooks.py`), a `deliver_webhook`
  job a try, to addresses on the internet over HTTPS alone, connecting to the address it checked
  outside the deployment's own network and taking no proxy from the environment: its machine
  must reach the internet on port 443. Their secrets are encrypted with `ENCRYPTION_KEYS`, which
  `encrypt-secrets` covers; their delivery logs are kept 30 days (migration bb533acac8ec), and
  `emudoc_webhook_deliveries_total` counts the tries. deploying.md's "Webhooks" says more. The
  contract `backend/app/api/v1/openapi.json` lists the events as OpenAPI's `webhooks`.

## 2026-10-24

Members:

- Programs build on a versioned API at `/api/v1`, called with an API key, which changes only by
  adding to it, so that what a program does today it goes on doing: projects, folders, uploads
  followed until they are indexed, search and facts, questions with their answers followed as
  they are written, and runs of the cross-checks with what they found. A request that makes
  something may carry an `Idempotency-Key`, and sent again with it after an answer that did not
  arrive, it is carried out once. "Organisations, members and roles" in the help says more.

- The upload client sends a folder from your computer into a project, its folders as they are,
  or goes on sending each file a scanner saves to a folder: download `emudoc_upload.py` under API
  keys on the organisation page and run it, by Python 3.9 or later, with a key that reads and
  writes. It leaves out what Emudoc does not read, says why a file was refused and when the files
  are read, sends each file once however it is renamed, and never deletes or moves one. "Uploading
  and organising documents" in the help says how to run it.

Operators:

- The versioned API under `/api/v1` (`backend/app/api/v1/`) takes API keys alone and answers in
  models of its own; its contract is `backend/app/api/v1/openapi.json`, which the suite holds to
  the routes and `python -m app.api.v1.contract` writes again, refusing a change that would
  break a program written against it. A request's `Idempotency-Key` is kept with its answer for a
  day in `idempotency_keys` (migration b613bb7c52ef), which the housekeeping round prunes, as
  deploying.md's "Retention and deletion" and the privacy policy say.

- `main` takes a commit only through a pull request whose checks passed, CI's fourteen jobs on
  the branch brought up to date (the ruleset in `.github/rulesets/main.json`, which
  `tests/test_repository.py` holds to CI's jobs), and a release to production is a job of the
  GitHub environment `production`, which waits for its reviewer: the production root's release
  role trusts that environment's jobs alone (`github_environment`), so apply it once. Staging's
  still trusts `main`. deploying.md, "The repository's rules", has the commands. CI runs the
  backend's suite in three parts a database (`pytest --shard K/N`) and the load test in a job
  of its own, so a run takes about half as long.

- The app serves the upload client at `/api/help/emudoc_upload.py` (`backend/app/client/`), a
  script of Python's standard library that sends through the API with a key: `GET /api/status`
  answers a key too, its `uploads` naming the extensions read, and each file an upload refuses
  names its place in the request (`index`) and, when the project holds its bytes already, the
  document that does (`duplicate_of`). The client halves a request a proxy refuses as too large,
  so a proxy's body limit under `MAX_UPLOAD_REQUEST_MB` costs it requests rather than files.

- Connected folders from OneDrive and SharePoint too, through Microsoft Graph, where
  `ONEDRIVE_CLIENT_ID` and `ONEDRIVE_CLIENT_SECRET` name an Entra ID app with the delegated
  permissions `offline_access`, `User.Read`, `Files.Read.All` and `Sites.Read.All` and the
  redirect URI `{APP_URL}/api/connectors/callback` (`ONEDRIVE_TENANT`, `common` by default, says
  whose accounts): browsing starts at the member's OneDrive and the libraries of the SharePoint
  sites they follow, a folder is read by its children and asked after through its drive's delta,
  and the refresh token Microsoft gives anew with each access token is kept. The subprocessor list
  and the privacy policy name OneDrive where it is set up.
- Connected folders from Dropbox too, where `DROPBOX_APP_KEY` and `DROPBOX_APP_SECRET` name a
  scoped app with `files.metadata.read`, `files.content.read` and `account_info.read` and the
  redirect URI `{APP_URL}/api/connectors/callback`: a folder is read in one listing with its
  subfolders, its cursor what the next sync asks from, and named by its id, so it stays connected
  as it moves; a Paper document is exported as Markdown. Disconnecting one gives its grant back.
  The subprocessor list and the privacy policy name Dropbox where it is set up.
- Connected folders, behind a flag of their own, `connectors`, rolled out to none, on the plans
  that include them (Team and Enterprise): the library's "Connected folders" connects a member's
  Google Drive through Google's consent, the grant kept encrypted with `ENCRYPTION_KEYS`, and the
  member chooses one of their folders and where its files go. The worker syncs each every 15
  minutes and at "Sync now", asking Google's change log first: new and changed files become
  documents through an upload's checks, as their member's upload, with a changed file's earlier
  document and the document of a file taken out of the folder in the project's trash, but only
  when the folder was read whole; Google's own documents are exported as Word, Excel or PDF. A
  connector pauses when Google no longer takes its grant or its member no longer may change the
  project, and goes with its member's membership; disconnecting it gives the grant back. New
  settings `GOOGLE_DRIVE_CLIENT_ID` and `GOOGLE_DRIVE_CLIENT_SECRET` (an OAuth client of its own,
  redirect URI `{APP_URL}/api/connectors/callback`, scope `drive.readonly`, which Google verifies
  before it serves more than a hundred users) and `CONNECTOR_BASE_URL` (the browser test's
  stand-in, refused in production); new tables `connectors` and `connector_files`, the audit
  actions `connector_created`, `connector_changed` and `connector_deleted` (a migration), the job
  kind `sync_connector` and the metrics `emudoc_connector_syncs_total` and
  `emudoc_connector_files_total`. The subprocessor list names Google Drive as what the folders
  are read from, and the privacy policy the grant, the record of the folder's files and the
  cookie `emudoc_connect`, which holds the consent's handshake for ten minutes.
- Mail in through Amazon SES: the AWS module's new `inbound` (both roots name `in.<domain>`)
  verifies the domain, points its MX at SES's receiving endpoint, and adds a rule of the
  environment's to the account's one active receipt rule set, which the account root now makes
  (apply it first): each message goes into a bucket of the environment's and SES's notice of it,
  with its SPF, DKIM and DMARC verdicts, through an encrypted SNS topic into an SQS queue,
  `INBOUND_MAIL_QUEUE`. A worker thread waits on the queue and queues a job per notice, which
  reads the message from the bucket, takes it in and deletes the copy; a sender is proven by
  DMARC, or for a domain without a DMARC policy by DKIM whose every signature is of its domain.
  The subprocessor list names Amazon Web Services as receiving the projects' mail where the
  queue's region says, and `DATA_REGION` refuses a queue outside it; SES wins where Resend is set
  up too. `DB_MAX_OVERFLOW` is 21 by default (it was 20), so that the default pool holds the
  worker's threads with both of SES's queues' readers.
- Mail in through Resend: with `INBOUND_DOMAIN`'s MX at Resend and its webhook taking
  `email.received` beside the bounces, a message to a project's address queues a job that fetches
  it from Resend's API (`RESEND_API_KEY` with full access) as it was sent, with Resend's SPF, DKIM
  and DMARC verdicts, and takes it in; one to any other address is dropped. A Resend out of reach
  is asked again after 1, 2, 4 and 8 minutes, and a message given up is among the failed jobs,
  named by Resend's id. Production now refuses `INBOUND_DOMAIN` without a provider that hands its
  mail over, and Resend receiving it under `DATA_REGION`; the subprocessor list says Resend
  receives the projects' mail. New job kind `receive_mail` (a migration).
- Email in, behind a flag of its own, `email_in`, rolled out to none: the library's "Email in"
  gives a project an address of its own at the new setting `INBOUND_DOMAIN`, and whoever may
  change the project lists whom it takes mail from besides themselves, addresses or domains but
  never a mail service's whole domain, and the folder what arrives goes into. A message the
  provider proved its sender's, from a member who may change the project or a sender listed,
  becomes documents: its text as a `.txt` file named by its subject, with its From, To, Date and
  Subject above, and each attachment as an uploaded file, through an upload's checks; anything
  else is turned away, with nothing sent back. The page lists what became of the last messages,
  kept 30 days, and the privacy policy says so where `INBOUND_DOMAIN` is set. No provider hands
  mail over yet: `python -m app.maintenance receive-mail <file.eml>` hands over a message saved
  as it was sent. New tables `project_mailboxes` and `inbound_messages` and the audit actions
  `mailbox_created`, `mailbox_changed` and `mailbox_deleted`, which count the senders and never
  name them (a migration); new routes `GET|PUT|DELETE /api/projects/{id}/mailbox` and `POST
  /api/projects/{id}/mailbox/address`, which API keys take; the metric
  `emudoc_inbound_messages_total` by outcome. A document that arrives by mail is audited as
  `document_uploaded` with `by_email`, by its member, or by nobody for a sender outside the
  organisation.
- Drafts from the chat: asked in a chat for a reply email, a dispute letter, a memo, a table or a
  report, the agent offers it under its answer as a draft of that kind about the documents it
  found, with what it should say, and the member's "Write the draft" has it written into the
  project's drafts and opens it there, counting no question beside the one asked. Nothing is
  written before the press, so a document cannot make drafts, and only a member who could ask for
  a draft on the drafts page is offered one. Behind the flag `drafts`, as the drafts are. New
  columns `chat_messages.draft_offer` and `draft_id` (a migration), the answer's `draft` in the
  chat's API, and a new route `POST /api/chats/{id}/messages/{id}/draft`, audited as
  `draft_created` with the chat's id.
- filelock 4.2.1, past six CVEs Audit found in 4.2.0 (only pip-audit uses it, in the backend's
  `audit` group, never the image). Brotli's CVE-2026-111497 in Caddy's and ClamAV's images is
  accepted in `deploy/.trivyignore.yaml` until 14 November: Alpine fixed it in 1.2.1, which
  neither image has been rebuilt with since; in Caddy's only curl links the library, which
  nothing runs, and in ClamAV's only freshclam's downloads from ClamAV's own mirror reach it.
- Draft templates: the drafts' "Templates" lists how the project's drafts of each kind read, a
  built-in template for each kind, which whoever may change the project customises into the
  project's own, its tone, its parts in order, which of them every draft has and what else every
  draft of the kind should do, and gives back for the built-in one. A writing follows the
  template in force as it starts; the kind's layout and the rules every draft keeps, its
  citations and placeholders, stay as they were. New table `draft_templates` and the audit
  actions `draft_template_created`, `draft_template_changed` and `draft_template_deleted`, by
  the kind and its parts' names (a migration); new routes `GET
  /api/projects/{id}/draft-templates` and `PUT|DELETE /api/projects/{id}/draft-templates/{kind}`,
  which API keys take.
- A draft's version downloads as a Word document, a PDF or its Markdown from its "Download", its
  citations numbered notes naming the document, section and pages of each passage, and its line
  breaks kept, as the draft's page now keeps them too. The PDF is set in Noto Sans, which the
  image now carries (`backend/app/fonts`, under the SIL Open Font License), for the Latin, Greek
  and Cyrillic alphabets. New route `GET /api/drafts/{id}/versions/{n}/export?format=docx|pdf|md`,
  which API keys take, held to `RATE_EXPORTS` with the facts table's files, and the audit action
  `draft_exported`, by the draft's kind, the version and the format (the same migration).
  reportlab and markdown-it-py are dependencies of the app now, no longer of its tests and its
  audit alone.
- Drafts, behind a flag of their own, `drafts`, rolled out to none: the library's "Drafts" asks
  the model for a reply email, a dispute letter, a summary memo, a comparison table or a report,
  saying what it should say, about the documents open beside the library or about what the model
  finds; a disputed document's review offers "Write a dispute letter". The worker has the agent
  write it from each document's facts beside the passages they came from and from what the
  project's newest run of cross-checks found about them, citing passages as an answer does. A
  draft keeps every version: the model's, each member's edit saved as a new version (refused when
  another was saved meanwhile) and each writing again with what is asked now, each with the
  passages it cites as they were. Asking for one, or for it again, counts as one of the month's
  questions and is held to the agent's runs in flight and the day's spending as a question is.
  New tables `drafts` and `draft_versions`, the job kind `write_draft` in the answerers' lane,
  the usage kind `draft` and the audit actions `draft_created`, `draft_changed` and
  `draft_deleted`, by the draft's kind, never what it says (a migration). New routes `GET|POST
  /api/projects/{id}/drafts`, `GET|DELETE /api/drafts/{id}`, `GET /api/drafts/{id}/versions/{n}`,
  `POST /api/drafts/{id}/versions` and `POST /api/drafts/{id}/rewrite` (the asking ones held to
  `RATE_QUESTIONS`), which API keys take. A draft is kept as long as a chat, from when it was last
  asked for or saved, which the privacy policy now says (the terms' edition is unchanged).
- An automation may put in review what arrived for it, every document of it or those its
  cross-checks found a difference about, assigned to a member it names, once its run is worked
  out; it never moves a document a member put in review already. Its assignee is told once of
  them all, and each move is in the audit log by Emudoc, naming the automation. New columns
  `automations.review` and `assignee_id` (a migration).
- Documents' reviews, behind the flag `extraction_templates`: a member puts a document in review
  from its menu in the library or its tab, assigns it to a member who sees the project and may
  change it, and approves or disputes it; a member assigned by another is told, a new kind of
  notice, in the app and by email. The library's "Reviews" lists a project's documents of each
  state, every member's or one's own, and the library and the facts table mark each document's
  review. The facts table's "To review", the rows with a fact nobody checked, is now "Unchecked"
  (the query's `unchecked`, the count `unchecked`), and its file gains a "Review" column, its
  "To review" column now "Unchecked". New columns `documents.review`, `assignee_id`,
  `review_changed_by` and `review_changed_at`, the notice kind `review_assigned` and the audit
  actions `document_review_changed` and `document_assigned` (a migration). The privacy policy
  names the reviews and the notice.

## 2026-10-23

Members:

- When Emudoc's background work is not running, the header says so ("Background work off"),
  and a chat says that questions cannot be asked until it is back, rather than taking a question
  that would wait unanswered. Documents uploaded meanwhile are kept and indexed once it runs
  again.
- Once an upload is sent, its panel in the library follows its documents as they are indexed:
  "12 of 40 ready; 26 being indexed; 2 could not be indexed.", with how many are partly indexed
  and how many wait for a retry. Those that could not be indexed are listed with why, and each
  opens in its tab ("Index again" is in its menu in the library). The panel stays until you
  close it or upload more.
- A document that was read but went without its summaries and facts, or without its search by
  meaning, is marked "Partly indexed" with an amber check, and says what it lacks: "Found by its
  words, not yet by meaning", "No summary or facts yet". It can be searched by its words and
  asked about meanwhile. "Index the rest", on its tab or in its menu in the library, adds only
  what it lacks, and the library offers the same for every such document of the project at
  once. While the AI model or search by meaning is unavailable, the button waits and says why.

- An answer that was stopped or failed can be tried again in its place: "Try again" under the
  last answer of your own chat writes it anew from the same question, and counts no other
  question against your plan. Stop also works now while a question is still being sent.
- A document whose indexing failed for a passing reason, such as the AI model being busy, says
  when Emudoc tries again: "Retrying at 14:32" in its status, or "Paused until 02:00" when the
  day's spending limit holds it, with an amber mark on its ring in the library. A ready
  document whose summaries or search by meaning wait says so under its status, and the library
  follows the retry when it runs. "Index again" starts it at once, as before.

Operators:

- Automations of a project's cross-checks, behind the flag `extraction_templates`: under the
  rules, an automation runs every cross-check, or those it names, with the discrepancy report
  when asked, once documents of a kind, or any, arrive in a folder or anywhere in the project,
  and with "Run them again" once the documents its checks compare them with arrive anywhere; it
  tells its maker, everyone who sees the project or nobody what the run found about what arrived,
  a new kind of notice, in the app and by email, counts and file names alone. A document arrives
  as its upload settles, and the run waits for the arrivals to go quiet: `AUTOMATION_WAIT`
  seconds after the last (120), at most a quarter of an hour after the first. Its report counts
  as one of the month's questions, and a month or day without room runs the checks without one,
  saying why. New tables `automations`, `automation_checks` and `automation_arrivals`, a run's
  `automation_id`, `trigger` and `told_at`, the job kind `run_automation` in the answerers' lane,
  the notice kind `checks_run` and the audit actions `automation_created`, `automation_changed`
  and `automation_deleted` (a migration); an automation's run is in the audit log by Emudoc,
  naming it. The privacy policy names the new notice.
- Runs of cross-checks and the discrepancy report, behind the flag `extraction_templates`:
  under a project's cross-checks, "Run the checks" works every rule out now and keeps what it
  found, the rules as they were and each value as it was read, so a run reads the same after its
  documents are read again or its rules change, and names the documents deleted since; the runs
  are listed newest first, each opening what each rule found. With "With a discrepancy report
  by the model" checked, or later from the run, the agent writes a report from the findings,
  citing the passages each value was read from as an answer does; asking for one counts as one
  of the month's questions and is held to the agent's runs in flight and the day's spending as a
  question is. New tables `workflow_runs` and `workflow_findings` and a usage kind `report` (a
  migration); the worker's `run_workflow` jobs, in the answerers' lane, work a run out and write
  its report, one run of a project at a time. New routes `POST|GET
  /api/projects/{id}/check-runs`, `GET|DELETE /api/check-runs/{id}`, `GET
  /api/check-runs/{id}/findings` (held to `RATE_SEARCHES`) and `POST
  /api/check-runs/{id}/report` (held to `RATE_QUESTIONS`), which API keys take; a run made, a
  report asked for and a run deleted are rows of the audit log (`cross_checks_run`,
  `check_report_requested`, `check_run_deleted`) naming the rules, never a value. A run is kept
  as long as a chat, which the privacy policy now says (the terms' edition is unchanged).
- What a template's reading changed, behind the same flag, no longer stops following a reading
  that ended just as it was asked for, saying that none was read again beside the document it
  had read.
- Cross-checks, behind the flag `extraction_templates`: where it is on, the library's
  "Cross-checks" lists a project's rules that compare a field of one kind's documents with a
  field of the same document, of the documents of another kind stating the same key, or of every
  document of another kind, value by value or line by line, with what each finds now: the values
  that differ, those missing what the comparison needs, and those that hold, each side opening
  its document where it is written. Five rules are suggested for the templates a project has
  (prices as on the price list, quantities no more than delivered, the total within its purchase
  order, payment terms as in the contract, due within 30 days of issue). A new table,
  `cross_checks` (a migration), at most 20 a project; what a rule finds is worked out from the
  facts whenever it is asked for, never by the model, and nothing of it is kept. New routes `GET
  /api/projects/{id}/cross-checks`, `POST` there, `PUT|DELETE /api/cross-checks/{id}` and `GET
  /api/cross-checks/{id}/findings` (held to `RATE_SEARCHES`), which API keys take; each rule
  made, changed or deleted is a row of the audit log naming the kinds and fields, never a value.
  The built-in invoice and purchase order templates now read each line's unit price and quantity
  as facts of their own, and a built-in price list template reads each item's price, from the
  next reading of a document on.
- Downloading the facts table, behind the flag `extraction_templates`: where it is on, the
  table's "Download" saves every row its sort, conditions, words and "To review" keep, as a CSV
  file of the table as it shows (a cell's values joined by "; ", the members' corrections in
  place, and columns naming the fields still to review and those whose quote was not found in
  the text), or as an Excel workbook of the same rows with each lone number, amount and date
  typed and a second sheet of every fact with its page, quote, whether the quote was found, who
  checked it and what the model read. New route `GET /api/projects/{id}/fact-table/export`
  (`format=csv|xlsx`, the table's query), which API keys take, held to the new `RATE_EXPORTS`
  (10 a minute per member); each download is a `facts_exported` row of the audit log (a
  migration) with the kind, the format and what was asked, never a value or the words. The
  model gives no confidence of its own for a fact: the files say whether its quote was found
  and whether a member checked it.
- Reading a template's documents again, behind the flag `extraction_templates`: once a template
  is saved or deleted, the library offers to read again the documents it concerns (those read
  for it, those of its kind read for none, and those read for the kind it was for before), not
  every document of the project, naming how many, and "What changed" then lists what each
  reading changed of the template's fields, those against a member's check first. A new table,
  `fact_changes` (a migration), one row a document, replaced at each reading that had one before
  it and dropped when its outline is read again. New routes `POST
  /api/projects/{id}/templates/read-again` and `GET /api/projects/{id}/fact-changes`, which API
  keys take; `TemplateOut` adds `matching`, `reading` and `changed`. Reading again writes no
  audit row, as "Index again" writes none.
- Checking and correcting facts, behind the flag `extraction_templates`: where it is on, a cell
  of the facts table opens its facts, each to check as read, correct to what the document states
  or open, and "To review" keeps the rows with a fact nobody checked. A new table,
  `fact_checks`, and `facts.check_id` (a migration): a check is kept by the document, the field
  and what the model read, so that "Index again" and a template read again give it back to a
  fact read the same. New routes `PUT /api/facts/{id}/check` and `DELETE
  /api/fact-checks/{id}`, which API keys with write access take; the fact table's `review` and
  the condition `field:unchecked`; `FactOut.check` and `FactRowOut.earlier`; the audit actions
  `fact_checked`, `fact_corrected` and `fact_check_removed`. `query_facts` gives the agent a
  member's value and says so, and a project's export holds each fact's check. The privacy policy
  says a member's checks are kept with their name.
- The facts table, behind the same flag `extraction_templates`: where it is on, each template
  with documents read for it opens a table of them from the library's "Extraction templates",
  a row a document and a column a field, sorted by any field and kept by conditions on them,
  each cell opening its fact where it was found. A new route, `GET
  /api/projects/{id}/fact-table` (`kind`, `sort`, `order`, `filter`, `q`, `after`, `limit`),
  which API keys take and `RATE_SEARCHES` holds as the other reads of the facts; no migration.
  At 5,000 invoices in a project, a page takes 30 to 120 ms (deploying.md, "The database").
- Extraction templates, the first of tier 3's features, behind the feature flag
  `extraction_templates` at 0, so off for every organisation until it is turned on for one in
  the operator's area or with `flags <slug> extraction_templates on`. A template is the fields a
  kind of document is read for: six built in (invoice, contract, purchase order, receipt, bank
  statement, delivery note) and up to twelve of a project's own, in a new table,
  `extraction_templates`, with `documents.template`, the kind a document's facts were read for
  (a migration). Where the flag is on, the enrich stage lists the project's templates in each
  document's first request, a few thousand tokens more a document, the library has an
  "Extraction templates" button, and the audit log records a template made, changed and
  deleted. A project's export lists its own templates, and each document's template (null
  without one), wherever the flag stands.
- Each worker says it runs, at its start and with every heartbeat, and says it stopped at its
  stop, in a new table, `workers` (a migration). `/api/status` adds `workers` (`running`,
  `error`): no worker seen for three minutes is "Background work off" in the members' header,
  and holds the chat's question; in development its sentence says to run `python -m app.worker`
  or leave `RUN_WORKER` unset. The `jobs` command and the operator's Jobs view list the workers
  seen in the last day, each with its release and whether it runs, stopped, or was lost, and say
  when none runs. Housekeeping forgets a worker a day after it was last seen.
- opentelemetry-api 1.45.1, past CVE-2026-114603, which Audit found in 1.45.0 (it comes with
  FastAPI, Anthropic's SDK and Pydantic AI; the app turns on no OpenTelemetry provider).
- A model call an answer was in the middle of when it was stopped, tried again or its run
  lost, is now counted in the answer's usage and the organisation's costs, for the tokens the
  provider reported by then; it was left out before, though the provider was paid for it.
- `POST /api/projects/{id}/reprocess` without `from_stage` now queues the project's partly
  indexed documents alone (ready, with enrich or embed failed or skipped and no retry waiting),
  each from the first stage it went without; with `from_stage` it runs every document from it,
  as before, when the default was `enrich`. `ProjectOut` adds `partly_indexed_count`, and
  `unenriched_count` now counts those of them without summaries and facts, failed as well as
  skipped. `list_documents` tells the agent which documents are partly indexed.
- `POST /api/chats/{id}/messages/{message_id}/retry` writes a stopped or failed answer again
  (`RetryOut`: the emptied answer and the `last_event_id` to follow its new try from). It is
  held to `MAX_ANSWERS`, the day's spending limits and `RATE_QUESTIONS`, not to the plan's
  questions. A migration adds `chat_messages.attempt`, the try an answer's job writes, so that a
  run of an earlier try writes nothing into a later one.
- A document's stages carry `retry_at` and `deferred` while a retry waits (`StageOut`), and the
  page asks about such a document when its retry is due, at most once a minute, rather than
  every 1.5 seconds.

## 2026-10-22

Members:

- The panes of a project resize without dragging: click the line between two of them and the
  buttons that show beside it narrow or widen the pane before it. Escape, or a click elsewhere,
  puts them away; dragging, the arrow keys and a double-click for the starting widths work as
  before.
- A folder can be uploaded whole, with the folders in it: drop it on the library or on a folder
  there, or choose it with "Upload a folder". Its folders are made as they were, and where a
  folder of the same name is there already its files go into that one. An upload now shows how
  far it has gone, as a whole and file by file under "Each file", and Stop ends it. A large
  upload goes in parts, one after another, so a folder of hundreds of files goes whole; files of
  a type Emudoc does not read and files too large are left out before anything is sent, and
  listed with why, and a folder's hidden files are left out too.
- Emudoc works by the keyboard alone and reads better aloud. A menu or panel takes the keyboard
  as it opens and gives it back to its button on Escape; Tab goes on past it. The library's
  files are one stop: a screen reader reads out each file as the arrows reach it, with its
  state, and the keyboard stays in the list after a rename or a deletion. A file or folder
  moves into another folder from its menu's "Move to…" as well as by dragging; the document
  tabs open with the arrow keys and close with Delete; the dividers between the panes move with
  the arrow keys. Faint grey text and hints are darker, and nothing fades, slides or pulses when
  your system asks for less motion.
- Deleting something for good from the trash, or emptying it, asks first in Emudoc's own
  dialog, which says what goes: a folder with the files deleted with it and anything deleted
  from it earlier, a project with its documents, folders, chats and trash, and what emptying
  the trash takes. Escape or Cancel keeps everything as it was.
- Emudoc says when your browser is offline, in the header, and waits for the connection rather
  than failing. An answer cut off goes on being written and shows in full once you are back; an
  upload begun offline starts by itself then, and one cut off on its way offers "Upload again".
  An answer whose connection stays lost offers "Reconnect" instead of a reload, and a request
  that reached no server says so in plain words.
- A part of the page that runs into an error no longer takes the whole page with it. The header
  stays, with the help menu, the notifications and the way to your other organisations and
  projects; in a project, the library, a document and a chat each break alone, the other two
  going on. The broken part offers to try again or to reload the page, and gives a reference to
  quote; opening another folder, document, chat or page starts it afresh.

Operators:

- The upload route takes `paths`, each file's folder below the one it goes into, and makes the
  folders where none of their names is (`folder_at` in `backend/app/folders.py`, under an
  advisory lock per project); a path it cannot make (".", "..", a name past 200 characters,
  past 32 folders deep) rejects its file alone, counted as `folder` in
  `emudoc_upload_rejections_total`. `GET /api/status` gives `uploads`, the request limits by
  which the page splits a large upload into several requests, each an upload with its own
  notice. The members' page's scripts grow to 228.5 kB gzipped, under a budget raised to 231.
- The browser test checks each kind of page, panel and dialog against axe-core's rules for
  WCAG 2.2 AA in both themes (`frontend/e2e/axe.ts`, `@axe-core/playwright`), and fails on any
  violation. The members' page's scripts grow to 225.6 kB gzipped, under a budget raised to 228.
- The operator's area asks before "Run again" and before a provider is switched off in the
  same dialog, rather than the browser's own.
- The page's error boundaries (`frontend/src/components/ErrorBoundary.tsx`): besides the one
  around everything, the shell's page, each of the workspace's panes and what each pane shows
  below its own navigation, and the operator's area's views, have one, each reporting to `POST
  /api/page-errors` as before. A report says the page's path, not which part broke; its frames
  say where.

## 2026-10-21

Members:

- What's new, from the question mark in the header: what changed in Emudoc, the newest first,
  a week at a time. The question mark carries a dot while there are changes you have not read
  since you last looked, counting from the day your account was made. The privacy policy says
  how far you read is kept; nobody needs to accept it again.
- Where the service has a status page, the help menu, the help pages and the screen that says
  Emudoc cannot reach its server link to it, to see whether Emudoc is down and what is being
  done about it.
- Help pages at `/help`, from the question mark in the header or under the sign-in pages: how
  to get started, upload and organise documents, search and ask, work with an organisation's
  members and roles, read the plans and their usage, sign in and keep an account safe, and
  export or delete what you hold, with a search over them. Their figures are the service's
  own: what each plan allows, the file types and sizes an upload takes, how long the trash
  keeps what was deleted.
- The question mark in the header writes to Emudoc's support from wherever you are: a problem, a
  question or an idea, in your own words. The message goes with your name and address, the
  page you wrote from and the reference of the last request that failed there, so support finds
  what you met; the answer comes to your address, and the menu gives you a reference to quote.
  The privacy policy says what a message holds and where it goes; nobody needs to accept it
  again.
- The audit log opens again for an organisation that exported a project: the export's row,
  "Exported a project", made the page fail. It also says when Emudoc's operators turned a
  feature being rolled out on or off for the organisation.
- A member who asks Emudoc's support for help lets it see their account in the organisation they
  work in, for a day or a week, under "Emudoc's support" on the account page, and withdraws that
  whenever they like. Support then sees what the member sees there, read only, an hour at a
  time; the member is mailed each time it opens their account, and the organisation's audit log
  records the leave, its withdrawal and each view. The terms of service and the privacy policy
  say so; nobody needs to accept them again.
- The audit log says when Emudoc's operators changed the organisation's plan, seats or agreed
  figures, or queued a failed document again: "by Emudoc's operators".
- A member exports a whole project from its card on the projects page: one ZIP file with every
  document as it was uploaded, in its folders, what was read from each (its outline, passages
  and facts) and the chats shared with the project and their own. It is made in the background,
  the member is told when it is ready, and it is kept 7 days for them alone. An organisation set
  to be deleted can still be exported, a project at a time.
- An owner deletes the organisation at the foot of its page, confirmed by the password, once its
  subscription no longer renews. It goes 14 days later with everything in it, and every member
  is mailed the day. Until then a banner says so on every page: nothing can be added to its
  projects or changed in them and nobody new joins it, while what it holds can still be read,
  asked about, downloaded and deleted, and any owner keeps it from the page or the banner, which
  every member is mailed too. A member it was the only organisation of is given one of their
  own.
- On a paid plan, an organisation's owners and admins make API keys on the organisation page,
  each with a name, read or read-and-write, and 30, 90 or 365 days or until revoked. A program
  calls the API with a key as the member who made it, in the organisation's projects alone and
  never beyond that member's role. The key is shown once; the page lists each key's first
  characters, whom it acts as and when it was last used, rolls one's own for a new secret and
  revokes any. Each is in the audit log, and so is what a key does, with the key named.
- An organisation's owners and admins choose how its members sign in, in the new Security
  section of the organisation page: requiring two-factor sign-in of everyone who works in it,
  once they sign in with it themselves, after the page names who does not yet; a member without
  it is asked to set up an authenticator app before working in that organisation again, and may
  switch to another of theirs, leave it or sign out instead. They can also sign everyone in
  again sooner than Emudoc does, every day or every week. The member list shows them who signs
  in without two-factor.
- The audit log has a page of its own, opened from the Security section: who did what and when,
  filtered by the kind of action or one action, a member, a project and a span of days, and
  downloaded as a CSV file for a spreadsheet. The download is in the log too.
- An organisation's owners and admins choose its settings on the organisation page: the
  language its documents are searched in, which the documents indexed before move to within
  minutes and which answers fall back to when a question does not show a language; keeping
  chats for less time than the plan does, the page saying which chats that deletes before it is
  saved; and how its members are told each kind of notice until they choose for themselves on
  their account page. Each change is in the organisation's audit log.
- Moving or renaming a file or a folder in the library can be undone at once, like a deletion:
  the notice under the library's toolbar says where the items went or what they are called now,
  and "Undo" puts each back in the folder it came from, or gives it the name it had.
- Deleting no longer loses anything at once: a file or a folder goes into its project's trash,
  which the library's trash button opens, and the library offers to undo the deletion right
  away; a chat goes into your own trash, under "Deleted chats" in the chat list; a project goes,
  with everything in it, into the trash under the projects. Restore it as it was, or delete it
  for good; what is still there after 30 days is deleted for good. Nothing in the trash is
  searched, read by the agent or counted against your plan, so deleting still makes room at
  once, and restoring needs the room again. Uploading a file that is in the trash says so,
  rather than indexing it again.
- Once an answer about your organisation's own documents is done, an owner or admin of an
  organisation nobody else is in or invited to finds a suggestion under it to invite the team,
  with the email and the role to send the invitation from the chat, or, on a plan for one
  person, the way to a plan for more. It goes once someone is invited, and for good with its
  cross; the sample's answers make none.
- The projects page lists your organisation's first steps, uploading your own documents, asking
  about them and inviting your team, each with a button that takes you where it is done, and
  ticks each as the organisation takes it, whoever in it does; hide the list with its cross, in
  every organisation of yours at once. The first project you open shows a short tour of the
  workspace, card by card: the library, uploading, search, the chat, your organisations, the
  notices and your account. Skip it, or press Escape; the user menu offers it again inside a
  project. An empty library offers to upload files, an empty projects page to create a project,
  and a search of a folder that finds nothing to search the whole project.
- A new account's organisation starts with a sample project, "Sample: Acme supplier contract": a
  supplier's contract, three of its invoices, the price list, the delivery log, an email and the
  notes of a review meeting, indexed already, so the first question is answered with citations
  at once. An empty chat in it offers three questions that check the documents against each
  other, such as whether an invoice applies the contract's volume discount. It counts against
  nothing on your plan (the usage page says so) and takes no uploads; your questions in it count
  as any do, and you delete it like any project. A colleague who joins from an invitation finds
  the sample of the organisation they join.

Operators:

- The image carries `CHANGELOG.md`, whose **Members** entries the app serves as what's new
  (`GET /api/changelog`, public; deploying.md, "What's new"); without it the page has nothing to
  tell and the log says why. A migration keeps how far each member read it.
- `STATUS_PAGE_URL` names the deployment's status page, run outside it, which the help menu,
  the help pages and the page that cannot reach its server link to; deploying.md ("The status
  page") says how the uptime service, the person on call and the alerts fill it.
- The help pages (`backend/app/help/`, Markdown filled in as `GET /api/help` serves them) take
  their figures from the code and the settings: the plans, `MAX_UPLOAD_MB` and the other upload
  ceilings, `OCR_MAX_PAGES`, `SESSION_DAYS`, the rates; with `SCANNER_URL`, Stripe or the breach
  check off, they say nothing of them.
- Members' messages to support from the app's help menu are mailed to `SUPPORT_EMAIL`, unset
  `OPERATOR_EMAIL`, with replies going to the member, at most `RATE_FEEDBACK` (10) an hour each;
  the mail names the account, the organisation (linked in the operator's area), the page, the
  version, the browser and the reference of the last request that failed there, which the log
  is searched by (deploying.md, "Support"). The mail about a changed address now names the
  support address for help.
- Features being rolled out are behind feature flags (`backend/app/flags.py`, none yet): each is
  on for the share of organisations the code names and for those an operator turns it on for.
  The organisation's page in the operator's area turns each on or off for it, or back to the
  rollout, a new Flags view lists every flag with how many organisations it is on for and those
  chosen by hand, and `maintenance flags [<slug> [<flag> on|off|rollout]]` does the same from a
  shell; each choice is a row of the organisation's audit log. Migration 4cf254f44ec1 adds
  `organizations.flags`.
- In the operator's area, an organisation's Members table offers "View as <member>" for each
  member who lets support see their account: the operator's own session then shows the app as
  that member sees it in that organisation, read only, under a banner, until the operator ends it
  or an hour passes, the member withdraws the leave or the session leaves the operators'
  networks. Every log line of a view carries `operator_id`; migration f6d05e27c762 adds
  `support_grants` and two columns of `sessions`.
- eMudhra's compromised intermediates (October 2026): the image leaves `emSign ECC Root CA - G3`
  out of Debian's bundle, which `SSL_CERT_FILE` names for the mail relay and every other TLS
  connection Python makes, as certifi 2026.10.19 does for the HTTP clients; Go's fix of the same
  (Go 1.26.10) is not yet in Caddy 2.11.7, Prometheus 3.15.0 or Alertmanager 0.34.1, and is
  accepted until 20 November in `deploy/.trivyignore.yaml`, each with where its binary verifies
  a chain. Pillow is 13.0.0, which closes six advisories about malformed images.
- The operator's area acts: under an organisation's Plan, "Change the plan" moves it to another
  plan with its seats and agreed figures as the `plan` command does, each figure written as the
  table words it; under Jobs, "Run again" queues a failed index job's document from the stage it
  stopped at, or every one, as `failed-jobs --requeue` does; and under Services a model provider
  is switched off for every process within five seconds, with no restart, and on again. The
  first two are rows of the organisation's audit log, read there as "by Emudoc's operators"
  with the operator's id kept in the row; a switch is a log line naming the operator.
  `DISABLED_PROVIDERS` stays the deployment's own switch, which the page cannot switch back on.
  The migration adds `provider_switches` ("The operator's area" in deploying.md).
- The operator's area at `/operator`: the organisations with their plans, usage, members and
  costs, the users, the costs across organisations, the job queue with its dead letters, and the
  models and services as the worker's watch sees them, each what a maintenance command prints. It
  reads; plan changes, requeues and the kill switch stay the commands'. `python -m app.maintenance
  operators add <address>` makes an account an operator (the migration adds `operators`), who
  opens the area from an address `OPERATOR_NETWORKS` lists (unset: this machine in development,
  nobody in production) and, in production, with two-factor sign-in on; anyone else gets 404.
  `pnpm build` now builds the page as a second bundle beside the members', which it leaves as it
  was ("The operator's area" in deploying.md).
- Project exports: the migration adds `project_exports`, the job kind `export_project` (run by
  the indexers), the notice kind `project_export` and the audit log's action `project_exported`.
  The worker writes each export whole into its temporary directory before it puts it under
  `orgs/<org>/projects/<project>/exports/<id>.zip`, so `TMPDIR` needs room for the largest
  project's originals; the housekeeping forgets an export 7 days after it was ready and its walk
  removes the file by its age. A failed export is a dead letter, mailed with the others.
- Deleting an organisation: the migration adds `organizations.deletion_due_at` and the audit
  log's actions `organization_deletion_requested` and `organization_deletion_cancelled`. The
  housekeeping round deletes a due organisation after the due accounts ("Deleted organisation
  …"), and leaves its record under `erasures/organizations/`, which a bucket restore keeps as it
  keeps `erasures/users/`, and which `after-restore` reads to delete again one a restore brings
  back. One whose subscription was renewed at Stripe after the request is kept and logged at
  ERROR every round until it is cancelled there.
- API keys: a request with `Authorization: Bearer emk_…` acts as the key's member, needs no
  `X-Requested-With`, and is held to `RATE_API_REQUESTS` (300 a minute) per key on top of its
  member's rates; the proxy passes the header as it is. The migration reshapes the unused
  `api_keys` table of the first schema (keys now go with their member's membership) and adds the
  audit log's actions `api_key_created`, `api_key_rolled` and `api_key_revoked`; the log's lines
  of a request made with a key carry `api_key_id`. Keys that ran out 30 days ago are pruned with
  the sessions.
- The migration adds `mfa_required` and `session_days` to `organizations`, the audit log's
  action `audit_exported`, and four indexes of `audit_events` led by the organisation and its
  action, its project, and time, and by the member, in place of the two by id and by user; on a
  large log it takes a write lock on the table while they are built. A session older than an
  organisation's `session_days` ends at its next request there, a 401 like an expired one's.
  The privacy policy says organisations may require two-factor and sign their members in again
  sooner, and that their owners and admins download the audit log; no new edition is asked.
- A new kind of job, `apply_language`, moves an organisation's passages to the language its
  owners or admins chose, 500 a transaction at the priority of bulk work, asking no model;
  `FTS_LANGUAGE` is now the language of an organisation that chose none. The audit log has a
  new action, `settings_changed`. The migration adds three columns to `organizations`.
- Projects, folders, documents and chats deleted wait in the trash, rows and files, until a
  member restores them or deletes them for good; the housekeeping round deletes for good what
  has been there 30 days (`TRASH_DAYS`, logged with the counts). `emudoc_documents` counts their
  documents as `trashed`, and their originals stay in `emudoc_stored_bytes` until then. The
  audit log records `*_restored` and `*_purged` beside `*_deleted`, which now means into the
  trash; the migration drops the job kind `purge_trash`, which nothing queued. The privacy
  policy names the trash's 30 days among what is kept.
- `SAMPLE_PROJECT` (on by default) gives the organisation an account signs up with the sample
  project, copied from the image in the sign-up's transaction: no model is called and nothing
  of the month is used; each document's index job makes its vectors alone, recorded as `embed`
  usage, and the audit log records `sample_added`. A start refuses an image whose sample files
  are not those its index describes, and a storage that refuses the files leaves the account
  without the sample, logged. `python -m app.maintenance build-sample` indexes the files again
  with the configured models (deploying.md, "The sample project").
- A migration keeps, per account, when its first steps were hidden and its tour seen, and takes
  both as done for the accounts from before it, so that only new ones are shown them. A second
  keeps when the suggestion to invite the team was hidden, likewise hidden for the accounts
  from before it.

## 2026-10-20

Members:

- A bell in the header holds your notices, of every organisation you belong to, the newest
  first, with how many you have not read; opening one marks it read and takes you to its page,
  switching to its organisation first when you work in another, and "Mark all as read" clears
  the count. The account page has a Notices section of its own: each kind of notice (a long
  upload indexed, the month's pages or questions running low) is told in Emudoc, by email, both
  or neither, as you choose, and a browser can show them as they come, as your computer's own
  notifications while Emudoc is open in a tab. The link at the foot of a notice now stops that
  kind by email rather than every notice. A link in a notice or in an owner's billing email
  opens its page in the organisation it is about. Notices are kept 90 days, and those about an
  organisation go when you leave it; the privacy policy and the data export say so.

- Emudoc now sends the notices the account page's switch is for. An upload that takes five
  minutes or more to index is followed by one email once its last document is done, saying how
  many are ready and naming each that could not be indexed with the reason the library shows;
  one indexed sooner sends none, since the library said as much while you watched. The owners
  and admins of an organisation are mailed once a month when its pages or questions for the
  month reach 80 % of what it may use, when they are all used, and when the pages left are too
  few for a document, with a link to the usage page; a pack bought afterwards starts the count
  again. The privacy policy names both.

- The emails Emudoc sends come with an HTML part in Emudoc's colours beside the plain text, the
  link a button with its address under it to copy. A link asked for while the mail server is
  down arrives once it is back, tried again for half an hour, instead of not at all. The account
  page's Email address section has a switch for notices, the mail about your work that you may
  do without (none is sent yet); each notice will carry a link that stops them, which a mail
  program's own "Unsubscribe" uses too, and mail about your account, invitations and billing is
  sent either way. The privacy policy and the data export say whether you want notices, and
  whether the mail provider said your address bounces.

- An owner changes or cancels the subscription from the organisation page: choosing another
  plan, other seats or Free shows first what it would mean (the figures that change, what the
  organisation would hold past the plan and so be read-only, which chats and audit log entries
  the lower plan keeps for less time and until when, and for a cancellation the day it ends),
  then Stripe's page shows the price and confirms it, and back on the page the plan shows as it
  stands. A move to a plan that keeps chats or audit log entries for less time now deletes none
  at once: those older are kept as before for 30 days after the move, which the plan's "Chats
  kept" says. The owners are mailed a cancellation and the lower plan once it comes, with what
  it leaves read-only and until when the older chats are kept; the terms and the privacy policy
  say so, and the audit log records a cancellation and its withdrawal.

- A Word file, a spreadsheet or a text file now counts against the month's pages like a PDF:
  a page for every 3,000 characters of its text, counted once, when it is first indexed. The
  usage page says so beside the pages indexed, a document's usage names them as pages of text,
  and a month without room for one refuses it with "about N pages of text". Documents indexed
  before count nothing more when indexed again.

- Every member finds the organisation's usage under "See what it holds and has used" on the
  organisation page: its projects, documents, storage and members, and this month's pages and
  questions, each against what the plan allows (the packs bought for the month counted), with
  a warning and the way past the limit from 80 % of a month's pages or questions on, and the
  pages indexed and questions asked on each of the last 30 days and in each of the last 12
  months, in UTC. A question now counts against the month as it is asked: deleting its chat no
  longer gives it back.

- A limit of the plan reached now offers the way past it where it was reached: the owner of an
  organisation whose month of pages or questions ran out buys a pack for the month there and
  then, is shown the plans on the organisation page, or, with a subscription, Manage billing to
  change the plan or add seats; anyone else is told to ask an owner, and an organisation whose
  terms were agreed, or a deployment that sells nothing, to ask the Emudoc team. An organisation
  that holds more than its plan allows, once its plan or seats were lowered (a subscription that
  lapsed to Free, seats taken off), keeps everything and is read-only until it is within the
  plan again: what it holds is read, searched, asked about and deleted, its members removed and
  invitations revoked, but no project, document, folder, rename, move, re-index, access change,
  invitation or joining goes through. Every page says so in a banner, with the way to the plan,
  and the organisation page lists what it holds past it; the banner goes once a deletion or a
  purchase brings it within. The terms say so.

- Where the deployment sells its plans, an owner buys Pro, or Team with the seats it needs, on
  the organisation page, which sends them to Stripe to pay and back; an organisation's first
  subscription starts with a trial. Behind "Manage billing" an owner changes the plan or the
  seats, the card, the billing address and VAT id, cancels, and finds the invoices, which carry
  the VAT id. The page says how the subscription stands: its trial, when it renews or ends, and
  a payment that failed, with the day until which the plan stays. The owners are written to when
  a payment fails, before a trial ends and when the organisation moved to the Free plan for want
  of a payment; it keeps everything it holds there, and has its plan again once it pays. Where
  packs are sold, an owner also buys pages or questions for the month on top of the plan's,
  paid once with an invoice, and the Plan section shows what was bought for the month. The
  terms say what a paid plan is, and the privacy policy what billing keeps, where plans are
  sold. An account cannot be set to be deleted while an organisation that would go with it pays
  for a subscription that still renews: it is cancelled first.

- The organisation page shows the plan and what it lets the organisation keep and use:
  projects, documents, storage, pages indexed and questions a month, members and how long
  chats are kept, with the seats bought on the Team plan as its members and a figure agreed
  with the organisation marked "Agreed". The terms and the privacy policy say that the seats
  bought, or terms agreed with an organisation, take the place of the plan's figures. A limit
  reached on the Enterprise plan asks for a higher limit rather than an upgrade.

- A chat can be shared with the project from its header ("Share", and "Make private" to take
  it back). Everyone who sees the project finds the chats others shared under "Shared with the
  project" in the chat list, and a new chat offers the latest of them, each with the name of
  the member who started it and the day of its last question. They read a shared chat and
  follow its answer as it is written, but only its starter asks in it, renames it or deletes
  it, and the page no longer offers the others the composer, rename or delete that the server
  refused. A project opens on one's own latest chat, never on one another member shared.

- A project can be made private, from "Who sees it" on its card or when making it: then only
  the members chosen for it see it, besides the organisation's owners and admins, who see every
  project, and anyone else finds no trace of it. Whoever may change the project chooses; a
  member stays among the members of a project they make private, and a viewer may be chosen,
  to read it. Opening it to everyone again forgets the choice, and leaving the organisation or
  being removed from it takes one's place in its private projects. Chats in a project one no
  longer sees are hidden until one is chosen again. The privacy policy says who sees a private
  project, and the copy of one's data names the private projects one was chosen for.

- The owners and admins of an organisation change the other members' roles and remove members
  from its page: an admin anyone's but an owner's, and to any role but owner, an owner
  anyone's, so an owner can make another without handing the organisation over. Nobody changes
  their own role there. A member removed loses their private chats in its projects, as on
  leaving, and one removed from their last organisation gets an empty one of their own. A
  viewer is no longer offered what they may not do: no new project, upload, folder, rename,
  move, delete or "Index again". A page whose request is refused because the account's role or
  standing changed meanwhile catches up at once. The privacy policy says that being removed
  takes the chats you did not share, as leaving does.

- The owners and admins of an organisation invite people by email, as an owner, admin, member
  or viewer, but never above their own role. The link mailed lets whoever holds the address
  join: signed in with it, or with an account made on the link's page or through Google or
  Microsoft from it, which then has no organisation of its own. The organisation's page lists
  the invitations, sends one again with a new link, which stops the one before, and revokes
  one; an invitation lasts a week and takes a place among the members the plan allows until
  then. An organisation can also let everyone whose confirmed address is at its domain join,
  as a member or a viewer: an admin with an address there adds the domain, and Emudoc offers
  the organisation to everyone at it, who joins only if they choose. The privacy policy says
  how long an invited address is kept.

- You can belong to several organisations and switch between them from the organisation's
  name in the header. Every tab of the browser follows a switch, and a tab still showing the
  organisation before starts again in the new one rather than acting in the old. "New
  organisation" starts one of your own, empty and on the Free plan; you may own two on it. The
  organisation's page renames it (owners and admins), lists its members, hands it over to
  another member, which takes your password and leaves you an admin, and leaves it, taking
  your private chats in its projects with you; the chats you shared stay. The privacy policy
  says that members see each other's names and addresses, and that leaving takes the chats you
  did not share.

- Your authenticator app's secret is kept encrypted, with a key the database does not hold, so
  a copy of the database cannot make your two-factor codes. If Emudoc ever cannot check your
  app's codes, it says so and asks for one of your recovery codes instead.

- Signing up starts with your email address alone: Emudoc mails it a link, and on the page the
  link opens you choose your name and password, accept the terms, and your account is made and
  signed in, its address confirmed. The form answers the same for an address that already has
  an account, which is told by mail how to sign in instead, so nobody learns from Emudoc who
  uses it; and nobody can choose the password of an account for an address that is not theirs.

- After 20 wrong passwords or codes in an hour, signing in to an account from browsers that
  have never signed in to it pauses until the hour ends, and its owner is told by mail. The
  browsers you have signed in with carry on as before, so someone guessing your password
  cannot keep you out; resetting the password ends the pause. A wrong password on the account
  page counts too.
- A new password that has appeared in a data breach is refused, at sign-up, from a reset link
  and on the account page, since it is among the first anyone guessing would try. A reset link
  stays good after such a refusal.

- The account page changes your name and your time zone, and moves your account to a new
  email address through a link sent there; once it is opened, the old address is told and
  signs in no more. The dates in the mail Emudoc sends you are in your time zone, which a new
  account takes from the browser.
- Opening the link that confirms your email address says it worked. It did before as well,
  but the page said "This link no longer works".

- Emudoc opens sooner on every visit after the first: the browser keeps the app's scripts
  until a release changes them, and asks only whether the page itself changed.
- In a large project, search finds more of the passages closest in meaning to what you typed:
  in one of 30,000 documents, 93 % of the ten closest where it found 83 %. It also stays as
  quick when many other projects share the deployment, where a large project's searches could
  slow to a third of their speed.

Operators:

- The notification centre (`backend/app/api/notifications.py`, deploying.md "Mail"). A
  migration adds `notifications` (one row per notice told in the app: the member, the
  organisation, the kind, its words and its page, and when it was read) and replaces
  `users.notices_by_email` with `users.notices`, the ways each kind is told as the member chose
  them (one who had notices off by email has every kind's email off). The housekeeping round
  removes the notices older than 90 days (`NOTIFICATION_DAYS`). An unsubscribe link names its
  kind now; one mailed before turns every kind's email off. The page's scripts' budget is 205 kB
  (`scripts/size.ts`), the bell having taken them to 202.4 kB.

- The notices are sent (`backend/app/notices.py`, deploying.md "Mail"). A migration adds
  `uploads` (one row per upload request, with the member who sent it and when its last
  document settled, named by `documents.upload_id`) and `quota_notices` (one row per
  organisation, month, figure, share and allowance told). The worker looks at an upload once a
  document's index job has ended, through a new `after` step of a job's handler; the
  housekeeping round settles those whose last look was lost and forgets the records of months
  gone by. A notice the worker writes links to `APP_URL` (in development without it, the
  frontend's development server). Payment receipts and invoices are Stripe's to mail: turn on
  "Successful payments" and "Finalized invoices" under its Customer emails (deploying.md,
  "Billing").

- The worker sends the mail now, in a lane of one thread of its own (the pool counts it: 16 +
  8 + 1 + 4 of 30 at the defaults, one more with `MAIL_EVENTS_QUEUE`): a request posts a
  `send_email` job, its message encrypted with `ENCRYPTION_KEYS` and taken out once sent, and a
  relay that says "later" gets it again after 2, 4, 8 and 16 minutes, then the job fails like
  any given up. The mailed links reach the worker's log in development and in the load test,
  not the API's. A migration lets a job be no organisation's, adds `mail_suppressions` and
  `users.notices_by_email`. `RESEND_API_KEY` sends through Resend's API instead of `SMTP_URL`
  (production takes one of the two), with each message's key as its `Idempotency-Key`. What
  bounced for good or was complained of reaches the app from Resend's webhook,
  `/api/mail/events` signed with `RESEND_WEBHOOK_SECRET`, or from SES through EventBridge and
  the SQS queue `MAIL_EVENTS_QUEUE` the worker reads every minute, and the address is sent no
  notices for a year (deploying.md, "Mail"). On AWS the `mail` domain gains its envelope domain
  `bounce.<domain>` (MX and SPF), a `_dmarc` record (`mail.dmarc_policy`, `quarantine` by
  default, `""` for none; `mail.dmarc_reports`), a configuration set that sends over TLS alone
  and publishes bounces and complaints, the EventBridge rule, the queue and the app role's
  right to read it: `tofu apply`, then add the records `dns_records` lists where the DNS is not
  Route 53. New metrics `emudoc_mail_sent_total` and `emudoc_mail_events_total`, a `mailer` lane,
  and the rules `EmudocMailWaiting` (critical), `EmudocMailRefused` and `EmudocMailBounces`.

- The organisation page opens Stripe's customer portal on one step, a cancellation
  (`subscription_cancel`) or a confirmed change of plan (`subscription_update_confirm`): the
  portal needs cancelling at the end of the period and every price `STRIPE_PRICES` sells first
  among its products to update to (deploying.md, "Billing", step 3). Portal sessions now return
  to `/organization?billing=back`, which asks Stripe for the subscription. A migration adds
  `organizations.retention_held_until`, `held_chat_days` and `held_audit_days`, which the
  housekeeper's chat and audit pruning read, and the audit actions `subscription_cancelled` and
  `cancellation_withdrawn`; the `plan` command prints a hold that stands.

- The `upload` usage events in `pages`, which the plans' months, the usage page and the `usage`
  command read, now hold the pages of text of a document without pages (`CHARS_PER_PAGE`,
  3,000 characters a page). A document's pages are counted once, as its usage shows them, so a
  parse redone after a lost lease no longer counts a PDF twice; the migration marks the
  documents parsed before as counted for 0 pages (`usage.upload.pages`, which no page shows).

- Each question asked is a usage event of its own, `answer` in `questions` (one a question, no
  reference, the asker and the project), which the plan's month counts in place of the month's
  answers still in the chats; the release's migration writes one for every answer the chats
  hold, at the time it was asked, so the month counts what it did. `maintenance usage <slug>`
  prints them, and `GET /api/organization/usage` serves the usage page.

- A refusal by the plan is a 402 whose body names the figure it is held to beside the sentence,
  `{"detail": …, "limit": "pages_per_month"}`, and a file the plan rejected in an upload carries
  the same `limit`. An organisation past its plan is refused every change of a project with 402
  (deletions aside) and every invitation and join; `maintenance plan <slug>` says when an
  organisation is read-only, and lowering a plan or seats under what an organisation holds
  makes it so until it deletes, parts with members or buys.

- Billing through Stripe (deploying.md, "Billing"): `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`
  (a webhook endpoint at `{APP_URL}/api/billing/events`, API version `2026-09-30.endive`) and
  `STRIPE_PRICES` (`pro=price_…,team=price_…`) turn it on, `STRIPE_PACKS`
  (`pages:1000=price_…`, one-time prices, for the month they are paid in), `STRIPE_TRIAL_DAYS` (14),
  `STRIPE_TAX` and `BILLING_GRACE_DAYS` (14) shape it, and `RATE_BILLING` (10 a minute) holds
  each member's requests; production refuses one of the first three without the others, a
  publishable key or a secret that is not a signing secret. Stripe then joins the subprocessor
  list, and the terms and privacy policy their paid plans' paragraph and billing records. The
  subscription sets the plan, read back from Stripe at each event; an Enterprise organisation or
  one with agreed figures keeps its plan, and the `plan` command says when an organisation pays
  for a subscription. The housekeeper moves to Free the organisations whose payment has failed
  past the grace period and forgets Stripe's events after a month. The release's migration adds
  the organisations' Stripe customer and subscription with its status and dates, the
  `billing_events` table, and gives the audit log `checkout_started`, `subscription_changed`
  and `payment_failed`; a second adds `allowance_packs` and `pack_bought`, and the webhook
  takes `checkout.session.async_payment_succeeded` too. The `plan` command names the month's
  packs.
- `maintenance plan <slug>` sells the Team plan by the seat and agrees figures with an
  organisation in place of its plan's: `--seats N` (`none` for the plan's own, three on Team),
  `--set NAME=VALUE` as the report prints it (`projects=500`, `storage_bytes=1 TB`,
  `daily_spend_micros=25.00`, `retention_days=none` for no limit) and `--unset NAME`; moving it
  to another plan clears both, and the report says so, marks each agreed figure and names what
  the organisation holds past what it allows, which it keeps. The release's migration adds
  `organizations.seats` and `organizations.overrides`, gives the Team organisations from before
  25 seats, what the plan allowed them, and gives the audit log `seats_changed` and
  `entitlements_changed`. The housekeeper prunes chats and audit rows by agreed days, and the
  job claim weighs an agreed `priority`.
- The release's migration gives `projects` a `visibility` (every project stays open to its
  organisation), adds `project_members`, the members chosen for a private project, and gives
  the audit log the action `project_access_changed`, whose detail names the members let in and
  left out by id. A restore to an earlier moment brings back who saw a private project then,
  which the runbook now tells the owners and admins to check.
- The release's migration gives the audit log the action `member_removed`; a role changed by
  an owner or admin is recorded as `role_changed`, as a successor's promotion was. Changing a
  role, removing a member, handing over and leaving each hold the organisation's row until
  they commit, as an invitation does.
- Invitations are mailed through the relay like every link, after the answer, held to
  `RATE_SIGN_INS` per inviter and per address mailed. The release's migration drops
  `invitations.accepted_at` (an invitation accepted or revoked is deleted, one that expired
  pruned with the one-time links), gives `organizations` the domains and role of joining by
  domain, and the audit log five actions (`invitation_sent`, `invitation_revoked`,
  `invitation_accepted`, `joining_changed`, `member_joined`). `after-restore` now also expires
  every open invitation, since one revoked or accepted after the restore point would work
  again, and says how many.
- `MAX_FREE_ORGANIZATIONS` (2) is how many organisations on the Free plan one user may own;
  0 removes the ceiling. The release's migration gives each session the organisation it works
  in and the audit log four actions (`organization_created`, `organization_renamed`,
  `ownership_transferred`, `member_left`). A sign-out is now recorded in the organisation its
  session worked in, a sign-in still in the one its user joined first.
- `ENCRYPTION_KEYS` is required in production: the keys the authenticators' secrets of
  two-factor sign-in are encrypted with, made with `openssl rand -base64 32` and kept apart
  from the database's backups (on AWS under the secrets path). After the release, run `python
  -m app.maintenance encrypt-secrets` once to encrypt the secrets kept from before; the start
  warns while any are left. A key is rotated by putting a new one first and running the same
  command, keeping the old one for `BACKUP_DAYS` (deploying.md, "The encryption keys"); the
  restore drill now fails when a restored secret's key is gone.

- A new account with a password is made from the link its sign-up mails, so while the mail
  relay is down only Google and Microsoft sign-ups get through; the form answers as usual and
  the API's log says why the mail failed. A sign-up no longer leaves an account behind when its
  mail fails. The link is a one-time link of a new purpose, `sign_up`, which belongs to no user
  until it is opened (a migration lets `auth_tokens.user_id` be null for it alone).
- `RATE_FAILED_SIGN_INS` (20 an hour) pauses the sign-ins of an account from browsers that
  never signed in to it once its wrong passwords and codes reach it, and of a browser that did
  once its own do; such a browser keeps a cookie, `emudoc_browser` (path `/api/auth`, 90 days),
  is counted by itself on `RATE_SIGN_INS` too, and has a row in the new `browsers` table. The
  audit log gains `sign_in_paused`. Changing the password and turning two-factor sign-in off
  are now held to `RATE_SIGN_INS` per user, as the address change and the deletion were. A
  migration adds the table and the action.
- New passwords are checked against Have I Been Pwned's Pwned Passwords
  (`PWNED_PASSWORDS_URL`, on by default; empty turns it off), which is sent the first five
  characters of the password's SHA-1 and appears on `/subprocessors` and in the privacy
  policy; a copy of its list on your own network sends nothing out, and production refuses
  one reached over plain HTTP off it. A check that fails lets the password through and logs
  why. The privacy policy names the new cookie, the browsers kept and for how long the abuse
  counts are kept (7 hours at most, where it said an hour).
- A failed sign-in for an address without an account now takes as long as one with a wrong
  password, so the time of the answer says nothing of which it was.
- A migration adds `users.time_zone`, a `change_email` link and three audit actions
  (`profile_changed`, `email_change_requested`, `email_changed`), recorded without the name
  or address they changed to. The backend now depends on `tzdata`, since the image's own zone
  database lacks names browsers still report, such as `Asia/Calcutta`. The mail to an address
  that was replaced names `OPERATOR_EMAIL` as the address to write to. Nothing to set.
- The page's scripts and stylesheet (`/assets/`, named by a hash of what they hold) are sent
  with `Cache-Control: public, max-age=31536000, immutable`, and the page, `theme.js` and
  `favicon.svg` with `no-cache`, an unchanged one answered with a 304; the API's answers stay
  `no-store`. The app compresses nothing, so the proxy should: the AWS Caddyfile now gzips
  what is outside `/api` (the 605 kB script goes out at 192 kB), and a proxy of your own needs
  the same lines, `encode` for Caddy or `gzip` for nginx, as deploying.md's examples ("TLS at a
  reverse proxy") give them, the API left as it is.
- CI's Frontend job fails when the built scripts pass 200 kB gzipped (`pnpm size`,
  `frontend/scripts/size.ts`, whose `BUDGET` says how to raise it); they are 184 kB today.
- Every search plans itself for its project (`plan_cache_mode = force_custom_plan`, `SET
  LOCAL`): psycopg prepares a statement on its fifth run on a connection, and Postgres may then
  keep one plan for every project, which in a database of many projects sorts a large
  project's passages by distance, 470 to 540 ms for 75,000 where the search took 150 to 250.
  The vector index is searched at `hnsw.ef_search` 200 where pgvector's default is 40: in a
  project of 93,000 passages its candidates held 93 % of the 10 nearest where they held 83 %,
  for about 5 ms more (deploying.md, "The database"). Nothing to set.
- The load test's search phase ranks 100 searches' passages by the vector index at
  `hnsw.ef_search` 40, 100, 200 and 400, and through the index whatever the planner would
  choose, against the nearest by exact distance, then searches a project of `--small-documents`
  (30) beside the library likewise; `--set EMBEDDING_BASE_URL=http://127.0.0.1:11434/v1 --set
  EMBEDDING_MODEL=nomic-embed-text` measures a real model's vectors. deploying.md's "Load" has
  the figures at 30,000 documents: a search 199 ms, `ilike` over the facts and the documents'
  names about 95 ms, which needs no trigram index yet.

## 2026-10-19

Members:

- A long scan is ready far sooner: four of its pages are read at once where they were read one
  after another, so a scan of 100 pages, the most one document may have read by default, is
  ready in about two minutes at a hosted model's pace, where it took about six.
- The pages of a long PDF show sooner as you scroll where the files are kept in cloud storage:
  the file is fetched once for the pages that follow rather than once a page, so a page of a
  62 MB scan is drawn in under a second where it took two.
- A large upload is ready far sooner: sixteen documents are indexed at once where two were, so
  3,000 documents uploaded together are ready to search and ask about in about ten minutes at a
  hosted model's pace, where they took over an hour.
- A large spreadsheet is ready far sooner: the AI model reads a long table by its header, its
  first rows and its last ones, so a sheet of 90,000 rows is one request where it was hundreds.
  Such a table gives no facts, since a few rows of thousands would answer a count wrong, and
  every row is searched and read in answers as before.
- A file with more than 20 million characters of text is refused at once with a sentence that
  says to split it, and a document too long for the model to read whole is summarised from its
  start, its indexing saying which pages the summaries and facts cover.
- When many members read answers at once, the answers no longer all wait five seconds before
  they begin, and an answer's text no longer stops and picks up again.
- A PDF's first pages show at once when you open it, and the pages you scroll to, or a
  citation opens, are drawn several at a time, so one long scan no longer holds up everybody's
  pages.
- A document whose indexing failed, and an answer that failed, say what it means for you ("The
  AI model is unavailable just now.", a document "found by its words alone" until semantic
  search takes it) and how it goes on, and no longer name the model, a service's address or
  the settings to change.
- Where the deployment keeps records of connections, the privacy policy says what they hold
  (your IP address, and for the page images and downloads your browser fetches from the file
  storage, your browser and a download's file name) and how long they are kept.
- The privacy policy says how long the server logs, with your requests' IP addresses, are
  kept.
- The library shows a folder's files a hundred at a time, with "Show more files" after them,
  so a project of thousands of documents opens as quickly as one of ten. Files are sorted with
  the numbers in their names compared as numbers ("invoice 2" before "invoice 10"), as before.

Operators:

- The new `OCR_CONCURRENCY` (4) is how many pages of one scan are read at once, each drawn and
  then sent to the model as an earlier one is answered: the load test's 300-page scan at 3 s a
  request was ready after 317 s, where one page at a time took 1,068 s, the worker at 120 MB
  more. Each indexer reading a scan sends that many requests at once, which the provider's rate
  limit counts; lower it when `EmudocModelRateLimited` fires while scans are read (deploying.md,
  "Large documents"). The load test's `--large-index-seconds` gives the large files a model's
  pace.
- From a bucket, the API keeps its copy of an original for the pages and thumbnails it draws
  next: five minutes after the last page drawn from it, up to the new `RENDER_CACHE_MB` (256)
  of copies in the temporary directory, the least recently used going first, and requests that
  come while it is being copied wait for that copy. Size the API's `/tmp` tmpfs for it beside
  the uploads (deploying.md, "The image" and "Drawing pages"); a directory storage needs none of
  it. A page of the load test's 62 MB scan from the compose bucket is drawn in 0.68 s at the
  50th percentile, where it took 2.06 s ("Load").
- `WORKER_CONCURRENCY` is 16 by default, where it was 2, and the new `PARSE_CONCURRENCY` (2) is
  how many of those indexers parse a file at once, each in a child of up to `PARSE_MEMORY_MB`;
  the others wait their turn, so an indexer waiting on the model no longer brings a parse's
  memory with it. `DB_POOL_SIZE` is 10 and `DB_MAX_OVERFLOW` 20 (they were 5 and 10), since the
  worker needs 28 connections at the new defaults: a deployment that set the pool or the
  concurrency by hand checks the sum (a worker refuses a pool too small for its threads) and
  sums its replicas against the database's `max_connections`. `emudoc_parses{state}` counts the
  parses running and the indexers waiting for one, and an index run's line has a `parse` span
  with the seconds it `waited`. deploying.md's "Load" has the figures: at 3 s an indexing
  request, 37 documents a minute with 2 indexers, 145 with 8, 289 with 16 and 554 with 32, the
  worker's memory the same throughout.
- What indexing one document costs is bounded: `PARSE_MAX_CHARS` (20,000,000) refuses a file
  with more text in the parse child, before the worker holds any of it, `ENRICH_MAX_REQUESTS`
  (50) caps the requests one document's summaries and facts take, a table of more than 100
  rows is shown to the model in part, and the embed stage keeps its vectors as 4-byte floats
  and writes them 500 at a time. deploying.md's new "Large documents" says what bounds what,
  and "Load" has the workbooks' new figures.
- `python -m loadtest` in `backend/` is the load test: the API and a worker in production's
  shape on one machine, against a stand-in model, with a member who uploads 3,000 documents,
  searches them, asks 20 questions at once and uploads a 300-page scan and 50 MB workbooks. It
  prints what the member waited at the 50th and 95th percentiles; deploying.md's new "Load"
  has the figures of 2026-10-19 and what they mean for `WORKER_CONCURRENCY`,
  `ANSWER_CONCURRENCY`, long scans, large workbooks and pages drawn from a bucket. CI runs it
  at a size that takes a minute.
- An answer's event stream, and a page waiting to be drawn, give their request's database
  connection back before they wait. Each stream held one for the answer's length and needed a
  second for each look at the answer, so 20 members following answers took the API's 15: every
  answer waited `DB_POOL_TIMEOUT` and some streams ended with "Following answer … stopped:
  every database connection is in use".
- PDF pages are drawn in child processes the API and the worker keep for it,
  `RENDER_PROCESSES` (2) at a time each, where one lock let a process draw a single page at a
  time; each page within `RENDER_TIMEOUT` (30 s), `RENDER_MEMORY_MB` (1024) and 2,576 pixels on
  its longer side, so a crafted page can no longer take the API's memory. A page past a limit is
  not drawn and says why; a scanned one fails its document's indexing. Once a PDF is parsed, a
  `render_pages` job draws its first `PRERENDER_PAGES` (3) pages, charged as `render` usage to
  the organisation. `emudoc_renders_total` and `emudoc_render_seconds` count them by purpose and
  outcome (deploying.md, "Drawing pages").
- `GET /api/projects/{id}/documents` lists one folder (`folder_id`, the root without it) a page
  at a time (`after`, `limit`, at most 500; `{documents, next_after}`), in the library's order,
  and answers 304 to an `If-None-Match` that names the page, which is what the library sends
  while a document indexes, every 1.5 s, where it fetched every document of the project. Folders
  carry their `document_count`, a project its `unenriched_count`. The migration makes an ICU
  collation, `natural_order`, for `documents.filename`, with an index in it: a database server
  without ICU cannot take it (RDS and the compose image have it). After an upgrade that brings
  another ICU, `python -m app.maintenance database`, the start and the watch name the index to
  rebuild (deploying.md, "The database").

- On AWS, what cannot wait has a topic of its own for a pager, `emudoc-<environment>-pages`
  (`tofu output pages`): Prometheus's critical alerts, in a few words an SMS can carry, a
  container in trouble, and CloudWatch's three alarms, each when it begins and when it is over,
  while the alerts topic keeps hearing everything. Subscribe the pager by hand, a paging
  service's SNS integration or a phone (deploying.md, "On call"); the module subscribes nobody,
  since the service's address carries its key. The instance's host settings gain
  `EMUDOC_PAGES`, so apply both roots, which replaces each instance, before the next release,
  which stops on an instance without it.
- Three service levels, each over 30 days: 99.5 % of the members' requests without a server
  error, 95 % of answers begun within 30 seconds of the question, 95 % of uploads ready within
  10 minutes, with a fourth, the app up, for an uptime check from outside (deploying.md,
  "Service levels"). Two new metrics measure the waits, `emudoc_answer_first_text_seconds` and
  `emudoc_document_ready_seconds` (once per document, by the new `documents.ready_at`), and six
  new alerting rules in `deploy/prometheus/alerts.yml` (33 now) fire when an objective's budget
  is spent too fast: `EmudocErrorBudgetFastBurn`, `EmudocAnswersLateFastBurn` and
  `EmudocDocumentsLateFastBurn` as critical, their `SlowBurn` twins as warnings.
- deploying.md says who hears which alert and how soon ("On call"), and what to do for what goes
  wrong most often ("Runbooks"): a first look, server errors, slow answers, a stuck upload, a
  provider down, switching models, changing the embedding model and what it costs, rotating
  each key, restoring a backup, draining the workers, an organisation spending fast. "Every
  setting" lists every variable the app reads, with its default and what production asks of it.
- `python -m app.maintenance jobs` shows the queue as it stands: each kind's jobs due, waiting,
  running and failed, how long the oldest due one has waited, and each running job with what it
  is for, its attempt and whether its lease is current. `jobs --document <id>` shows where one
  document stands, each stage with what its members read, its index jobs, and what the newest
  one means and what to do.
- `python -m app.maintenance costs` prints what each organisation's model use cost over the
  last 30 days (`--days`), costliest first: its plan, the plan's daily limit, its costliest day
  against that limit and the days it reached it, its answers, model calls and pages indexed;
  then the platform's total against `DAILY_SPEND_CAP`, the cost by kind of work and model, and
  the models whose tokens were recorded without a price (deploying.md, "What each organisation
  costs"). The spending warning names `costs --days 1`.
- Each run of a job ends with one line in the worker's log that says where its time went: its
  wait for a worker, each stage, each request to a model or the embeddings endpoint (the
  model, how it ended, its first event, the tokens), each tool call, the writes of an answer's
  events and when its first text was written (deploying.md, "Traces"). Every request starts a
  trace, `trace_id`, which its lines and the lines of every job it queued name, so one id finds
  a question and its answer, or an upload and its documents' indexing. uvicorn's access lines
  say how long each request took to begin its response (`seconds`), and the lines of a
  question's request and of following an answer name its `message_id`.
- Production refuses to start without `SERVER_LOG_DAYS`, the days the deployment keeps its
  server logs (the app's, the proxy's and the database's), which the privacy policy now names;
  have whatever collects them delete older lines, as journald's `MaxRetentionSec` does
  (deploying.md, "How long they are kept"). On AWS the module writes it from `server_log_days`
  (30, `shared/`): every container logs to the instance's journal, which each release holds to
  the period, and RDS keeps Postgres's log a day short of it (7 days at most) without an error's
  DETAIL, which could repeat an email address. Apply both environments, then release.
- On AWS the account root keeps a CloudTrail trail of every region (`account/trail.tf`): a key
  and a bucket of its own, signed files kept `trail_days` (365), and every request for the
  state bucket's objects. Each environment keeps the documents bucket's access logs and the
  VPC's flow logs in a logs bucket of its own for `access_log_days` (30, `shared/`), and runs
  Database Insights' standard mode on its database. The module writes the new setting
  `ACCESS_LOG_DAYS`, from which the privacy policy names those logs and their period. Apply the
  account root and both environments (deploying.md, "The account's records").
- In production a document's stage, its error and an answer's error name no model, address or
  setting. The operator's sentence is in the log (the worker's line with each retry, `Stage …
  of document … failed: …` for a failure nothing runs again, the model's own failures) and in
  the job's `last_error` once the worker gives up. Development still shows it to whoever uses
  the app, and rows written before keep what they said.
- On AWS the instance runs Prometheus with the alerting rules and Alertmanager, which sends what
  fires to the environment's alerts topic with a role of its own, and its watchdog to a
  heartbeat topic every few minutes. CloudWatch reports the heartbeat's silence (the instance,
  its Docker, Prometheus or Alertmanager gone) and the instance's failed status checks, which
  it recovers or reboots. Both topics are encrypted with a key of the environment's own. The
  pages of Prometheus and Alertmanager answer on the instance's loopback, through Session
  Manager. Apply both environments before releasing this commit: it replaces their instances,
  and a release on an instance from before stops and says so (deploying.md, "Alerts", "On
  AWS").
- On AWS the account root sends the trail to CloudWatch Logs as well, and alarms on what
  should not happen unannounced, after CIS's benchmark: the root user at work, a console
  sign-in without a second factor, bursts of failed sign-ins and refused calls, and changes to
  IAM, the trail, keys, buckets' policies, security groups and the network. They go to a topic
  of the account's own, which `shared/`'s new `account_alert_email` subscribes. Fill it in and
  apply the account root (deploying.md, "The account's records").
- A new alerting rule, `EmudocWatchdog`, always fires, for a dead man's switch, and
  `EmudocNoApi` and `EmudocNoWorker` carry the `process` they miss, so that an Alertmanager can
  hold back `EmudocProcessDown` meanwhile (deploying.md, "Rules on the metrics").

## 2026-10-18

Members:

- When the AI model or semantic search cannot be used, the header and the Ask tab say what
  cannot be done for now and to try later, and no longer name the models, the search service's
  address or the settings to change. The header's "Embeddings" is called "Semantic search".
- A page that runs into an error shows a Reload button and a reference to quote in place of a
  blank screen, and the error is reported. An error of the server's own says so in a sentence
  that ends with the reference, rather than "Internal Server Error".
- Where error reports are made, the privacy policy says what they hold, and the subprocessor
  list names the error tracker when it is a company's.

Operators:

- In production `GET /api/status`, the members' status line, names no model, address or
  setting: why the models or the embeddings endpoint cannot be used is in the start's log, the
  metrics and the alerts. Development still names them and says what to change.
- With `ALERT_EMAIL` set, the worker also writes when something it can see goes wrong, once when
  it begins and once when it is over: questions or documents waiting long for a worker,
  indexing or answers failing, a model failing over to its fallback, the embeddings endpoint,
  the scanner or the storage not answering, the database failing to archive its log or falling
  behind, and what `python -m app.maintenance database` flags. A migration adds the table that
  remembers what was raised (`watch_alerts`). deploying.md, "Alerts".
- `deploy/prometheus` holds 26 alerting rules over the metrics, tested with promtool in CI, and
  the Prometheus configuration that loads them beside the compose file, for an Alertmanager.
  The metrics gain whether a worker hears of queued jobs or polls for them, each table's live
  and dead rows, the oldest open transaction, and the archiving of the write-ahead log.
- On AWS, `alert_email` is the app's `ALERT_EMAIL` too, and the instance reports to the alerts
  topic a container that stopped, restarts or fails its health check for five minutes and more,
  and again when every container is well.
- `SENTRY_DSN` reports the errors nobody planned for, the API's, the worker's and the page's, to
  Sentry, or to GlitchTip or a Sentry of your own, with what the log would show of them and the
  ids of the request or job as tags; never a message the app did not write, a frame's
  variables, a request's body, cookies or query values. `SENTRY_ENVIRONMENT` files them under
  an environment's name, which the AWS module sets; there `SENTRY_DSN` goes into the secrets
  path. The page reports through `POST /api/page-errors`, held to `RATE_PAGE_ERRORS` a minute
  per address (10). deploying.md, "Error tracking".
- The API and the worker serve metrics in Prometheus's format at `METRICS_ADDRESS`, which the
  image sets to 9464 of its container and the compose file publishes nowhere: requests by
  route, the database pool, the models' requests, outcomes, latency, first events, tokens and
  fallbacks, the stages, the jobs and their waits, the worker's threads, uploads and their
  refusals, rate refusals, and from each worker the documents and jobs by status, the backlog's
  age, the answers in flight, the organisations and their bytes by plan, and the day's spend
  against `DAILY_SPEND_CAP`. No label names an organisation, a user or anything a member typed.
  deploying.md, "Metrics", with a Prometheus to scrape them.
- A process that runs the worker needs one more database connection than before for its
  threads, the metrics' reading of the database: `DB_POOL_SIZE` + `DB_MAX_OVERFLOW` must hold
  `WORKER_CONCURRENCY` + `ANSWER_CONCURRENCY` + 4 (the defaults do).
- A 500 is JSON with the request's id in its body and in `X-Request-ID`, and carries the
  security headers, which it lacked.

## 2026-10-17

Members:

- The privacy policy says that the server logs name the account and project each request was
  for.
- The user menu shows the version of Emudoc that runs.
- When Gemini cannot be reached, questions and documents go to the second model, as they did
  when Gemini was down or refused them.
- A file that needs more memory to parse than allowed says so and suggests splitting it, where
  it sometimes said that it could not be parsed.
- A passage opened from an answer's citation or a search result comes into view in a text
  document, where it sometimes stayed below the screen once the outline appeared beside it.

Operators:

- In production the API, the worker and the migrations log one JSON object a line, with the
  time in UTC and what the line was about: the request's id and the user, organisation and
  project it acted on, or the job's id and kind and its document or answer. `LOG_FORMAT=text`
  gives the lines development writes; the maintenance commands write text either way.
  uvicorn's lines, its access log included, come out the same way, and the probes that
  succeeded are left out of the access log.
- Every response carries its request's id in `X-Request-ID`. The id is the proxy's when it
  sends one, which the Caddy and nginx examples in `deploying.md` and the AWS Caddyfile now
  do, so the proxy's access log and the app's lines name the same request.
- The worker's lines are logged as `app.worker`, where they were `__main__`.
- The compose file runs llama.cpp's server for the embeddings in place of Ollama, as
  `embeddings` at `http://embeddings:8080/v1`, which `EMBEDDING_BASE_URL` must now name: the
  same model, nomic-embed-text, with the same vectors (to a cosine of 0.999999), so nothing is
  embedded again; an image of 1.2 GB on disk in place of 9.2 GB, without the 43 known
  vulnerabilities Ollama's binary carried; and the weights downloaded from Hugging Face once
  and checked against their SHA-256. Other weights are named by `EMBEDDING_WEIGHTS_URL` and
  `EMBEDDING_WEIGHTS_SHA256`, where `EMBEDDING_MODEL` alone named Ollama's. Ollama's volume
  and image can go (`docker volume rm emudoc-deploy_models`). Dependabot proposes a build of
  the server every week, and CI checks each against the stored vectors (`deploying.md`, "The
  embeddings endpoint").
- The images the deployment runs beside the app, ClamAV's, Ollama's and Caddy's, are scanned
  for known vulnerabilities every morning too. Caddy 2.11.7 carries three in Go's HTTP and
  TLS code, fixed in Go 1.26.9 after it was built, and Ollama 0.40.5 forty-three, since Ollama
  builds with Go 1.26.0; both are accepted until a date in `deploy/.trivyignore.yaml`, each
  with what it exposes (`deploying.md`, "The image").
- The Models workflow asks every model and fallback the deployment names what the app asks of
  them, every morning, once the repository names them and holds their keys (`deploying.md`,
  "The models").
- Ollama 0.40.5 and Caddy 2.11.7 in the compose files (Caddy's fixes a crash when proxying a
  request body over HTTP/2). Ollama runs with `OLLAMA_NO_CLOUD=1`, so that it asks ollama.com
  for the embedding model alone, not, as 0.40 would every four hours, for models to recommend.
- `/api/health` and `/api/livez` name the version, the commit the image was built from (the
  Dockerfile's `VERSION`), each process logs it as it starts, and the image is tagged with the
  commit's full SHA.
- Releases come from CI: every commit on `main` that passes the checks is scanned, pushed and
  released on staging; production is released by hand from the Release workflow and takes only
  a commit staging ran. The image repository takes images from CI alone, and no AWS key is kept
  in GitHub.
- The dependencies and the image are audited for known vulnerabilities every morning, every
  commit is scanned for keys, the images are pinned by digest, and Dependabot proposes the
  updates.

## 2026-10-16

Members:

- Questions are answered and documents indexed while a model provider is down, by a second
  model.

Operators:

- `AGENT_FALLBACK_MODEL` and `INDEX_FALLBACK_MODEL`; production refuses a model left unset or
  named by an alias its provider moves.
- Staging and production on AWS, in OpenTofu (`deploy/aws`).
- CI on every push to `main` and every pull request.

## 2026-10-15

Members:

- An account can be deleted, with 14 days to change one's mind, and its data downloaded.
- Chats older than the plan keeps them are removed.
- Owners and admins can read the organisation's audit log: sign-ins, uploads, downloads,
  deletions and sharing.
- Signing up asks to accept the terms of service and the privacy policy.
- An answer keeps streaming across a restart of the server.

Operators:

- The logs hold no document text, question, answer, key or email address.
- Backups: the bucket keeps deleted files within `BACKUP_DAYS`, `after-restore` sets the app
  right after a restore of the database, and a restore drill runs every month.
- One container image for every process, with the migrations as a release step, and a compose
  file for one machine.
- Liveness and readiness probes for the API, an alive file for the worker.
- Managed databases and pgbouncer in transaction mode; indexing goes on through a failover.
- Production requires an embeddings endpoint; the compose file runs Ollama.

## 2026-10-14

Members:

- Uploads are scanned for malware, and a flagged one is quarantined.
- An upload whose content is not the type its name says is refused, and a file locked with a
  password or without pages says why it cannot be read.
- Original files are downloaded rather than opened in the browser, images apart.
- The subprocessor list is published.

Operators:

- `DATA_REGION` keeps the documents in one region.
- Production refuses unencrypted connections that leave the deployment's network, and checks
  the mail relay's certificate.
- The app answers no cross-origin request.

## 2026-10-13

Members:

- Uploads, questions, searches and sign-ins are held to a rate per minute.
- An answer's stream stays open while the model thinks.

Operators:

- `DAILY_SPEND_CAP` and the plans' daily limits on model spending, and `DISABLED_PROVIDERS` to
  stop a provider.
- Secure cookies in production, the browser's security headers on every response, and a guide
  to running behind a TLS proxy.

## 2026-10-12

Members:

- Each answer and document shows what it used and what it cost.
- Organisations are held to their plan's limits on projects, documents, storage, pages and
  questions.
- Answers are written in the background and go on when the tab closes.
- A stage that failed because a provider was out of reach is tried again by itself.
- Too large a question, upload or scan, an image too large to decode, and the same file
  uploaded twice at once are refused with a sentence instead of an error.

Operators:

- Parsing runs in a child process with time, memory, page and row limits; model and embedding
  requests time out.
- The workers are shared fairly between organisations; the jobs they gave up on are kept,
  mailed and listed (`failed-jobs`).
- The database pool is sized by settings and answers 503 when full; the answers streamed at
  once are capped.
- Usage is recorded per organisation (`usage`).

## 2026-10-11

Members:

- Organisations, roles and sign-in, with the sign-in pages; a session is required.
- The library is a tree, with an indexing ring per document.

Operators:

- The schema is Alembic migrations, applied by a command, and `rebuild-embeddings` resizes the
  vector column.
- `ENVIRONMENT=production` refuses development's settings; `DATABASE_URL` has no default.
- Files are kept in a directory or a bucket (`STORAGE_URL`); old rendered pages and the files
  of deleted documents are swept away.
- Background work is jobs with leases, run by a worker process of its own in production.
- Cross-site requests that change something are refused.

## 2026-10-05 to 2026-10-10

Members:

- Documents uploaded into a project are indexed, searched and asked about, with answers that
  cite their passages.
- Several saved chats per project.
- A workspace of three panes: the library, the open documents and the agent.
- Thumbnails of PDFs and images, a light theme by default with a toggle.
- The product is called Emudoc.
