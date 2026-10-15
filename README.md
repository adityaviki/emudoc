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
and the document says which limit it hit. Before any of that, the upload checks that a file's
bytes are what its name says (a PDF's header, an image's signature, the part a Word document or
a workbook declares, UTF-8 text with no binary in it) and rejects one that is not with the
reason, while the other files of the same upload go in. A Word document or a workbook saved with
a password, or in the format from before 2007 (.doc, .xls), is rejected there with what to do
in Office; a PDF that needs a password to open, or has no pages, goes in and fails to parse with
the reason (one whose password only restricts printing or copying is read like any other). An
empty file is rejected at the upload. The first stage then has the file
scanned for malware by the ClamAV daemon `SCANNER_URL` names, before any parser opens it and
before the file is served to anyone: a flagged file is quarantined, which the document shows
with the scanner's verdict, nothing of it is indexed, and no member can open or download it
until an editor deletes it (or has it scanned again with "Index again", once the signatures
have changed); the worker reports each quarantine to `ALERT_EMAIL`. A daemon that is down
holds the upload in the queue, retried like any passing failure. Without a scanner the stage is
skipped and every document says so, which production accepts only when `SCANNER_URL` is
set to `off` on purpose.

| Stage     | What it does                                                                      | Needs      |
| --------- | --------------------------------------------------------------------------------- | ---------- |
| scan      | Has the file scanned for malware; a flagged file is quarantined, not indexed       | ClamAV     |
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
an answer's model calls and tokens, an upload's bytes and pages, the pages a scan took to read
and the model calls of the indexing stages, the texts embedded, and the pages rendered for the
viewer, each with the organisation, project and member they belong to and the message or
document they were for. Each is counted at the end of the run that made it, so an answer that
was stopped, a scan that failed at its tenth page and a stage a worker lost are counted for
the calls they made. `uv run python -m app.maintenance usage <slug>` (in `backend/`) prints an
organisation's use per day in UTC, by kind and unit, over the last 30 days or `--days`.
An answer shows what it used under its text (the model, its calls and tokens, and the
cost), and a document's header shows what bringing it in and indexing it used, with the
breakdown by stage on hover; the figures are kept on the message and the document as the
rows are recorded, summed over every run. The costs follow `MODEL_PRICES` in `.env`, what
each model costs per million tokens in and out, in `PRICE_CURRENCY`; without prices, no
cost is shown.

A day of model use has two limits, both measured against those costs since midnight UTC: the
organisation's, set by its plan (`backend/app/entitlements.py`: USD 1.00 a day on Free, 10 on
Pro, 50 on Team, none on Enterprise), and the platform's, `DAILY_SPEND_CAP`, the most the
operator will see on the providers' bills in one day. At the plan's limit a question is
refused with 402 and the plan's sentence, and an indexing stage that needs the model (reading
scans, writing summaries and facts) waits for midnight UTC and says so on the document, while
the outline is built without the model so that the document can be searched today; at the
platform's, every question is refused with 503 and a `Retry-After` until midnight, and the
stages wait the same way. A question that was queued before a limit was reached ends with the
sentence as its error. With `ALERT_EMAIL` set, the worker mails a warning once a day when a
day's spending reaches 80 % of a limit. The limits count nothing without `MODEL_PRICES`, and
they hold the next piece of work rather than the ones running, so a day can run past a limit
by what was in flight. The brake for a bill that cannot wait for midnight is
`DISABLED_PROVIDERS`: at the next restart it switches a provider's models off, and the app
runs as it does without a key, saying why. Set a budget at each provider as well (Google Cloud
billing budgets for the Gemini key, the Anthropic console's spend limits, OpenRouter's credit
limit): the app counts what it knows the price of, the provider counts the bill.

Every organisation is on a plan (`backend/app/entitlements.py`): Free, Pro, Team or
Enterprise, each a row of what the organisation may hold (projects, documents, bytes of
documents, members), may use in a calendar month in UTC (pages indexed, questions asked) and
may spend on models in a UTC day, plus what the later features will read from it (API access, connectors, how long data is
kept, the model tier) and how much of the workers its jobs get. A new organisation starts on
Free; the local development account has no limits. The plan is checked where the thing
happens: creating a project, uploading (the request is refused when the plan has no room at
all, a file that would not fit is rejected while the others go in), asking a question, and
in the parse stage once it knows how many pages a document has, which fails the document
with the reason rather than read a page past the month's allowance. A refusal is a 402 with
a sentence that names the plan and the limit, which the app shows as the way to upgrade;
there is no self-serve upgrade yet, so `uv run python -m app.maintenance plan <slug> [plan]`
(in `backend/`) shows an organisation's plan and what it allows, or moves it to another. A
paying plan's jobs also count for less in the claim's share of the workers, so its backlog
is indexed sooner next to a free organisation's.

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
worker inside the API process instead, for a small deployment. Putting it on a server, with
TLS at a reverse proxy in front of uvicorn, is [`deploying.md`](deploying.md).

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
| ClamAV           | Everything; each document says it was not scanned     | Malware scanning of uploads (off by choice only in production) |

The header shows which of the two is available. After adding a key, restart the backend and
press "Run the model on them" in the Documents tab to enrich what was indexed without it.
`DISABLED_PROVIDERS` switches a provider's models off on purpose, with the same effect and the
reason shown in the header, and the same button enriches what was indexed meanwhile once it is
lifted. Running without the model is for development: with `ENVIRONMENT=production` the
backend refuses to start until the key is in the environment, unless the model was switched
off that way.

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
  and asked to wait a moment. It is the same for every organisation; the caps that differ by
  plan are in `backend/app/entitlements.py`.
- `MAX_QUESTION_CHARS` is the longest question, 20,000 characters by default. A question goes
  to the model whole with every turn of its answer, so one that is pasted rather than asked is
  refused with 413 and asked to be shortened; the documents are what to upload.
- `MAX_UPLOAD_MB`, `MAX_UPLOAD_FILES` and `MAX_UPLOAD_REQUEST_MB` bound one upload: 100 MB a
  file, 100 files a request and 500 MB a request by default. A file over its size is rejected
  on its own while the others go in; a request over the other two is refused whole with 413
  and asked to upload in smaller batches. The body has been read by then, so a proxy's limit
  on the request body is the guard against the request itself.
- `RATE_UPLOADS`, `RATE_QUESTIONS` and `RATE_SEARCHES` are how fast one user may go: 20
  upload requests, 20 questions and 120 searches (the search and facts routes) a minute by
  default. `RATE_SIGN_INS` holds the sign-in routes (sign-in, sign-up, the reset and
  verification mails, the authenticator code) to 10 requests a minute per email address, and
  `RATE_SIGN_INS_PER_ADDRESS` to 60 per client address, so that neither a password nor a code
  can be guessed at speed. The request past a rate is refused with 429 and the seconds until
  the minute turns; `0` removes a limit. The counts live in the database, so every API
  replica counts the same.
- `SCANNER_URL` names the ClamAV daemon that scans every upload before it is indexed or
  served: `clamd://host:3310`, or `clamd:///run/clamav/clamd.ctl` for one on the same
  machine. The file is streamed to it, so the daemon needs no access to the storage; its own
  limits (`StreamMaxLength`, `MaxFileSize`, `MaxScanSize` in `clamd.conf`) must allow a file
  of `MAX_UPLOAD_MB`, or it refuses the file and the document says so. Empty, nothing is
  scanned and every document says so; `off` is the same choice stated, the only form
  production accepts besides a daemon. clamd's protocol has no encryption, so production
  takes a daemon on the deployment's own network only. `docker compose --profile clamav up -d
  --wait` runs one to try this with.
- `ALERT_EMAIL` is where the worker reports the jobs it gave up on, through the same mail
  settings as the sign-in links, as one digest every quarter of an hour, where it reports
  the uploads the malware scan quarantined the same way, and where it warns once a day when
  a day's spending on models reaches 80 % of a limit, the platform's or an organisation's.
  Empty sends nothing; the `failed-jobs` command lists the failures either way.
- `DAILY_SPEND_CAP` is what every organisation together may spend on models in one UTC day,
  in `PRICE_CURRENCY` by the costs `MODEL_PRICES` gives; `0`, the default, is no limit. Past
  it every question is refused with 503 until midnight UTC and every indexing stage that needs
  a model waits for it. The limit per organisation is its plan's. Production refuses the cap
  without `MODEL_PRICES`, since it would count nothing.
- `DISABLED_PROVIDERS` is the kill switch: the model providers switched off, by the prefix of a
  `provider:model` name, separated by commas (`google,anthropic`). A model of a provider named
  here cannot be used until the name is removed and the backend and the worker restarted;
  meanwhile the app runs as it does without a key and the header says why. Production starts
  with it, unlike with a missing key.
- `ENVIRONMENT` is `development` (the default) or `production`. In development the backend
  copies the model keys from `.env` into its environment, where the model libraries read them.
  A production deployment injects every secret through the environment or a secret manager
  instead; the backend then copies nothing from `.env`, and a missing key is reported as one to
  set in the environment. Production also refuses to start, listing what to change, when the
  settings are development's: the `docker compose` database's password, `LOCAL_ACCOUNT=true`,
  no `STORAGE_URL`, no `APP_URL`, no `SMTP_URL`, no `SCANNER_URL`, no `HOSTING_PROVIDER`, no
  `OPERATOR_NAME`, `OPERATOR_EMAIL` or `BACKUP_DAYS`, a
  `google:` model without `GOOGLE_PAID_TIER`, a `DAILY_SPEND_CAP` without `MODEL_PRICES`, no
  key for the model, with `DATA_REGION` a model, a bucket or an embeddings endpoint outside
  the region, or a connection that would carry the documents unencrypted off the deployment's
  own network: a database there without `sslmode=verify-full`, a scanner there, or an
  embeddings endpoint, a model's address or a bucket there over plain `http://`
  (`deploying.md`, "Encryption"). It does not serve the interactive API docs at `/docs`, and it marks
  the session cookie `Secure` and sends `Strict-Transport-Security` whatever the request's
  scheme, since a deployment is behind TLS; a request that still reaches it as plain HTTP is
  logged with what the proxy should send.
- `HOSTING_PROVIDER` names who runs the deployment's servers and database, such as
  `Hetzner Online GmbH, Germany`, for the subprocessor list (Accounts, below): the one entry
  the other settings cannot supply. Production refuses to start without it.
- `OPERATOR_NAME` and `OPERATOR_EMAIL` say who runs the deployment (the company with its postal
  address) and where people write about their data, and `BACKUP_DAYS` how long its backups of
  the database and bucket are kept (0 for none): the terms of service and the privacy policy at
  `/terms` and `/privacy` name them (Accounts, below). Production refuses to start without them,
  and with a versioned bucket that keeps a deleted file's earlier versions longer than
  `BACKUP_DAYS` (`deploying.md`, "Backups").
- `DATA_REGION` is the region the deployment keeps its documents in: `eu` (the European Union)
  or `us` (the United States), empty for none. The backend holds to it what it can choose (an
  `openrouter:` model is reached at the region's own address, an `anthropic:` model is told to
  run in the US) and checks at start what it can read: Vertex AI's `GOOGLE_CLOUD_LOCATION`, a
  Bedrock profile or `AWS_DEFAULT_REGION`, OpenAI's European address, the bucket's region and
  the embeddings endpoint. In production it refuses to start while one is outside the region or
  cannot be placed, the Gemini Developer API (`google:`) always among them; in development it
  warns. Where the servers and the database run is the operator's word. Each organisation
  records the region as it is created, the account page shows it, and a deployment that finds
  organisations promised another region refuses to serve them. `deploying.md`, "Keeping the
  data in one region", says where each provider stands.
- `FORWARDED_ALLOW_IPS` is uvicorn's, not the app's: the addresses whose `X-Forwarded-Proto`
  and `X-Forwarded-For` headers it believes, which tell the app the request's scheme and the
  browser's address behind a reverse proxy. `127.0.0.1,::1` by default, right for a proxy on
  the same machine; a proxy elsewhere needs its address or network named, in the environment
  (`deploying.md`).
- `DATABASE_URL` names the database as a SQLAlchemy URL. There is no default: a process that
  is not told its database stops at once and says so. `.env.example` names the
  `docker compose` container, which only this machine can reach; a deployment sets the
  variable in the environment. A database off the deployment's own network is reached with
  `?sslmode=verify-full&sslrootcert=system` on the URL (or the provider's authority's file in
  place of `system`), which production requires.
- `STORAGE_URL` is where uploaded files and rendered page images are kept: a directory as
  `file:///var/lib/emudoc`, or an S3-compatible bucket as `s3://bucket/prefix`. The default is
  `backend/data` inside the checkout; a deployment names a place of its own. A bucket is reached
  with the standard `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION` and, for
  a service other than AWS, `AWS_ENDPOINT_URL`; the backend checks at start that it can use the
  bucket, and logs whether the bucket encrypts what it keeps (its default encryption, which
  every AWS bucket has; a directory is as encrypted as its disk). Files are always served through the API after its permission checks: from a directory
  the backend sends the file, from a bucket it redirects to a URL signed for 15 minutes, so the
  bucket is never public. An original comes as a download, never shown in the browser's own
  viewer, where a PDF could run scripts and submit forms; only an image opens in a tab, and the
  files the backend sends itself carry `Content-Security-Policy: sandbox`, so a browser runs
  nothing in them. `docker compose --profile s3 up -d --wait` runs a local bucket to try
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
  thumbnail alike, from its header and before a pixel of it is decoded. `OCR_MAX_PAGES` (100)
  is how many pages of one document the model may read as scans, each a model call: a
  document with more fails before a page is read, with a message that says to split it; `0`
  removes the limit.
- `AGENT_MODEL` and `INDEX_MODEL` name the AI models as `provider:model`. Both default to
  `google:gemini-2.5-flash-lite`. Indexing is the high-volume side and the agent answers
  questions, so the two can differ, even across providers. The index model is sent the text
  and the scanned pages of every document, the agent each question and the passages it reads,
  so a deployment uses providers whose terms forbid training on them (`deploying.md` says
  where each provider stands). Production refuses `google:` models, the Gemini Developer API,
  until `GOOGLE_PAID_TIER=true` says billing is on for the key's Google Cloud project: on the
  free tier Google may use what it is sent to improve its products, and a free key looks like
  a paid one. `google-cloud:` reaches the same models through Vertex AI. A request through
  OpenRouter is forwarded only to a provider of the model that neither stores it nor trains on
  it.
- Each provider reads its own key: `GOOGLE_API_KEY` for `google:` models, `ANTHROPIC_API_KEY`
  for `anthropic:`, `OPENROUTER_API_KEY` for `openrouter:`; Vertex AI signs in as a service
  account, with `GOOGLE_APPLICATION_CREDENTIALS` naming its key file and `GOOGLE_CLOUD_PROJECT`
  its project. To switch models, change these lines and restart. [Pydantic
  AI](https://pydantic.dev/docs/ai/models/overview/) makes the requests, so any model it
  supports can be named.
- `AGENT_EFFORT` and `INDEX_EFFORT` set how hard a model thinks, for models that can.
- `AGENT_MAX_TURNS` is how many rounds of tool calls one answer may make, 12 by default; an
  answer that needs more stops with what it has and says so. Each round is a model request,
  so this bounds what a question can cost.
- `EMBEDDING_MODEL` and `EMBEDDING_DIM` select the embedding model. Changing the model re-embeds
  every document on the next start. Changing the size changes the type of the column the
  vectors live in, which discards them, so the backend refuses to start until
  `uv run python -m app.maintenance rebuild-embeddings` (in `backend/`, with the app stopped)
  has resized the column and queued every document to be embedded again.
- Sign-in: `LOCAL_ACCOUNT=true` lets a request without a session act as a local owner account,
  for development on one machine (off by default); `APP_URL` is the address the links in emails
  point at; `SMTP_URL` sends the verification and reset emails over TLS to a relay whose
  certificate the machine trusts (without it they go to the log);
  `GOOGLE_OAUTH_CLIENT_ID` and `MICROSOFT_OAUTH_CLIENT_ID` with their secrets offer sign-in with
  those providers.

## Accounts

Signing up with an email and a password makes a user and an organisation of their own, which they
own; a verification link goes out by email, and a banner asks for it until it is used. Sign-in
sets a session cookie, marked `Secure` over HTTPS and always in production. The account page (the
initials in the header) changes the password, turns on a code from an authenticator app at sign-in
(shown as a QR code to scan, with recovery codes for a lost phone), lists the browsers and devices
that are signed in and signs any of them out; a forgotten password is reset through a link sent by
email, which signs the account out everywhere. Google and Microsoft accounts sign in through
OpenID Connect and are linked to an existing account only when both sides have verified the
address. A visitor without a session is sent to `/login` and returns to the page they wanted.
A session the server stops accepting drops everything the tab had fetched, so whoever signs in
next in it starts from nothing. Every request that changes something must carry an `X-Requested-With` header, which the app's own
requests do and a page on another site cannot add, so the cookie alone changes nothing; send the
header when calling the API by hand. The app answers no request from another origin (there is
no CORS): the frontend is served from the API's own origin, and the defence rests on that. The
routes are under `/api/auth`, the pages in `frontend/src/account/`.

Signing up takes a ticked box that accepts the terms of service and the privacy policy, which
anyone can read at `/terms` and `/privacy` (the sign-in pages and the user menu link to them).
The backend fills both in from its settings and constants (`backend/app/legal/`, `GET
/api/legal`): who runs the deployment, where the documents are kept, how long each thing is
kept on each plan, the two cookies, whether a model provider may learn from what it is sent,
whether uploads are scanned. The account records the edition it accepted (`TERMS_VERSION`) and
when, and the audit log records the acceptance. An account made through Google or Microsoft,
or one that accepted an older edition, sees only a page that asks for the current one, with a
copy of its data and its deletion as the other ways out; the API refuses it everything else
with 403. The app sets no analytics or advertising cookies and loads nothing from other sites,
so it shows no cookie banner. The chat pane says that answers are written by an AI model and
can be wrong, and a link in an answer shows its address without leading anywhere, since a
document can steer what the model writes.

The account page also downloads "your data": one JSON file with everything kept about the user
as a person (the account, how it signs in, its organisations, the sessions of the last month,
the chats it started with every message, the usage counted to it, and the audit log's rows that
name it), without the password
hash, the authenticator secret or the organisation's documents (`backend/app/export.py`, `GET
/api/auth/account/export`). Deleting the account takes the password, or the address typed out
for an account that signs in only through Google or Microsoft. It signs out every other browser,
mails a notice, and leaves 14 days in which the account can do nothing but be kept, exported or
signed out of: signing in shows the day and a "Keep my account" button. Then the worker's
housekeeping purges it, with every organisation it was the only member of and everything in it,
rows and files; in an organisation with other members only the user's membership and chats go,
and the last owner of one is refused until another member is an owner (`backend/app/retention.py`).
Each purge leaves a record under `erasures/` in the storage (the account's id and creation time,
nothing else), so an account that a restored backup brings back is purged again at the next round.
After the database is restored to an earlier point, `python -m app.maintenance after-restore
<time>` brings back from a versioned bucket the files deleted since, moves the ids past every id
the storage holds, signs everyone out and records the restore in each organisation's audit log
(`backend/app/restore.py`); until it has run, an upload refuses a place in the storage that a
lost document's files still hold. With `--dry-run` it changes nothing and fails when the storage
lacks a document's original at that point: `deploy/restore-drill.sh` restores the newest backup
into a throwaway copy and runs it against the live storage, the monthly restore drill
(`deploying.md`, "The drill").
A chat is kept for the plan's retention after its last question (30 days on Free, a year on
Pro, for good on Team and Enterprise), which the chat list and the account page say; sessions
and one-time links are removed a month after they ended.

The log holds no document text, question, answer or key (`backend/app/logs.py`): a traceback
keeps its frames and the kinds of its exceptions but not their messages unless the app wrote
them, a database error its SQLSTATE and names rather than the row it refused, a provider's error
its status and codes rather than its words (members see the same), and the access log a query's
parameter names without their values, as does the line httpx writes for each request the app
sends, without the user and password an address may carry. A failed parse names the document
by its id. The links in emails carry their token after `#`, which
no browser sends to a server, and pages send no `Referer`. A refused request (422) says where
and why, never with the value it refused, so a short password or a link's token does not come
back in the response. `deploying.md` ("Logs") gives the
proxy a log format to match.

Each organisation keeps an audit log (`backend/app/audit.py`): its members' sign-ins (and
failed ones for their accounts), password, two-factor and session changes, exports and
deletion requests, every upload, download and deletion of a document, folder or project, chats
shared, taken private or deleted, and what the operator (a plan change, a requeue) and the app
(a quarantine, an account purge with the owner it promoted) did on their own. A row names the
member, the project and the object, the client's address, and names or settings as they were
(a file's name, a role before and after), never what a document, a question or a chat's title
says. Its owners and admins read it page by page through `GET /api/audit`; the view with
filters is still to come. A row is kept 90 days on Free, a year on Pro and Team, and for good
on Enterprise. A purged account's rows stay with an organisation that keeps other members,
without their addresses, and the account export includes the rows that name its user.

`/subprocessors` lists the companies the deployment sends data to, what for and what each
receives, for anyone to read before signing up (the sign-up page and the user menu link to it).
The backend reads it from its settings (`backend/app/subprocessors.py`, `GET
/api/subprocessors`), so it changes when a provider does: `HOSTING_PROVIDER`, the bucket, the
companies behind the two models, a hosted embeddings endpoint, the mail relay, and Google and
Microsoft when sign-in with them is set up. A service on the deployment's own network is not a
subprocessor and is left out; a host the backend does not recognise is listed by its name.
Each entry says where the company handles what it receives when the settings show it
(`backend/app/residency.py`), and with `DATA_REGION` the page opens with the region the
documents are kept in.

Every response carries the headers that tell the browser what a page of the app may do
(`backend/app/headers.py`): a `Content-Security-Policy` that allows scripts, styles and requests
from the app's own origin only, and images from the storage bucket besides, so an answer cannot
load an image from an address a document planted in it; `frame-ancestors 'none'` and
`X-Frame-Options`, so no other site can show the app in a frame; `X-Content-Type-Options:
nosniff`; `Referrer-Policy: no-referrer`; and `Strict-Transport-Security`
over HTTPS and always in production. Every answer of the API, the files and page images
included, carries `Cache-Control: no-store`, and signing out sends `Clear-Site-Data: "cache"`,
so a shared computer keeps nothing of the account's chats or documents. The policy allows no inline code, which is why the one
script `index.html` runs before the app, the saved theme, is the file `frontend/public/theme.js`.
The development server on :5173 sends none of this; the build served on :8000 does.

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
  scanning.py   the malware scan of uploads: the clamd client, the quarantine, the digest
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
  restore.py    what the app sets right after its database is restored to an earlier point
  usage.py      what each organisation used, recorded as it happens, and the daily rollup
  entitlements.py
                what each plan allows, and the checks the routes and the parse stage make
  api/          HTTP endpoints
  headers.py    the headers every response carries for the browser
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
deploy/
  restore-drill.sh
                the restore drill: the newest backup into a throwaway copy, checked against the
                live storage
```

## Not built yet

- Invitations: an organisation has the one member who created it until the invitation routes
  and the page that accepts them exist; the roles (viewers read, members change, a chat is
  private until shared) are already enforced. The plan's member cap (`check_member` in
  `backend/app/entitlements.py`) waits for them.
- Billing: plans exist and are enforced, but nothing sells them. The `plan` command moves an
  organisation between them; the API keys, the connectors and the model tier the plans
  declare are not read by anything yet.
- A trash: deleting a project, folder, document or chat is immediate and permanent.
- More file types (slides, email, legacy .doc and .xls), and reading order for multi-column PDFs.
