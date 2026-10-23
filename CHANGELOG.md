# Changelog

What changed for the people who use Emudoc (**Members**) and for those who run it
(**Operators**), newest first, by the day the change reached `main`. A release is a commit: its
full SHA is the image's tag, the version `/api/health` reports and the user menu shows, so
`git log <released>..<to release>` lists the commits a release adds, and this file says what
they change. A commit that changes what a member or an operator sees adds its line here, under
its day, in the same commit.

The **Members** entries are what the app's "What's new" shows (`backend/app/changelog.py`), so
they are written for members, as Markdown. The file keeps one shape, which the suite holds it
to: `## YYYY-MM-DD` a day (or `## YYYY-MM-DD to YYYY-MM-DD`), the newest first, then `Members:`
and `Operators:`, each a list of `- ` entries whose lines go on indented by two spaces, a day's
newest entry first.

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
