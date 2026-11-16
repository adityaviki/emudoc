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

- Cross-checks are gone, with their runs and discrepancy reports: the library no longer offers
  "Cross-checks", whose rules compared a fact of one kind of document with another's, and no run
  keeps what they found. Ask the agent in a chat to check documents against each other instead,
  an invoice against its purchase order or the price list: it compares their facts and cites
  where each figure is written, as any answer does.
- Automations are gone: no check runs on its own as documents arrive, and nobody is told what
  one found. The notices of an automation's run already under the bell stay until they are
  pruned, and open the project.
- Question sets are gone, with their reports: the library no longer offers "Question sets", and
  no set asks its questions every day, one day a week or as documents arrive. Ask the agent the
  same questions in a chat when you want them answered: it answers from the documents as they
  read then, citing each passage. The notices of a report ready already under the bell stay
  until they are pruned, and open the project.
- The weekly digest is gone: nobody is told on Monday morning what arrived in their projects
  over the week, and the account page no longer offers "What arrived this week". The library's
  "Date added" order, newest first, lists what arrived in a folder.
- Reviews are gone: a document is no longer put in review, assigned to a member, approved or
  disputed. The library's "Reviews", the review controls on a document's tab and in its menu,
  the marks beside a document's name in the library and in the facts table, the search's "In
  review" filter and an automation's "Put in review" go with them, the facts table's file loses
  its "Review" column, and nobody is told of a document assigned. The notices of a document
  assigned already under the bell stay until they are pruned.
- A disputed document's "Write a dispute letter" is gone, and My files no longer list the files
  the agent was asked to write outside a chat. Ask for a dispute letter in a chat instead: the
  agent writes it into your My files, to the project's template for dispute letters, citing
  where each figure is written.
- Comments are gone, with their mentions: a document's tab no longer offers "Comments" or
  "Comment on this", and nobody is told of a mention or of a reply in a thread. Write what the
  project's members should know about a document in its note, or ask the agent about it in a
  chat shared with the project. The notices of a mention or a reply already under the bell stay
  until they are pruned, and still open their document.
- The library's "Activity" is gone. "Recent" still lists the documents you opened lately, and the
  workspace's owners and admins read what was done in each project in its audit log.

### Admins

- Migration b8ee7b962950 drops the `cross_checks`, `workflow_runs`, `workflow_findings`,
  `automations`, `automation_checks` and `automation_arrivals` tables with the rules, the runs
  with what they found and their reports, the automations and the documents waiting for them,
  the `run_workflow` and `run_automation` jobs, queued or kept, with their kinds, and what the
  members and the workspace chose for the notice of an automation's run; every webhook stops
  listening for `check_run.completed`, one that listened for nothing else hearing nothing until
  an admin chooses its events again, and the delivery log's entries of that event go with the
  tries still waiting. A webhook now hears of the documents alone. A downgrade makes the tables
  again, empty. `GET|POST /api/projects/{id}/cross-checks`, `PUT|DELETE /api/cross-checks/{id}`,
  `GET /api/cross-checks/{id}/findings`, `GET|POST /api/projects/{id}/check-runs`,
  `GET|DELETE /api/check-runs/{id}`, `GET /api/check-runs/{id}/findings`,
  `POST /api/check-runs/{id}/report`, `GET|POST /api/projects/{id}/automations` and
  `PUT|DELETE /api/automations/{id}` are gone, for API keys too, and so is the setting
  `AUTOMATION_WAIT`, which an `.env` may drop (one that still sets it is ignored); the worker's
  answerer threads (`ANSWER_CONCURRENCY`) run the answers alone. A break of `/api/v1`:
  `GET /api/v1/projects/{id}/cross-checks`, `GET|POST /api/v1/projects/{id}/check-runs`,
  `GET /api/v1/check-runs/{id}` and `GET /api/v1/check-runs/{id}/findings` are gone with their
  `CrossCheck`, `CrossCheckList`, `NewCheckRun`, `CheckRun`, `CheckRunList`, `Rule`,
  `FindingCounts`, `Finding`, `FindingList` and `Partner`, and the webhook event
  `check_run.completed` (`CheckRunEvent`) is no longer sent, nor may a webhook choose it (0.1.0
  promised nothing more before 1.0); the contract is written again without them. The notices of
  an automation's run already sent stay in the notification centres until they are pruned; the
  audit log's `cross_check_created`, `cross_check_changed`, `cross_check_deleted`,
  `cross_checks_run`, `check_report_requested`, `check_run_deleted`, `automation_created`,
  `automation_changed` and `automation_deleted` entries stay and still read, and so do the usage
  rows of the reports, of the kind `report`, which the Administration page's costs list by that
  kind, and the questions their asking counted.
- Migration f149fd1a2183 drops the `question_sets`, `question_set_arrivals` and `question_reports`
  tables with the sets, the documents waiting for them and the reports in them, the column
  `digested_on` of `memberships`, the `run_question_set`, `write_question_report` and
  `send_digests` jobs, queued or kept, with their kinds, and what the members and the workspace
  chose for the notices of a report ready and of the weekly digest; every webhook stops
  listening for `report.completed`, one that listened for nothing else hearing nothing until an
  admin chooses its events again, and the delivery log's entries of that event go with the tries
  still waiting. A downgrade makes the tables and the column again, empty.
  `GET|POST /api/projects/{id}/question-sets`, `PUT|DELETE /api/question-sets/{id}`,
  `POST /api/question-sets/{id}/run`, `GET /api/projects/{id}/reports` and
  `GET|DELETE /api/reports/{id}` are gone, for API keys too, and no setting goes. A break of
  `/api/v1`: `GET /api/v1/projects/{id}/reports` and `GET /api/v1/reports/{id}` are gone with
  their `Report`, `ReportSummary`, `ReportAnswer` and `ReportList`, and the webhook event
  `report.completed` (`ReportEvent`) is no longer sent, nor may a webhook choose it (0.1.0
  promised nothing more before 1.0); the contract is written again without them. An account's
  export no longer lists the question sets its member made, nor a membership's `digested_on`.
  The notices of the two kinds already sent stay in the notification centres until they are
  pruned; the audit log's `question_set_created`, `question_set_changed`,
  `question_set_deleted`, `question_set_run` and `question_report_deleted` entries stay and
  still read, and so do the usage rows of the reports' answers, of the kind `answer`, which the
  Administration page's costs count with the chats'.
- Migration cc45ffa7565c drops the `my_file_writings` table with the writings under way or failed
  in it, the columns `review`, `assignee_id`, `review_changed_by` and `review_changed_at` of
  `documents` and `review` and `assignee_id` of `automations`, the `write_draft` jobs, queued or
  kept, with their kind, and what the members and the workspace chose for the notice of a
  document assigned; a downgrade makes the table and the columns again, empty.
  `PUT /api/documents/{id}/review`, `GET /api/projects/{id}/reviews`,
  `GET /api/projects/{id}/reviewers`, `POST /api/projects/{id}/my-files/writings`,
  `POST /api/my-file-writings/{id}/retry` and `DELETE /api/my-file-writings/{id}` are gone, for
  API keys too, and so are a document's `review`, `assignee_id`, `assignee`, `review_changer`
  and `review_changed_at`, an automation's `review` and `assignee_id`, the search's `reviews` and
  the My files list's `writings` in the page's API. A break of `/api/v1`: its `Document`, which
  the bodies of the `document.ready` and `document.failed` webhooks carry too, no longer holds
  `review` and `assignee_id` (0.1.0 promised nothing more before 1.0); the contract is written
  again without them. A project's export no longer names a document's `review`, `assignee` and
  `review_changed_at`. The notices of a document assigned already sent stay in the notification
  centres until they are pruned; the audit log's `document_review_changed` and
  `document_assigned` entries stay and still read, and so do the usage rows of the kind `draft`,
  what the writings cost, which the Administration page's costs list by that kind.
- Migration 7e532b9ccb61 drops the `comment_threads` and `comments` tables with the threads and
  comments in them, and what the members and the workspace chose for the notices of a mention
  and of a reply in a thread; a downgrade makes the tables again, empty.
  `GET|POST /api/documents/{id}/threads`, `POST /api/threads/{id}/comments`,
  `PATCH /api/threads/{id}`, `PATCH|DELETE /api/comments/{id}`,
  `GET /api/projects/{id}/mentionable` and `GET /api/projects/{id}/activity` are gone, for API
  keys too; `/api/v1` and the webhooks' events are unchanged. A project's export no longer
  lists its documents' `threads`, nor an account's export its `comments`. The notices of the two
  kinds already sent stay in the notification centres until they are pruned, and the audit
  log's `comment_added`, `comment_deleted`, `thread_resolved` and `thread_reopened` entries stay
  and still read.

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
