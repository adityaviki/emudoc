# How Emudoc works

A map of the code for someone about to change it: the processes, how a document becomes
something the agent can search, how an answer is written, and where each part lives. The
self-hosting guide (`deploying.md`) says how to run it; `docs/development.md` how to work on
it. Each module starts with a docstring that says what it holds and why it is built that way:
read it before changing the module.

## The processes

```text
             browser
                │  HTTPS
         reverse proxy (Caddy, nginx)
                │
   ┌────────── API ───────────┐        uvicorn app.main:app: /api, and the built frontend
   │            │             │
   │        Postgres ──── worker(s)    python -m app.worker: indexing, answers, mail, housekeeping
   │     (pgvector, the       │
   │      job queue)          ├──> model providers (Gemini, Claude, OpenAI, …)
   │                          ├──> embeddings server (llama.cpp, EmbeddingGemma)
   └──── storage ─────────────┤──> ClamAV (optional), the converter (optional)
     (a directory or a bucket)└──> mail relay (optional)
```

- **The API** (`backend/app/main.py`, routes in `backend/app/api/`) answers the page and
  programs. It does no long work itself: anything that takes more than a moment is a job.
- **The worker** (`backend/app/worker.py`) claims jobs from Postgres and runs them in threads:
  indexing documents, answering questions, sending mail, delivering webhooks, syncing connected
  folders, the housekeeping. In development the API runs it in its own process
  (`RUN_WORKER`); in production it is a process of its own, as many as the backlog needs.
- **Postgres 17 with pgvector** holds everything but the files: the data, the job queue, the
  passages' vectors and the search index.
- **Storage** (`backend/app/storage.py`) holds the uploaded files and the pages drawn from them,
  in a directory or an S3-compatible bucket, under keys the database names.
- Around them, each optional: the **embeddings server**, for search by meaning; **ClamAV**,
  which scans uploads; **the converter** (`python -m app.converter`, LibreOffice in a container
  of its own), which turns Office files into the PDF they are shown as; a **mail relay**.

Every process reads the same settings (`backend/app/config.py`), from the environment, and
checks them at start (`backend/app/startup.py`): a production process refuses development's
conveniences and says which.

## Jobs

`backend/app/jobs.py`. A job is a row of the `jobs` table. A worker claims one with `FOR UPDATE
SKIP LOCKED` and holds a lease on it, which it extends while it works; a job whose worker died
is claimed again once its lease runs out, and a worker that lost its lease stops at its next
commit with nothing written. A job that failed for a reason that passes (a provider down, a
rate limit) is tried again after 1, 2, 4 and 8 minutes; one that cannot succeed (a rejected key,
an unreadable file) fails at once. The worker keeps what it gave up on as failed
(`backend/app/deadletters.py`), which the Administration page lists and queues again. Answers
and indexing run in lanes of their own, so a backlog of uploads never holds up an answer.

## From an upload to an index

```text
                                         ┌──> passages (search index) ───┐
upload ──> scan ──> parse ──> structure ─┼──> the map (outline, summaries) ┼──> the agent's tools
                                         └──> facts (fact table) ─────────┘
```

An upload (`backend/app/api/documents.py`, `backend/app/uploads.py`) checks that each file's
bytes are what its name says, unpacks an email or a ZIP archive into documents
(`backend/app/unpacking.py`), stores the file and queues its indexing. The pipeline
(`backend/app/indexing/pipeline.py`) runs five stages, each storing its output so that any
stage can be run again on its own:

| Stage | What it does | Needs |
| --- | --- | --- |
| scan | has the file scanned for malware; a flagged file is quarantined (`scanning.py`) | ClamAV |
| parse | turns the file into ordered blocks (headings, text, tables) with their page and position (`parsing/`) | nothing |
| structure | builds the outline from the headings and cuts the sections into passages (`indexing/`) | nothing |
| enrich | writes a summary of each section and of the document, and reads its facts (`indexing/`, `locales.py`) | the model |
| embed | turns each passage into a vector (`embeddings.py`) | the embeddings server |

Scan, parse and structure are required; enrich and embed are optional, and a document indexed
without them is "Partly indexed": found by its words, finished later by "Index the rest". The
parser runs in a child process with a time and a memory limit, so a hostile or huge file stops
that process alone; a long PDF is read in parts. Scanned pages and images are transcribed by the
indexing model. Pages are drawn by pdfium in child processes of their own
(`backend/app/rendering.py`), a few ahead and the rest as they are opened.

The stages build three indexes:

- **Passages**, searched by `backend/app/search.py`: Postgres full-text search and pgvector's
  nearest neighbours, merged by reciprocal rank fusion, each document in the language it is
  written in (`backend/app/languages.py`).
- **The map**: each document's outline, with a summary of every section.
- **Facts**: typed values (text, numbers, dates, amounts) read from every document, its
  parties, identifiers, dates, amounts and terms, each with the words it was read from and their
  passage, its dates and amounts read the way its language writes them
  (`backend/app/locales.py`); the agent's `query_facts` filters and compares them across the
  project's documents.

## The agent and its answers

The agent (`backend/app/agent/`) answers with five read-only tools over the indexes:
`list_documents` and `get_outline` read the map, `search` the passages, `query_facts` the facts,
and `read` returns a passage's exact text. Every member is also offered the tools of their own
My files (`agent/writing.py`: `list_my_files`, `read_file`, `create_document`,
`edit_document`, `create_spreadsheet`, `edit_spreadsheet`, `copy_to_my_files`; Word documents by python-docx in `word_files.py`, workbooks by openpyxl in
`sheet_files.py`, their formulas' values worked out by the converter), each write a version only
they see. Every tool is scoped to the chat's project, and to its folder or documents, by the
server, never by the model.

An answer cites passages as `[P<id>]`, which the page turns into links that open the source.
What the tools return reaches the model inside tags its prompt names as data, never as
instructions, so a document that tells the agent what to do is reported, not obeyed
(`python -m app.maintenance injection` tries it).

An answer is a job (`backend/app/answers.py`): the worker writes its events to the
`chat_events` table as the model streams, and the API streams them to the page as server-sent
events, so an answer goes on when the page or the API that served it goes away, and the page
picks it up again from where it was. Every call to a model goes through `backend/app/llm.py`,
which falls back to another provider's model when the first cannot answer, counts the tokens and
their cost (`usage.py`, `costs.py`), and stops at the day's spending cap (`spending.py`).

## Who may do what

An install holds one workspace (an organisation in the code: `org_id` is on every table and in
every query's scope). Its members have a role, viewer, member, admin or owner
(`backend/app/access.py`), and a project may be private to the members chosen for it. A request
acts as the user its session cookie names (`backend/app/auth.py`, `api/deps.py`'s
`get_account`), or as the member whose API key it carries (`Authorization: Bearer emk_…`,
`apikeys.py`), which the versioned API under `/api/v1` takes (`backend/app/api/v1/`, held to its
published contract, `openapi.json` beside it). Every request that changes something and carries
a cookie must carry an `X-Requested-With` header, the defence against cross-site requests. What
the members do that matters is written in the audit log (`backend/app/audit.py`).

The secrets the database keeps (authenticators, connectors' tokens, webhooks' secrets) are
encrypted with keys it never holds (`ENCRYPTION_KEYS`, `keyring.py`, `encrypted.py`).

## The frontend

`frontend/`: React 19, Vite and Tailwind 4, with TanStack Query for what the server holds.
`src/api.ts` is the typed client; `src/App.tsx` the routes, every page but the sign-in pages
inside the `Shell`, which sends a visitor without a session to sign in. A project's page is the
workspace (`src/pages/Workspace.tsx`, `src/workspace/`): the library on the left, the documents'
tabs and the chat on the right. In production the API serves the built page from its own
origin; in development Vite serves it and passes `/api` on.

## Where things are

```text
backend/app/
  main.py           the app: the routes, the headers, the start and the stop
  api/              the routes the page calls; api/v1/ the versioned API for programs
  config.py         every setting; startup.py the checks a process starts with
  models.py         the database's tables; migrations/ (beside app/) their Alembic migrations
  jobs.py           the job queue; worker.py the worker that runs it
  uploads.py        an upload's checks; unpacking.py emails and archives opened
  parsing/          each file type into blocks; scanning.py the malware scan
  indexing/         the pipeline: structure, enrich and embed
  rendering.py      pages drawn from PDFs; conversion.py and converter.py Office files as PDFs
  search.py         the hybrid search; languages.py the language a document is searched in
  my_files.py, share_links.py, …
                    one module per feature, with its routes in api/
  agent/            the tools and the loop; llm.py every call to a model
  answers.py        an answer as a job and its stream
  auth.py, accounts.py, access.py, apikeys.py, oidc.py
                    sign-in, who a request acts as, roles, API keys, Google and Microsoft
  mail.py, postbox.py, notices.py, inbound.py
                    mail out through the queue, the members' notices, mail in
  connectors/       Google Drive, Dropbox and OneDrive folders synced into a project
  webhooks.py       webhooks and their deliveries
  storage.py        the files; backup.py and restore.py the backups
  metrics.py, logs.py, tracing.py, errors.py, watch.py
                    metrics, logs, a request's trace through its jobs, error tracking, alerts
  maintenance.py    the admin's commands: python -m app.maintenance --help
  help/             the help pages members read in the app
  sample/           the sample project a new workspace starts with
  client/           the upload client members download and run
backend/tests/      the suite (pytest), a database per worker; tests/corpus the parsers' snapshots
backend/loadtest/   the load test
frontend/src/       the page; frontend/e2e/ the browser test and its stand-in model
deploy/             compose.yml and .env.example for a team's server, the restore drills,
                    prometheus/ the example alerting rules
Dockerfile          the one image for every process
.github/            CI, the image's publishing, the weekly audit, Dependabot, main's rules
```
