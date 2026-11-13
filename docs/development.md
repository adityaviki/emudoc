# Working on Emudoc

How to run Emudoc from a checkout, and the tests that hold it. `CONTRIBUTING.md` says how a
change gets in; `docs/architecture.md` where things are.

## Running it

You need Docker, [uv](https://docs.astral.sh/uv/) and [pnpm](https://pnpm.io) with Node 24.

```bash
docker compose up -d --wait   # Postgres with pgvector, on 127.0.0.1:5437
cp .env.example .env          # then add a model's key, GOOGLE_API_KEY by default
./dev.sh                      # the backend on :8000 and the frontend on :5173
```

Open <http://localhost:5173>. A new database opens the setup page, which makes your account and
the workspace. `dev.sh` starts the database, installs what the frontend's lock file asks for,
applies the migrations, and runs the backend (restarting when its Python changes) and Vite's dev
server (updating the open page as you edit), which passes `/api` on to the backend. Ctrl+C stops
both. Port 8000 serves the last `pnpm build`, not your edits. `dev.sh`, the browser test and the
load test take fixed ports, so they run in one checkout at a time.

In development the backend runs the worker's threads in its own process (`RUN_WORKER`), so one
process indexes what it serves. `LOCAL_ACCOUNT=true` in `.env` skips signing in: every request
acts as a local owner. Without a model key the app still parses, searches and shows documents,
and says what it cannot do (the README's "What works without what").

For search by meaning, run [Ollama](https://ollama.com) with `ollama pull embeddinggemma`, at
the `EMBEDDING_BASE_URL` `.env.example` names; its vectors are those of the llama.cpp server a
team runs. The development compose file runs more beside the database behind profiles:
`clamav` (the scanner), `converter` (Office files shown as they look), `s3` (a bucket) and
`pgbouncer` (a pooler), each started with `docker compose --profile <name> up -d --wait` and
named in `.env` as `.env.example` says.

Settings are read once at start: restart the backend after changing `.env`. `.env.example`
lists every setting with its default, and `deploying.md`'s "Every setting" says what each does.

### The database

The schema is a set of Alembic migrations in `backend/migrations/`. The app never changes the
database itself: at start it checks that the schema is current and refuses to serve otherwise,
naming the command. After a change to `backend/app/models.py`:

```bash
uv --directory backend run alembic revision --autogenerate -m "Add x"
uv --directory backend run alembic upgrade head
uv --directory backend run alembic check     # the models and the database agree
```

### The image

```bash
docker build -t emudoc:$(git rev-parse HEAD) --build-arg VERSION=$(git rev-parse HEAD) .
.github/scripts/check-image.sh emudoc:$(git rev-parse HEAD)   # runs it as a team does, ~1 min
```

One image for the API, the worker, the migrations and the admin's commands, with the frontend
built in, named by the commit it was built from (`/api/health` and the user menu show it; a
checkout says `development`). `deploying.md`, "The image", says what a container needs.

## The tests

```bash
uv --directory backend run pytest -q -n auto      # the backend's suite; needs the database container
uv --directory backend run pytest -q tests/test_x.py
uv --directory backend run pytest -q --shard 1/3  # a third, as each of CI's backend jobs runs
pnpm --dir frontend lint && pnpm --dir frontend test && pnpm --dir frontend build
pnpm --dir frontend size                          # after the build: the page's scripts against their budget
pnpm --dir frontend e2e                           # the browser test, after the build
```

**The backend's suite** (pytest) runs against databases of its own, `emudoc_test` and one more
for each of `-n`'s workers, which it empties, so it refuses any other database, and a server
off this machine unless `TEST_DATABASE_ALLOW_REMOTE=1`; the server is `TEST_DATABASE_SERVER`,
the compose container by default. It makes no network call: the models, the embeddings, the
mailer and the identity providers are stand-ins, and the requests to Gemini, Claude and
OpenRouter go through their real SDKs against recorded responses.

**The parse corpus** (`backend/tests/corpus`) holds each parser to a snapshot of what it reads
in each of a set of files: the sample project's, and at least one of each kind the app reads.
A change to a parser, or to a library it reads with, that reads a file differently fails the
suite. A change meant to do that writes the snapshots again, and the diff shows what each file
now reads as:

```bash
uv --directory backend run python -m tests.corpus
```

**The frontend's unit tests** (Vitest) sit beside the modules they test, `src/**/*.test.ts`,
and run in Node.

**The browser test** (Playwright, `frontend/e2e/`) drives the built page in Chromium as a new
install's admin and their colleague through most of the app, from the setup page to an answer's
citation, checking each kind of page, panel and dialog against axe-core's rules for WCAG 2.2 AA
in both themes. It starts the app itself (`e2e/serve.sh`, in a database made afresh each run)
against a stand-in for the model and the embeddings (`e2e/standin.py`), which answers the same
way every run and needs no key. Chromium comes once with `pnpm --dir frontend exec playwright
install chromium`.

**The load test** (`uv --directory backend run python -m loadtest`, ~12 minutes) runs the API
and a worker as production does, against the same stand-in, for a member who uploads 3,000
documents, searches them, asks 20 questions at once and uploads a 300-page scan and large
workbooks, and prints what the member waited. `docs/load.md` has the last figures and what they
mean.

**The models** are asked by hand, with real keys, since the suite uses recorded answers:

```bash
uv --directory backend run python -m app.maintenance models          # every model and fallback, once each
uv --directory backend run python -m app.maintenance golden          # thirteen questions about the sample, graded
uv --directory backend run python -m app.maintenance fact-accuracy   # the sample's facts against labelled values
uv --directory backend run python -m app.maintenance injection       # documents with planted instructions
```

Run them after a change to the prompts, the agent, the sample or the models. Each costs a few
answers' worth of tokens. The reports name the prompts' versions they ran.

**A change to `/api/v1`** writes its published contract again, and fails on a change that would
break a program built on it:

```bash
uv --directory backend run python -m app.api.v1.contract
```

**The upload client** (`backend/app/client/`) promises Python 3.9 and the standard library
alone, and its tests run it by that Python:

```bash
UPLOAD_CLIENT_PYTHON=$(uv python find --no-project 3.9) uv --directory backend run pytest -q tests/test_upload_client.py
```

## CI

`.github/workflows/ci.yml` runs every check on every pull request and every push to `main`, on
GitHub's runners, without a secret, so a pull request from a fork runs them all: the backend's
lint, format and suite in three parts; the frontend's lint, unit tests, build and size; the
browser test; the load test at a size that takes a minute; the image, built and run by
`.github/scripts/check-image.sh`; gitleaks over every commit; shellcheck, actionlint, promtool
on the example alerting rules, trivy on the Dockerfile and the compose files, and the tools'
versions (`.github/scripts/check-versions.sh`). `pooler.yml` runs the suite through pgbouncer
when what a pooler sees changes, and `embeddings.yml` checks the embeddings server's vectors
when it changes. `audit.yml` audits the dependencies and the images for known vulnerabilities
every Monday and when a manifest, a lock file or an image changes, and Dependabot proposes the
updates weekly.

The pre-commit hook in `.githooks` runs gitleaks on what is staged, so a key never reaches a
commit. It needs Docker, and is turned on once per clone:

```bash
git config core.hooksPath .githooks
```
