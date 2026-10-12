# Emudoc

Upload documents into a project, and ask an AI agent about them. Each document is parsed once,
then indexed three ways so the agent can find anything quickly and cite where it found it.

## How it works

```text
                                 ┌──> Search index ──────┐
Upload ──> Parse ──> Structure ──┼──> Hierarchical map ──┼──> Agent tools
                                 └──> Fact table ────────┘
```

Every upload queues a job that runs it through four stages in the background. Jobs are rows in
Postgres that the worker claims with a lease, so a job whose worker died is picked up again by
the next one; and a worker that lost its lease stops at its next commit with nothing of that
stage written, so two workers never write the same document. The workers are shared between
organisations: a claim takes the job of the organisation that has had the least of them lately
(jobs running or finished in the last five minutes), so one organisation's upload of five
hundred documents costs another's single upload one slot, not five hundred. Uploads go before a
project reprocess regardless. Each stage stores its output, so any stage can be run again on
its own. A stage
that failed because the model, the embedding endpoint or the bucket was out of reach (down,
rate limiting, a dropped connection) is run again by itself after 1, 2, 4 and 8 minutes; the
stage says so in the document's status until it succeeds, and after the last try it stays
failed until "Index again". A stage that failed for a reason that will not pass on its own, such
as a rejected key or a file nothing can be read from, is not retried. A job the worker gave up
on, after the last retry, after a crash, or after losing it to a dying process three times, is
kept as failed for a month: `uv run python -m app.maintenance failed-jobs` (in `backend/`)
lists them with the reason, and `--requeue` runs their documents again from the stage they
stopped at; with `ALERT_EMAIL` set, the worker mails the new ones there every quarter of an
hour as one digest. A request that gets no
answer is given up on and counts as the provider being out of reach: an indexing request after
five minutes, an embedding request after two, and a turn of an answer in the chat after two
minutes or a minute of silence, which the chat shows as an error. The parse stage runs the
parser in a process of its own with a time limit, a memory limit and caps on the pages of a PDF
and the rows of a sheet (`PARSE_TIMEOUT`, `PARSE_MEMORY_MB`, `PARSE_MAX_PAGES`,
`PARSE_MAX_ROWS`), so a file that would take hours or all the memory stops that process alone,
and the document says which limit it hit.

| Stage     | What it does                                                                      | Needs      |
| --------- | --------------------------------------------------------------------------------- | ---------- |
| parse     | Turns the file into ordered blocks (headings, text, tables) with page and position | nothing    |
| structure | Builds the outline from the headings and cuts sections into passages               | nothing    |
| enrich    | Writes a summary per section and document, and extracts typed facts                | AI model   |
| embed     | Embeds every passage for semantic search                                           | embeddings |

The agent answers with five tools: `list_documents` and `get_outline` read the map, `search`
reads the passage index (keywords and meaning, merged), `query_facts` reads the fact table, and
`read` returns exact source text. Answers cite passages, and each citation opens the source.

A project can hold any number of chats. Each one is saved with its answers, including the
steps the agent took and the passages it was shown, so a chat can be reopened later. An answer
is written by the background worker, not by the request that asked the question: the question
is saved with an empty answer and a job, the worker runs the agent and writes each event to a
table, and the page follows them from there. So an answer keeps streaming while you look at
another tab, survives a reload, a dropped connection and a restart of the API, and is picked
up where it was by another tab that opens the chat. Stop ends it with what arrived so far,
whether or not a worker is alive; a chat takes one question at a time.

What an organisation uses is recorded as it happens, one row per measure in `usage_events`:
today an answer's model calls and tokens, with the organisation, project and member they
belong to and the message they were for, counted at the end of the run that made them, so an
answer that was stopped or that a worker lost is counted for the calls it made. The indexing
stages and the page renders are next. `uv run python -m app.maintenance usage <slug>` (in
`backend/`) prints an organisation's use per day in UTC, by kind and unit, over the last 30
days or `--days`.

## The workspace

A project opens as two panes. On the left is the **library**: the project's documents filed in
folders, with the usual file-manager moves (new folder, rename, drag to move, delete, upload by
button or by dropping files, search), shown as a tree that opens folders in place. Opening a file
adds a third pane in the middle, the **document**, with a tab for each open file; the tabs stay
open across reloads, and closing the last one leaves the library and the agent side by side again.
PDFs show their rendered pages with the extracted text laid invisibly over them, so it can be
selected and searched; spreadsheets show one tab per sheet; images show the picture above the text
read from it. On the right is the **agent**. Clicking a citation in an answer opens the cited
document in the middle at that passage, with the library still beside it. The agent sees the
folders too: ask about "the invoices in Finance/2026" and `list_documents`, `search` and
`query_facts` work within that folder.

## Run it

You need Docker, [uv](https://docs.astral.sh/uv/) and Node with pnpm.

```bash
cp .env.example .env                 # then add your GOOGLE_API_KEY
docker compose up -d                 # Postgres with pgvector on 127.0.0.1:5437
cd frontend && pnpm install && pnpm build && cd ..
cd backend && uv run alembic upgrade head   # creates or updates the database schema
uv run uvicorn app.main:app
```

Open <http://127.0.0.1:8000> and create an account. The backend serves the API and the built
frontend, and in development it runs the background worker too, so that one process indexes
what it serves. To skip signing in while developing on your own machine, put
`LOCAL_ACCOUNT=true` in `.env`: every request then acts as a local owner account.

In production the API runs no worker. Start `uv run python -m app.worker` (in `backend/`) as a
process of its own next to the API, as many as the backlog needs: it reads the same settings,
refuses the same misconfigurations, and claims jobs from the queue in Postgres. Ctrl+C or
SIGTERM stops it: the jobs it is running get a few seconds to finish (`--grace`, 8 by default,
under Docker's 10), and the rest are queued again for the next worker to take at once. Without
any worker, uploads stay queued and questions wait unanswered. `RUN_WORKER=true` runs the
worker inside the API process instead, for a small deployment.

The database schema is a set of migrations in `backend/migrations/`; `uv run alembic upgrade
head` applies the ones the database has not seen yet, so run it again after updating. The backend
never alters the database itself: at start it checks that the schema is current and refuses to
serve otherwise, naming that command. A database made by a version from before there were
migrations cannot be upgraded; the command says so, and `docker compose down -v` resets it.

For semantic search, run [Ollama](https://ollama.com) with `ollama pull nomic-embed-text`, or
point `EMBEDDING_BASE_URL` at any OpenAI-compatible embeddings endpoint.

When working on the app, run `./dev.sh` instead and open <http://localhost:5173>. It starts the
database, applies the migrations, and runs the backend (restarting when Python changes) and the
frontend dev server (updating the open page as you edit, no build needed), which proxies `/api` to
the backend. Ctrl+C stops both
servers. Port 8000 still serves the last build, so it does not show your edits.

## What works without what

| Missing          | Still works                                           | Does not                                          |
| ---------------- | ----------------------------------------------------- | ------------------------------------------------- |
| Model API key    | Parsing, outlines from headings, search, the viewer   | Summaries, facts, scans and images, the Ask tab   |
| Embeddings       | Keyword search, everything else                       | Search by meaning                                 |

The header shows which of the two is available. After adding a key, restart the backend and
press "Run the model on them" in the Documents tab to enrich what was indexed without it.
Running without the model is for development: with `ENVIRONMENT=production` the backend
refuses to start until the key is in the environment.

## Configuration

Settings live in `.env`; `.env.example` lists them all with their defaults. The ones that matter:

- `RUN_WORKER` is whether the process runs the background worker as well. Unset, it does in
  development and does not in production, where the worker is `uv run python -m app.worker`,
  a process of its own ("Run it" above).
- `WORKER_CONCURRENCY` and `ANSWER_CONCURRENCY` are the worker's threads: 2 documents indexed
  at once and 8 questions answered at once, in lanes of their own, so a backlog of uploads
  never holds an answer up. An answer's thread is idle while the model thinks, so the answers
  cost memory and a database connection each more than anything else.
- `DB_POOL_SIZE`, `DB_MAX_OVERFLOW` and `DB_POOL_TIMEOUT` size the database connections a
  process holds: 5 kept open, up to 10 more while all of those are in use, and 5 seconds a
  request waits for one before it is refused with 503 and asked to try again. An API replica
  needs about one per request in flight; a worker needs one per document and per answer it
  runs at once (an answer holds one only while a tool runs) plus three for its helper
  threads, and refuses to start with a pool that cannot hold them; its listener holds one more
  outside the pool. The sum over every replica must stay under the database's
  `max_connections` (100 for Postgres), or a connection pooler goes in between.
- `MAX_ANSWERS` is how many questions one organisation may have waiting for or receiving an
  answer at once, 20 by default. The worker answers `ANSWER_CONCURRENCY` of them at a time,
  shared between the organisations, so the cap bounds how long a question waits without
  letting one organisation keep the others waiting; the question past it is refused with 429
  and asked to wait a moment.
- `ALERT_EMAIL` is where the worker reports the jobs it gave up on, through the same mail
  settings as the sign-in links, as one digest every quarter of an hour. Empty sends nothing;
  the `failed-jobs` command lists them either way.
- `ENVIRONMENT` is `development` (the default) or `production`. In development the backend
  copies the model keys from `.env` into its environment, where the model libraries read them.
  A production deployment injects every secret through the environment or a secret manager
  instead; the backend then copies nothing from `.env`, and a missing key is reported as one to
  set in the environment. Production also refuses to start, listing what to change, when the
  settings are development's: the `docker compose` database's password, `LOCAL_ACCOUNT=true`,
  no `STORAGE_URL`, no `APP_URL`, no `SMTP_URL`, or no key for the model. It does not serve the
  interactive API docs at `/docs`.
- `DATABASE_URL` names the database as a SQLAlchemy URL. There is no default: a process that
  is not told its database stops at once and says so. `.env.example` names the
  `docker compose` container, which only this machine can reach; a deployment sets the
  variable in the environment.
- `STORAGE_URL` is where uploaded files and rendered page images are kept: a directory as
  `file:///var/lib/emudoc`, or an S3-compatible bucket as `s3://bucket/prefix`. The default is
  `backend/data` inside the checkout; a deployment names a place of its own. A bucket is reached
  with the standard `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION` and, for
  a service other than AWS, `AWS_ENDPOINT_URL`; the backend checks at start that it can use the
  bucket. Files are always served through the API after its permission checks: from a directory
  the backend sends the file, from a bucket it redirects to a URL signed for 15 minutes, so the
  bucket is never public. `docker compose --profile s3 up -d --wait` runs a local bucket to try
  this with; `docker-compose.yml` lists the settings for it. A checkout with uploads from before
  storage keys (October 2026) keeps them in the old layout: run
  `uv run python -m app.maintenance relocate-files` once in `backend/` with the app stopped (the
  backend logs a reminder while they are there), or `docker compose down -v` and upload again.
- `PAGE_IMAGE_DAYS` is how long a rendered page image or thumbnail is kept after it was
  rendered (30 by default). Pages are rendered from the original the first time they are asked
  for and kept beside it; every few hours the backend removes the ones older than this, and a
  page asked for again is rendered again, so nothing is lost. `0` keeps them for good.
  `uv run python -m app.maintenance sweep-page-images` (in `backend/`) runs the sweep at once,
  say after lowering the setting. The same walk removes what a failed delete left behind: the
  files of documents and projects that no longer exist in the database (files under an hour
  old are left alone, as an upload whose row is not committed yet).
  `uv run python -m app.maintenance sweep-orphans` lists them and `--delete` removes them at
  once. Neither removes anything while the database holds no documents at all, since the
  storage is then more likely another deployment's than all orphans; the message says what to
  check.
- `PARSE_TIMEOUT`, `PARSE_MEMORY_MB`, `PARSE_MAX_PAGES` and `PARSE_MAX_ROWS` bound the parsing
  of an upload: 300 seconds, 2048 MB of address space, 500 pages of a PDF and 100,000 filled rows
  on one sheet by default; `0` removes a limit. The parser runs in a child process of the worker
  that is killed at the time limit and caps its own memory, so a file that is too big fails with
  a message naming the limit, is not retried, and leaves the worker and the other uploads
  untouched. `WORKER_CONCURRENCY` parses run at once, each with its own memory on top of the
  worker's. An image of more than 50 megapixels is refused the same way, by OCR and by the
  thumbnail alike, from its header and before a pixel of it is decoded.
- `AGENT_MODEL` and `INDEX_MODEL` name the AI models as `provider:model`. Both default to
  `google:gemini-2.5-flash-lite`. Indexing is the high-volume side and the agent answers
  questions, so the two can differ, even across providers.
- Each provider reads its own key: `GOOGLE_API_KEY` for `google:` models, `ANTHROPIC_API_KEY`
  for `anthropic:`, `OPENROUTER_API_KEY` for `openrouter:`. To switch models, change these
  lines and restart. [Pydantic AI](https://pydantic.dev/docs/ai/models/overview/) makes the
  requests, so any model it supports can be named.
- `AGENT_EFFORT` and `INDEX_EFFORT` set how hard a model thinks, for models that can.
- `EMBEDDING_MODEL` and `EMBEDDING_DIM` select the embedding model. Changing the model re-embeds
  every document on the next start. Changing the size changes the type of the column the
  vectors live in, which discards them, so the backend refuses to start until
  `uv run python -m app.maintenance rebuild-embeddings` (in `backend/`, with the app stopped)
  has resized the column and queued every document to be embedded again.
- Sign-in: `LOCAL_ACCOUNT=true` lets a request without a session act as a local owner account,
  for development on one machine (off by default); `APP_URL` is the address the links in emails
  point at; `SMTP_URL` sends the verification and reset emails (without it they go to the log);
  `GOOGLE_OAUTH_CLIENT_ID` and `MICROSOFT_OAUTH_CLIENT_ID` with their secrets offer sign-in with
  those providers.

## Accounts

Signing up with an email and a password makes a user and an organisation of their own, which
they own; a verification link goes out by email, and a banner asks for it until it is used.
Sign-in sets a session cookie. The account page (the initials in the header) changes the
password, turns on a code from an authenticator app at sign-in (shown as a QR code to scan, with
recovery codes for a lost phone), lists the browsers and devices that are signed in and signs any
of them out; a forgotten password is reset through a link sent by email, which signs the account
out everywhere. Google and Microsoft accounts sign in through OpenID Connect and are linked to an
existing account only when both sides have verified the address. A visitor without a session is
sent to `/login` and returns to the page they wanted. Every request that changes something must
carry an `X-Requested-With` header, which the app's own requests do and a page on another site
cannot add, so the cookie alone changes nothing; send the header when calling the API by hand.
The routes are under `/api/auth`, the pages in `frontend/src/account/`.

## Tests

```bash
cd backend && uv run pytest          # needs the database container
cd frontend && pnpm lint && pnpm build
```

The backend tests use a separate `emudoc_test` database and make no network calls: the AI
model, the embedding model, the mailer and the identity providers are replaced by stand-ins, and
the requests to Gemini, Claude and OpenRouter are checked through their real SDKs against recorded
responses. The tests empty that database, so they refuse to run against any other, and against a
server that is not on this machine unless `TEST_DATABASE_ALLOW_REMOTE=1` is set; the server comes
from `TEST_DATABASE_SERVER` (default: the `docker compose` container, `emudoc:emudoc@127.0.0.1:5437`).

## Layout

```text
backend/app/
  parsing/      PDF, Word, Excel, CSV, text and Markdown into blocks
  indexing/     structure, enrich and the pipeline
  jobs.py       the job queue: background work as rows the worker claims with leases
  worker.py     the worker that claims and runs the jobs; its own process in production
  deadletters.py
                the jobs the worker gave up on: the operator's digest, listing and requeue
  startup.py    the checks and the services every process starts with
  search.py     hybrid search over passages
  folders.py    the folder tree as paths and subtrees
  agent/        the five tools and the tool-use loop
  accounts.py   who a request acts as; signing up makes a user and their organisation
  auth.py       passwords, sessions, one-time links and authenticator codes
  oidc.py       sign-in with Google and Microsoft
  mail.py       the emails the app sends
  maintenance.py
                the embedding checks at start, the housekeeping and the operator commands
  usage.py      what each organisation used, recorded as it happens, and the daily rollup
  api/          HTTP endpoints
  answers.py    an answer as a job: the worker writes its events, the API streams them
  llm.py        every call to the AI model
backend/migrations/
                the database schema as Alembic migrations, one file per change
frontend/src/
  pages/        the Projects page and the Workspace (two panes)
  workspace/    the library, tabs, document viewers and the chat pane
  account/      sign-in, sign-up, the email links, and the account page
  api.ts        the typed client for the backend
  auth.ts       who is signed in, for the shell and its pages
  chat.ts       a chat's messages as the chat pane holds them, and the answers being followed
```

## Not built yet

- Invitations: an organisation has the one member who created it until the invitation routes
  and the page that accepts them exist; the roles (viewers read, members change, a chat is
  private until shared) are already enforced.
- More file types (slides, email, legacy .doc and .xls), and reading order for multi-column PDFs.
