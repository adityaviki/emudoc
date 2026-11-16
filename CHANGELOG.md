# Changelog

What changed in each release of Emudoc, for the people who use it (**Users**) and for the people
who run it on their server (**Admins**), the newest release first. A release is a version tag,
`v0.1.0`, which publishes the image `ghcr.io/adityaviki/emudoc:0.1.0` for a team to upgrade to
(deploying.md, "Upgrades"); what is on `main` and not released yet is under **Unreleased**.

A change that users or admins would notice adds its line under Unreleased in the same commit,
written for them, as Markdown. The file keeps one shape, which the suite holds it to
(`backend/tests/test_changelog.py`): `## Unreleased` first, then each release as
`## X.Y.Z (YYYY-MM-DD)`, the newest first; under each, `### Users` and `### Admins`, each a list
of `- ` entries whose lines go on indented by two spaces, the newest entry first. A release
renames Unreleased to its version and day, and a new Unreleased starts above it.

## Unreleased

### Users

- Comments are gone, with their mentions: a document's tab no longer offers "Comments" or
  "Comment on this", and nobody is told of a mention or of a reply in a thread. Write what the
  project's members should know about a document in its note, or ask the agent about it in a
  chat shared with the project. The notices of a mention or a reply already under the bell stay
  until they are pruned, and still open their document.
- The library's "Activity" is gone. "Recent" still lists the documents you opened lately, and the
  workspace's owners and admins read what was done in each project in its audit log.

### Admins

- Migration 7e532b9ccb61 drops the `comment_threads` and `comments` tables with the threads and
  comments in them, and what the members and the workspace chose for the notices of a mention
  and of a reply in a thread; a downgrade makes the tables again, empty.
  `GET|POST /api/documents/{id}/threads`, `POST /api/threads/{id}/comments`,
  `PATCH /api/threads/{id}`, `PATCH|DELETE /api/comments/{id}`,
  `GET /api/projects/{id}/mentionable` and `GET /api/projects/{id}/activity` are gone, for API
  keys too; `/api/v1` and the webhooks' events are unchanged. A project's export no longer
  lists its documents' `threads`, nor an account's export its `comments`. The notices of the two kinds already sent stay in the notification
  centres until they are pruned, and the audit log's `comment_added`, `comment_deleted`,
  `thread_resolved` and `thread_reopened` entries stay and still read.

## 0.1.0 (2026-11-14)

### Users

- The first public release of Emudoc. Upload your team's documents into a project and ask about
  them in your own words: every answer cites the passages it read, and a citation opens the
  document at that passage.
- Documents: PDF, Word, Excel, PowerPoint, RTF, HTML, CSV, plain text and Markdown, photos and
  scans (read by the model), emails saved as .eml with their attachments, and ZIP archives.
  File them in folders, drag them about, upload a new version of one, open several in tabs, and
  take back what you deleted from the trash for 30 days. Favourites, recent documents, a note
  and the details of each document.
- Search a project from its library, the files by name first and then the passages, by their
  words and their meaning, or search every project you see at once.
- Facts: each document is read for its kind (an invoice, a contract, a purchase order, a price
  list and more, or a template of your project's own) into a facts table you sort, filter,
  check and correct cell by cell, and download as a CSV file.
- Checks: cross-checks between documents (an invoice against its purchase order, the contract
  or the price list), run when you ask or by an automation as documents arrive; reviews with an
  assignee, disputed or approved; question sets asked on a schedule, each run a report.
- Writing: the agent writes letters, replies, memos and reports from the documents as Word
  documents in your own My files, to your project's templates for each kind, and writes and
  edits Excel workbooks there, a workbook's formulas worked out as it is written. A disputed
  document's "Write a dispute letter" has the letter written into your My files.
- Working together: a workspace with roles, projects private to the members chosen for them,
  chats private until shared with the project, comments on the words of a document with
  mentions, a project's activity, links that share a document or an answer with someone outside,
  and notices under the bell and by email.
- Download an answer or a whole chat as Word, PDF or Markdown, and a whole project as a ZIP file.
- Help pages, from the help menu, describing your team's Emudoc as it is set up.

### Admins

- A self-hosting guide, `deploying.md`, from the install step by step and the settings a team
  sets, through the models' keys and costs, the proxy, sign-in with Google or Microsoft, mail,
  backups and upgrades, to monitoring and the runbooks, with every setting the app reads.
- One image for amd64 and arm64, `ghcr.io/adityaviki/emudoc`, and one compose file that runs
  everything on one server: Postgres with pgvector, the migrations, the API, a worker and the
  embeddings server, and behind profiles ClamAV, a converter that shows Office files as they
  look and works out the formulas of the workbooks the agent writes, and Caddy with a
  certificate from Let's Encrypt. A team sets four values in `.env`.
- A new install opens its setup page, which makes the first admin and the workspace. Sign-up is
  closed unless `SIGNUP_OPEN`; without a mail relay, admins hand invitations and password links
  on themselves.
- Models through their providers' APIs, Gemini by default, or Claude, OpenAI, OpenRouter,
  Vertex AI, or an OpenAI-compatible server, each with a fallback of another provider; a daily
  spending cap; and what they cost on the Administration page, beside the background work and
  the services, with a switch that stops a provider at once.
- Backups of the database and the files with one command, `docker compose run --rm backup`,
  restored with another; upgrades by moving `EMUDOC_TAG`, the migrations run first.
- Optional, each once configured: mail through SMTP or Resend, a project's email address, sign-in
  with Google or Microsoft, folders connected from Google Drive, Dropbox and OneDrive, webhooks,
  API keys for the versioned API at `/api/v1`, and an upload client that sends a folder.
- Metrics for Prometheus with example alerting rules, JSON logs that follow a request through
  the jobs it queued, and error tracking through Sentry's protocol.
- The secrets the database keeps encrypted with keys it never holds (`ENCRYPTION_KEYS`), and
  every connection off the server's own network checked for TLS.
- Free software under the GNU Affero General Public License, version 3 only: the help menu's
  "Source code" leads to the code the install runs.
