# Deploying Emudoc

How to run the app on a server. The README's "Run it" is the development path, one `uvicorn`
on one machine; this is what changes for a deployment. The processes below run from the image
("The image") or from a checkout with `uv`, as in development: the commands are the same, with
`uv run` in `backend/` before each in a checkout.

## The pieces

- **Postgres 17 with pgvector 0.8 or later**, named in `DATABASE_URL`, the compose file's
  own ("The image"), managed, or with a standby of the deployment's own, on no public address, its role given a statement and an
  idle timeout, and a pooler in front when the replicas need more connections than it takes:
  "The database", below. The schema is applied as a release step, before the new version
  starts: `alembic upgrade head`. A process that finds the database behind its migrations
  refuses to serve and says so. A database off the deployment's own network, such as a
  managed one, is reached with its certificate checked: "Encryption", below.
- **Storage** for uploads and page images, named in `STORAGE_URL`: a directory the API and
  every worker can reach, or an S3-compatible bucket with the `AWS_*` variables.
- **The API**: `uvicorn app.main:app`, serving `/api` and the built frontend, which the image
  holds (in a checkout, `pnpm build` in `frontend/` before it starts). A reverse proxy in front
  of it ends TLS, below.
- **The worker**: `python -m app.worker`, as many as the backlog needs. It reads the same
  settings as the API and refuses the same misconfigurations.
- **A reverse proxy** that holds the certificate and forwards to the API: Caddy, which the
  compose file runs with its `tls` profile, or nginx, below.
- **ClamAV**, named in `SCANNER_URL`, which scans every upload before a worker parses it and
  before the API serves it to the other members of a project. The `clamav/clamav` image with
  the signatures on a volume is enough: the workers stream each file to the daemon over its
  port, so it needs no access to the storage and can run on any machine the workers reach,
  one for all of them. It wants about a gigabyte of memory for the signatures and a minute to
  load them at start, during which the workers hold uploads in the queue and try again. Its
  own limits must allow a file as large as `MAX_UPLOAD_MB`: `StreamMaxLength`, `MaxFileSize`
  and `MaxScanSize` in `clamd.conf` (the compose file's `clamav` service sets them through
  the image's `CLAMD_CONF_*` variables; an older image takes a mounted `clamd.conf`). It is
  the team's choice: a deployment runs without one, `SCANNER_URL` empty or `off`, every
  document saying it was not scanned and every start warning.
- **The converter**, named in `CONVERTER_URL`, which turns each Word, PowerPoint, Excel, RTF and
  HTML upload into the PDF the document pane shows it by, as it looks: the image run as
  `python -m app.converter`, a container of its own that holds no settings and reaches nothing
  but the workers. Without it those documents are shown by their text: "The converter", below.
- **An embeddings endpoint** in `EMBEDDING_BASE_URL`, which turns every passage and every
  search into a vector for search by meaning: llama.cpp's server beside the app, as the
  compose file runs it, or a hosted one: "The embeddings endpoint", below.
- **A mail relay**, if the team wants mail: an SMTP relay in `SMTP_URL` (SES's on AWS), or
  Resend through its API with `RESEND_API_KEY`, with the sending domain's SPF, DKIM and DMARC
  records in its DNS, and the provider's news of what bounced reaching the app: "Mail", below.
  The workers send the mail, so a relay that is down holds the links back while they try again
  for half an hour; the form answers as usual, and the worker's log names the failure. Without
  one nothing is mailed: an owner or admin hands invitations and password-reset links on, the
  notices are told in the app alone, and every start says so ("The providers", under "Mail").
- **The model providers** named by `INDEX_MODEL` and `AGENT_MODEL`, and by their fallbacks,
  each on terms that forbid training on what it is sent, each model named by its version and
  tried with `python -m app.maintenance models`: "The models" and "What leaves the deployment",
  below.
- **Backups** of the database and of the files, encrypted and kept for `BACKUP_DAYS`, which
  restore to the same point: "Backups", below.
- **Logs kept for a period the team chooses** and no longer: the app's, the proxy's and the
  database's, by whatever collects them (journald deletes by age, Docker's own drivers do not):
  "How long they are kept", below.
- **A scraper for the metrics**, if the deployment keeps them: Prometheus or anything that reads
  its format, asking the API and every worker on a port of their own, with Alertmanager for the
  rules in `deploy/prometheus` and a dead man's switch for its watchdog: "Metrics" and
  "Alerts", below. On AWS the instance runs both.
- **An address for the alerts**, `ALERT_EMAIL`, where the worker reports what it gave up on and
  what it sees go wrong, through the same relay: "Alerts", below.

`deploy/aws` builds all of it on AWS, staging and production alike: "On AWS", below.

The settings arrive through the process environment, from the orchestrator (the compose file
passes its `.env` to the containers as Compose's `env_file`) or a secret manager: the app reads
no `.env` file itself, since `ENVIRONMENT=production` makes it copy nothing from one, and makes
it refuse to start with
development's conveniences (the compose database's password, `LOCAL_ACCOUNT=true`, no
`STORAGE_URL`, `APP_URL`, `ENCRYPTION_KEYS` or `EMBEDDING_BASE_URL`, no `AGENT_MODEL` or
`INDEX_MODEL` or one its provider moves, no key for a model or its fallback, an embeddings
endpoint that turns its requests down, and a connection that would carry the documents
unencrypted off the deployment's own network), naming each. What a team may run without, a
mail relay, a scanner, and the word that a Gemini key is on the paid tier
(`GOOGLE_PAID_TIER`), it refuses nowhere: each start warns about each one left out.
`.env.example` documents every setting; the README's "Configuration" says what the ones that
matter do.

## The image

`Dockerfile` builds one image for every process, for amd64 and arm64: the API is its default
command, and the worker, the release step and the maintenance commands are the others. A version
tag (`v0.1.0`) publishes it to GHCR as `ghcr.io/emudocai/emudoc` (`.github/workflows/publish.yml`),
tagged with the version (`0.1.0`), its line (`0.1`), `latest` and the commit's full SHA, the
version being what `/api/health` reports, the user menu shows and each process logs as it starts
("Starting Emudoc 0.1.0"). Each platform's image is run as below, arm64 under emulation, and
scanned before anything is pushed. Built from a checkout instead, it is tagged with the commit's
full SHA and told it as `VERSION`; an image built without it says `unknown`, a checkout
`development`:

```bash
commit=$(git rev-parse HEAD)
docker build -t emudoc:$commit --build-arg VERSION=$commit .
```

`.github/scripts/check-image.sh emudoc:<tag>` runs it as a team does: `deploy/compose.yml` with a
`.env` made from `deploy/.env.example` as the README's quick start makes one, so in production,
in a Compose project of its own on a network and a port Docker picks. It checks the probes, the
version, the page and the files the page names, that the API started beside the embeddings
server, the metrics of the API and the worker, that the converter turns a Word file into a PDF
for the worker, and that Caddy takes its configuration. It needs the network the first time, for
the images and the embedding model's weights, which it keeps in a volume. CI does the same for
every commit on `main`.

It holds Python 3.13 on Debian's slim image with the backend's locked dependencies (`uv sync
--frozen --no-dev`, compiled ahead), the app and its migrations, and the frontend's build, under
`/app` as in a checkout, so the API serves the frontend from its own origin as "TLS at a reverse
proxy" asks. It holds no `.env`, no uploads, no tests, and neither uv nor Node: `.dockerignore`
names what goes into the build and leaves out everything else. Nor pip, which the base brings
and the build removes, since nothing in the image installs anything. It runs as `emudoc`, uid
and gid 10001, with `ENVIRONMENT=production` set, so a container started without its settings
refuses to run rather than running as development.

The commands:

```bash
alembic upgrade head                    # the release step, once per release
uvicorn app.main:app --host 0.0.0.0 --port 8000 --timeout-graceful-shutdown 8
                                        # the API, the image's default
python -m app.worker                    # a worker, as many as the backlog needs
python -m app.maintenance failed-jobs   # the operator's commands, as in a checkout
```

The release step runs to its end before a container of the new image serves or works, since
the API and the worker refuse a database behind their migrations and never upgrade one
themselves. A container needs:

- **Its settings in the environment**, from the orchestrator's secrets or an env file
  (`docker run --env-file`, Compose's `env_file`), never baked into an image.
- **A read-only filesystem with a tmpfs at `/tmp`.** Nothing in the image is written to, but
  every upload passes through the temporary directory, and a worker copies an original there
  to scan, parse and render it: size it for what the API takes in at once
  (`MAX_UPLOAD_REQUEST_MB`, and `MAX_UPLOAD_MB` again, per upload at a time) with, from a
  bucket, the originals it keeps for drawing pages (`RENDER_CACHE_MB`, "Drawing pages"), and
  for what a worker works on (`MAX_UPLOAD_MB` per `WORKER_CONCURRENCY`, 1.6 GB at the
  defaults). A tmpfs
  counts as the container's memory and keeps the documents in the clear ("Encryption", below).
  A process that cannot write a temporary file refuses to start: "No temporary directory can be
  written in …".
- **A volume for a directory storage**: `STORAGE_URL=file:///var/lib/emudoc`, one volume shared
  by the API and every worker. A named volume takes the directory's owner and mode (10001,
  `0750`) from the image; a directory of the host's needs `chown 10001:10001`. A bucket needs
  none.
- **The proxy's address.** The API listens on every address of its container, published on
  the host's loopback (`127.0.0.1:8000:8000`) for a proxy there. Docker passes those
  connections on from the gateway of the container's network, not from 127.0.0.1, so
  `FORWARDED_ALLOW_IPS` names the gateway; without it every session shows the gateway's address
  and production logs that requests reach the app as plain HTTP.
- **Time to stop.** Docker sends SIGTERM and kills the process 10 seconds later
  (`stop_grace_period`, `docker stop -t`). The API stops taking connections at once and ends
  the answer streams it serves, which would otherwise last until their answers are written:
  the page follows each answer again, from where it was, from the next API it reaches, and the
  worker goes on writing it meanwhile. The other requests in progress get 8 seconds
  (`--timeout-graceful-shutdown` in the image's command) before they are cut. The worker gives
  its running jobs 8 (`--grace`) and hands the rest back to the queue, where the next worker
  starts them over. Either number goes up with the grace period above it, and an API that
  runs the worker too (`RUN_WORKER=true`) needs both its 8 and the worker's 8 within it. On one
  machine the API is away from its stop to the new one's start, a few seconds in which the
  proxy answers 502: the page tries the answer again five times over 15 seconds before it
  says to reload.
- **A private authority, if the database's or the mail relay's certificate comes from one**:
  an image of the deployment's own `FROM` this one that copies it into
  `/usr/local/share/ca-certificates/` and runs `update-ca-certificates` as root. The image sets
  `SSL_CERT_FILE` to the system's bundle, without which the database's driver finds no
  authority at all ("Encryption", below).

The image has no curl, so a health check runs Python, as the compose file below does: the
API's asks `/api/health`, the worker's reads the age of the file it keeps with `--alive-file`
("Health checks", below).

Its bases are pinned by digest (`python:3.13-slim-trixie@sha256:…` and `node:24-slim@sha256:…`
in the `Dockerfile`, and uv's), as are the images beside it in the compose files (Postgres,
ClamAV, llama.cpp's server, Caddy), so a commit builds the same image whenever it is built and a tag
moved on a registry changes nothing until a commit does. A security fix in Debian therefore
reaches a deployment as a commit and a release. Dependabot proposes the digest a base was
rebuilt with every Monday (`.github/dependabot.yml`), and the Audit workflow builds the image
every Monday morning and fails on a high or critical vulnerability that has a fix (trivy), which is the
sign to take that pull request, or to move the digest by hand (`docker buildx imagetools inspect
python:3.13-slim-trixie` prints the tag's), and to release the new commit. The same scan by
hand, of an image built here, with what was accepted in `.trivyignore.yaml`:

```bash
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock -v "$PWD:/src:ro" -w /src \
  aquasec/trivy:0.73.0 image --scanners vuln,secret --severity HIGH,CRITICAL --ignore-unfixed \
  --ignorefile .trivyignore.yaml emudoc:<tag>
```

The images that run beside it, Postgres's, ClamAV's, llama.cpp's and Caddy's, get the same scan every
morning (Audit's "Images beside the app"), each at the digest the compose files pin, read from
its registry for `linux/amd64`, the instance's platform. Only their projects can fix what it
finds, in a newer tag or a rebuild, which Dependabot proposes like a base's and which is
released the same way. Until one comes, a finding is accepted in `deploy/.trivyignore.yaml` with
the reason it can wait and an `expired_at`, after which the morning run fails again unless the
release has come; an entry whose finding has gone is removed. llama.cpp's server is built every
day on Ubuntu's current packages, so a fix reaches its image within a day and the deployment
with the next Monday's pull request. Ollama, which ran in its place until October 2026, builds
every release with the Go its `go.mod` names and seldom moves it, so its binary carried 43 high
vulnerabilities Go had fixed, which any scanner of the deployment would report. By hand, the
list and one image:

```bash
EMUDOC_ENV=/dev/null EMUDOC_IMAGE=emudoc:app POSTGRES_PASSWORD=unused EMUDOC_DOMAIN=example.org \
  EMUDOC_ENVIRONMENT=x EMUDOC_ALERTS=x EMUDOC_PAGES=x EMUDOC_HEARTBEAT=x \
  docker compose -f deploy/compose.yml -f deploy/aws/compose.yml --profile '*' config --images
docker run --rm -v "$PWD:/src:ro" -w /src aquasec/trivy:0.73.0 image --image-src remote \
  --platform linux/amd64 --timeout 20m --scanners vuln,secret --severity HIGH,CRITICAL \
  --ignore-unfixed --ignorefile deploy/.trivyignore.yaml caddy:2.11.9@sha256:…
```

`deploy/compose.yml` puts it together on a team's server, as the README's "Quick start" runs
it: Postgres 17 with pgvector, the release step, the API, a worker, and llama.cpp's server for
the embeddings, from the image GHCR holds (`EMUDOC_TAG`, `latest` by default, or `EMUDOC_IMAGE`
for one built here), with three more behind profiles: ClamAV (`scan`), the converter
(`office`) and Caddy in front (`tls`), and the backups, which `run` starts alone (`backup`,
"Backups"). Its settings are the `.env` beside it, made from
`deploy/.env.example`: Compose fills the file's `${…}` from it (`POSTGRES_PASSWORD`, `APP_URL`
for Caddy, `EMUDOC_TAG`, `EMUDOC_BACKUPS`, `COMPOSE_PROFILES`), and the app's containers take every line of it as
their environment (`EMUDOC_ENV` names another file for that). Compose expands `$` in its values
unless they are in single quotes. The example sets what the file runs, `DATABASE_URL` made of
the password, `STORAGE_URL=file:///var/lib/emudoc` and `EMBEDDING_BASE_URL=http://embeddings:8080/v1`,
and with their profiles `SCANNER_URL=clamd://clamav:3310` and `CONVERTER_URL=http://converter:8080`,
never without them: a scanner named and not running holds every upload in the queue. A team
sets four values, `APP_URL`, `POSTGRES_PASSWORD`, `ENCRYPTION_KEYS` and a model's key, and may
add any other setting ("Every setting"):

```bash
docker compose up -d --wait                       # in the folder of compose.yml and .env
docker compose up -d --scale worker=3             # more workers
docker compose run --rm --no-deps worker python -m app.maintenance failed-jobs
docker compose logs -f api worker                 # what they log
docker compose run --rm backup                    # a backup of the database and the files
```

From a checkout, the same commands take `-f deploy/compose.yml`, with the `.env` in `deploy/`.
`up` starts Postgres, runs `alembic upgrade head` once it answers, and starts the API and the
worker once that has succeeded and the embeddings server answers; `--wait` returns once the API
answers its health check and the worker keeps its alive file. It replaces the running API and
worker before it runs the migrations, though, so a migration that fails there leaves neither
running. An update, a new `EMUDOC_TAG` in `.env` (or the same `latest` pulled again), runs them
alone first and stops there if they fail, and so does the embeddings server, whose download and
start the API and the worker wait for in the same way, after a backup to go back to
("Upgrades"):

```bash
docker compose run --rm backup && \
  docker compose pull && \
  docker compose run --rm migrate && \
  docker compose up -d --wait embeddings && \
  docker compose up -d --wait
```

Postgres runs on the compose network alone, which the app counts as its own, so without TLS,
in the `database` volume, with the role's two limits set as the server's ("The role's
timeouts"); its backups are "Backups". A deployment with a database of its own names it in
`DATABASE_URL` and leaves the service out with a file over this one that gives it a profile
nobody starts (`postgres: {profiles: [external]}`), which `deploy/aws/compose.yml` does for
RDS: the app's services wait for it only where it runs. The file gives its network a subnet of
its own, `10.73.0.0/24`, so that `FORWARDED_ALLOW_IPS` can name the gateway (change both if the
subnet collides with one the machine reaches), and its project another name than the
development file's, so one machine can run both. Caddy runs on the host's network, answers on 80
and 443 for `APP_URL`'s name and forwards to `127.0.0.1:8000` ("Caddy"); without the profile, a
proxy of the server's own does. The mail relay and a bucket are the deployment's, named in
`.env`. Wherever this guide says `uv run …`, the image runs the same command through `docker
compose run --rm --no-deps worker`: without `--no-deps`, `run` runs the release step first.

## Health checks

Two questions, for the API and the worker alike: is the process alive, or stuck and better
restarted; and is it ready, worth sending requests to. A database outage makes every API
replica unready but none of them stuck, since a restart cures nothing and costs each its start.

- **`GET /api/livez`**, the API is alive: 200 as long as its event loop runs. It touches
  nothing, neither the database nor a thread of the pool that the routes run in, which a burst
  of slow requests can fill for a while.
- **`GET /api/health`**, the API is ready: 200 once it holds a database connection and the
  database answers, with the version that answered: `{"status":"ok","version":"3f2c1ab…"}`,
  the commit the image was built from. 503 otherwise: a database it cannot reach logs one
  line, "Not ready: the database cannot be reached (…)", and a pool with every connection in
  use says `BUSY`, as any route would. It says nothing else, since it is public. The load
  balancer, an uptime service, `up --wait` and a release ask this one.
- Both answer `HEAD` as well as `GET`, and so does the page (`HEAD /`), which uptime services
  often probe instead.
- **The worker** serves nothing to ask. Started with `--alive-file PATH`, it touches the file
  every 10 seconds while every one of its threads runs, and removes it at stop; a check calls
  it stuck when the file is a minute old, or missing. A thread that ended, which none does by
  design, stops the worker with exit status 1 ("The worker's housekeeper thread stopped
  unexpectedly…"), so that whatever runs it starts a whole one. A path the process cannot
  write in is a refusal to start.
- **Whether any worker runs** is in the database: each worker writes a row of `workers` (its
  `host:pid`, its release, its start) at its start and every minute with its heartbeat, and
  marks it stopped at its stop. Nothing else would know: a deployment that never started a
  worker serves every page, takes every upload and question, and leaves them queued. The
  members' status line reads it (below), and so do `maintenance jobs` and the operator's Jobs
  view, which list the workers seen in the last day, each running, stopped, or "not seen for"
  a while, a process that died or lost the database. A row goes a day after it was last seen.

`GET /api/status` is no probe: it is the status line the members see, signed in only, and it
asks the embeddings endpoint (at most once a minute per process, waiting 10 seconds at most).
In production it names no model, address or setting: while the models cannot be used it says
that questions cannot be asked and documents get no summaries or facts for now, while the
embeddings endpoint does not answer that search matches keywords only, and while no worker has
been seen for three minutes that documents wait to be indexed and questions cannot be asked
("Background work off" in the header, and a chat holds its question), each with "Try again
later". A worker that stopped itself counts for those three minutes too, so that a release,
which stops one worker and starts the next, never shows as an outage. The operator reads why in
the start's log, the metrics and the alerts (below).

A failure says as little to the members. In production a document's stage, its error and an
answer's error name no model, address or setting either: a model that refused the key, ran out
of credit or could not be reached is "unavailable just now", a request it refused "could not be
answered", an embeddings endpoint that did not answer leaves the document "found by its words
alone", a scanner out of reach holds the file ("cannot be opened yet"), and the storage "could
not be reached"; each is followed by the retry the worker queued, or by "Tried 6 times…" once it
gives up. What a member's file or question caused is said as before (no text layer, too many
pages, a content filter, a reply that took too long). The operator's sentence, with the model,
the address and what to change, is in the log: the worker's line with each retry, `Stage enrich
of document 12 failed: …` for a failure nothing runs again, the model's own failures
(`Model request failed …`), and the job's `last_error` once the worker gives up (`failed-jobs`).
Rows written before keep what they said. A development checkout shows the operator's sentence,
as its status line does.

Docker restarts nothing for a failing health check. The compose file's checks are what `up
--wait` waits for and what `ps` shows; what restarts a container is its process exiting, with
`restart: unless-stopped`. An orchestrator that restarts on a failed liveness check, such as
Kubernetes, gets one per process:

```yaml
# The API
startupProbe:              # the checks at start reach the storage, the scanner and the model
  httpGet: { path: /api/livez, port: 8000 }
  periodSeconds: 2
  failureThreshold: 30
livenessProbe:
  httpGet: { path: /api/livez, port: 8000 }
readinessProbe:
  httpGet: { path: /api/health, port: 8000 }
lifecycle:                 # out of the Service's endpoints before uvicorn closes its socket
  preStop:
    sleep: { seconds: 5 }
# The worker, started with --alive-file /tmp/worker.alive
livenessProbe:
  exec:
    command:
      - python
      - -c
      - import os, sys, time; sys.exit(time.time() - os.stat('/tmp/worker.alive').st_mtime > 60)
  initialDelaySeconds: 30
  periodSeconds: 30
```

Kubernetes takes a stopping pod out of the Service's endpoints at the moment it sends SIGTERM,
and the two race: uvicorn closes its socket at once, so a request routed to it meanwhile is
refused. The `preStop` sleep holds the signal back until the endpoint is gone; it needs
Kubernetes 1.30, and an older cluster runs `exec: { command: [sleep, "5"] }` instead. The pod's
`terminationGracePeriodSeconds`, 30 by default, covers the sleep and the stop's own 8 seconds
("Time to stop" under "The image"). No readiness change is needed while the API stops: it
takes no new connection, and its answer streams end at once, for their pages to follow again
from another replica.

## The database

Postgres 17 with pgvector 0.8 or later, either managed (RDS, Cloud SQL, Azure's flexible
server and the like, which bring pgvector) or run by the deployment as a primary with a
standby. Either way it has no public address: it sits on the deployment's private network,
or the provider's network the servers are attached to, and is reached with its certificate
checked when that network is not the deployment's own ("Encryption", below). Every process
says at start what it found, "The database is Postgres 17.6 with pgvector 0.8.1; the role
emudoc has statement_timeout 30s and idle_in_transaction_session_timeout 5min.", and
`python -m app.maintenance database` reports the same with the state of the largest tables
("Vacuum", below).

**pgvector 0.8** brought iterative index scans, which searches by meaning use. Without them
the vector index hands back the 40 passages nearest the question from the whole table, every
project's, and the project's filter drops the others' afterwards, so in a database of many
projects a search finds few of its own passages or none. Production refuses to start with an
older one and says what to run: `ALTER EXTENSION vector UPDATE;` as the database's owner when
the server has a newer one installed (a managed database after a minor version upgrade), or
an upgrade of the server's pgvector when it has not. Development warns and searches without.

**A search is planned for its project**: Postgres sorts a project of up to a few percent of
the table's passages by their exact distance from the question, and walks the vector index
for a larger one, keeping `hnsw.ef_search` passages in view as it does (200, where pgvector's
default of 40 left out one in six of the 10 nearest in a project of 93,000; "Load"). Every
search sets `plan_cache_mode = force_custom_plan` for itself: psycopg prepares a statement on
its fifth run on a connection, and Postgres then keeps one plan for every project once it
looks no dearer, which in a table of many projects is the small project's sort, three times
slower for a large one and slower as it grows. None of this needs the operator.

**The facts table** (behind `extraction_templates`) sorts a kind's documents by a field's
first value, looked up for each document as it sorts them, so a page's time grows with the
kind's documents in the project: at 5,000 invoices among 55,000 documents and 920,000 facts,
a page took 30 to 120 ms (two statements of 30 to 55 ms each), sorted by the name, an amount, a
date or a text, with conditions and words or without, the next page alike. Each is planned for
its project and runs without the JIT, which the lookups' cost would otherwise start: that took
330 of the 385 ms a page took before.

**ICU**: the library lists a folder's files in an order the database keeps an index in, by
an ICU collation the migrations make, `natural_order` (numbers in a name compared as numbers,
case and accents set aside). The server must be built with ICU, as the official images, RDS
and the other managed databases are. Postgres records the ICU version a collation was made
with, and an upgrade of the server or its operating system may bring another, after which the
index (`ix_documents_listing`) may hold names in the old order and a page of the library skip
or repeat files. The start, `python -m app.maintenance database` and the watch say so then;
as the database's owner:

```sql
REINDEX INDEX CONCURRENTLY ix_documents_listing;
ALTER COLLATION natural_order REFRESH VERSION;
```

**High availability**: a managed database's standby (Multi-AZ) or the deployment's own
(Patroni, or a streaming replica promoted by hand) takes over under the same address, or, with
two hosts in the URL, `?host=db-a,db-b&target_session_attrs=read-write` finds the one that
takes writes. A failover drops every connection, and for a minute or so none can be made. The
pools check a connection as they hand it out and replace a dead one, so what was idle carries
on. What was running at that moment: a request fails, and readiness (`/api/health`) says 503
until the database answers; the worker's claims fail and are tried again every 5 seconds, and
its listener connects again. A document whose stage lost the database is run again from that
stage with the usual backoff once it answers ("Retrying in 1 minute"), or, if it was still away
when that was to be written, when the job's lease runs out, 5 minutes on; it is never failed for
the failover. An answer being written ends failed, "The answer failed unexpectedly.", for the
member to ask again, or starts over by itself when the database stayed away past its lease.

### The role's timeouts

The app connects as one role, which the deployment gives two limits, so that a query that runs
away does not hold its connection and its locks until it ends by itself, and a transaction left
open does not keep vacuum from cleaning up after it:

```sql
ALTER ROLE emudoc SET statement_timeout = '30s';
ALTER ROLE emudoc SET idle_in_transaction_session_timeout = '5min';
```

New connections take them, so restart the app after. The compose file's Postgres sets the same
two as the server's own (`-c statement_timeout=30s`), which needs nothing run. In production a
role without them is a warning at every start, with these two statements. A member's request whose statement runs
into the limit is answered with 503 and "The database took longer over this request than it
allows, so the request was stopped. Try again in a moment.", and the API's log has one line
naming the route ("GET /api/… was stopped by the database: …"). The work whose length is the
data's lifts both for its own transaction (`unbounded` in `backend/app/db.py`): the
migrations, `rebuild-embeddings`, the look at every passage at start for a changed embedding
model, the housekeeping round and `sweep-orphans` (which keep one transaction open while the
storage lists), `after-restore`, an account's purge and a project's deletion. The worker's
stages hold no transaction while they wait for the model, the scanner or the embeddings
endpoint, so the idle limit never ends one. Leave `transaction_timeout` unset: an upload
keeps its transaction while each file goes to the storage. Set the limits on the role, not in
`DATABASE_URL`'s `options`, which pgbouncer refuses.

### A pooler

Each process holds up to `DB_POOL_SIZE` + `DB_MAX_OVERFLOW` connections (31 by default), and a
database takes about 100 (`max_connections`; a managed one sets it from its memory). When the
replicas need more, a pooler between them and the database lets many client connections share
a few server ones: pgbouncer, or the provider's (RDS Proxy, Azure's built-in pgbouncer, Cloud
SQL's managed pooling, Supabase's port 6543).

In **session mode** a client keeps its server connection until it disconnects, and the app
needs nothing. In **transaction mode** it has one for a transaction at a time, which shares
them far better and changes two things, both set with:

```ini
# DATABASE_URL is the pooler's address.
DATABASE_POOLER=transaction
# The database itself, or the pooler's port in session mode.
DATABASE_LISTEN_URL=postgresql+psycopg://emudoc:…@db.internal:5432/emudoc?sslmode=verify-full&sslrootcert=system
```

- **No prepared statements.** psycopg prepares a statement after its fifth run on a
  connection, and the next transaction may run on a server connection that never saw it:
  "prepared statement "_pg3_0" already exists" or "does not exist", on pgbouncer before 1.21
  or with `max_prepared_statements = 0`. With `DATABASE_POOLER=transaction` nothing is
  prepared.
- **The listener elsewhere.** The worker's listener holds a `LISTEN jobs` that tells it the
  moment a job is queued. Through a pooler in transaction mode the `LISTEN` ends up on a
  server connection the pooler then hands to someone else, and nothing reaches the listener,
  without an error. `DATABASE_LISTEN_URL` gives it an address of its own, one connection per
  worker process. Without one, behind a pooler in transaction mode, the worker does not
  listen, says so at start, and finds queued jobs by polling every 2 seconds, which is that
  much longer before an answer starts.

The rest works through either mode: every advisory lock the app takes is the transaction's
(migrations, housekeeping, `after-restore`, the alerts), and the settings it changes, search's
`plan_cache_mode`, `hnsw.ef_search` and `hnsw.iterative_scan`, are `SET LOCAL`. The release step and the maintenance commands
may go through the pooler too. `DATABASE_LISTEN_URL` is held to the same rules as
`DATABASE_URL` in production: its own password, and its certificate checked off the own
network. `docker compose --profile pgbouncer up -d --wait` runs pgbouncer in transaction mode
in front of the development database for trying it (`docker-compose.yml`), and the test suite
passes through it.

A pgbouncer of the deployment's own, beside the database:

```ini
[databases]
emudoc = host=127.0.0.1 port=5432 dbname=emudoc

[pgbouncer]
listen_addr = 10.0.0.5
listen_port = 6432
auth_type = scram-sha-256
auth_file = /etc/pgbouncer/userlist.txt
pool_mode = transaction
# The replicas' pools together; each client connection costs pgbouncer little.
max_client_conn = 500
# The server connections per database and role: under max_connections, with room for the
# listeners, the release step and an operator's psql.
default_pool_size = 40
client_tls_sslmode = require
client_tls_cert_file = /etc/pgbouncer/server.crt
client_tls_key_file = /etc/pgbouncer/server.key
```

Clients that reach it off the own network check its certificate as they would the database's,
so its certificate names the address in `DATABASE_URL`.

### Vacuum

Postgres keeps a deleted or replaced row until vacuum removes it, and autovacuum does that by
itself once a table's dead rows pass a share of it (20 % by default). Some of the app's tables
churn: `passages` loses every passage of a document indexed again and gains new ones, and a
change of `EMBEDDING_MODEL` replaces every row at once, each with an entry in the vector index
that stays until vacuum has been; `chat_events`, `jobs` and `rate_limits` are written and
pruned all day. A table vacuum falls behind on is read slower, dead rows included, and keeps
growing.

```text
$ python -m app.maintenance database
Postgres 17.6 with pgvector 0.8.1, which searches with iterative index scans.
The role emudoc has statement_timeout 30s and idle_in_transaction_session_timeout 5min.
DATABASE_URL goes through a pooler in transaction mode, which is sent no prepared statement, and the worker listens for queued jobs at DATABASE_LISTEN_URL.

The largest tables:
  table          rows  dead     size  indexes  vacuumed  analysed
  passages    412,880    3%  1.8 GB   3.1 GB   2 h ago   2 h ago
  …
The oldest open transaction began 2 s ago (active).
Nothing to look at.
```

It exits with 1, and says what to do, when a table has 10,000 dead rows or more and they are
over a fifth of it, when a transaction has been open for over an hour (vacuum keeps every row
replaced since it began, in every table), when pgvector is older than 0.8, when a collation
was made with another ICU than the server has ("ICU", above), and in production when the role
has no timeouts. Run it from a timer that mails on failure, as for the drill, or
take the same numbers from `pg_stat_user_tables` (`n_dead_tup`, `last_autovacuum`) and
`pg_stat_activity` (`xact_start`) into the monitoring, where a managed database's own metrics
have them too.

When `passages` falls behind: `VACUUM (VERBOSE, ANALYZE) passages;` at once, and autovacuum
sooner for that table from then on:

```sql
ALTER TABLE passages SET (autovacuum_vacuum_scale_factor = 0.05, autovacuum_analyze_scale_factor = 0.02);
```

Vacuuming an HNSW index after many rows were replaced, as after a change of embedding model,
takes long; pgvector's advice is to rebuild the index first, which builds it without the dead
entries and leaves the vacuum little to do: `REINDEX INDEX CONCURRENTLY ix_passages_embedding;`
then the vacuum. The build is fastest when the index fits in `maintenance_work_mem` (Postgres
says "hnsw graph no longer fits into maintenance_work_mem" when it does not), which a session
raises for itself: `SET maintenance_work_mem = '2GB';` before it, on a server with the memory
to spare. `rebuild-embeddings` builds the index the same way.

## The embeddings endpoint

Search by meaning, the library's and the agent's, needs every passage turned into a vector as
it is indexed and every search as it is asked, by an OpenAI-compatible `/embeddings` endpoint:
`EMBEDDING_BASE_URL`, with `EMBEDDING_MODEL`, `EMBEDDING_DIM` (the vectors' size) and the
model's prefixes (`EMBEDDING_QUERY_PREFIX`, `EMBEDDING_DOCUMENT_PREFIX`, EmbeddingGemma's by
default). Without one, search matches keywords only, so neither a member nor the agent finds a
passage worded otherwise than the question, and every document's embed stage is tried again
after 1, 2, 4 and 8 minutes and then given up, each a failed job for `ALERT_EMAIL`. The default
address, an Ollama on 127.0.0.1, is in the image the container itself, so production refuses to
start with `EMBEDDING_BASE_URL` unset or blank.

Every process asks the endpoint for one vector at start and says what it found: "Passages are
embedded by embeddinggemma at http://embeddings:8080/v1". An endpoint that turns the request
down stops a production start, with the settings to look at: a model it does not serve (404;
an Ollama's until `ollama pull`), a model that makes no embeddings (501 from Ollama), a key it
refuses (401), vectors of another size than `EMBEDDING_DIM`. One that is out of reach or busy
may be starting beside the app, so the process starts and warns: "Until it answers, search
matches keywords only…". A start refused this way changes nothing, even with a new
`EMBEDDING_MODEL`: the documents are queued to be embedded with it only by a start that goes
through. Afterwards each search that falls back to keywords logs "A search in project 12
matched keywords only: …", and the members' status line says the same.

**Beside the app.** The compose file runs llama.cpp's server (`ghcr.io/ggml-org/llama.cpp`,
its `server` image) as `embeddings`, on its network only, as an unprivileged user on a read-only
filesystem, with the weights in the `embedding-model` volume. A one-shot `embedding-model`
service fills it before the server starts: it downloads `EMBEDDING_WEIGHTS_URL` when the volume
has no file named by `EMBEDDING_WEIGHTS_SHA256`, checks the download against that hash and
keeps it under it, so a first `up` needs the weights' 334 MB from Hugging Face and later ones
nothing from anywhere, and every machine and every release serves the same file. The default is
Google's EmbeddingGemma 300M at 8 bits, as llama.cpp's own organisation published it
(`ggml-org/embeddinggemma-300M-GGUF`), at a fixed commit of its repository. The server answers
whatever model a request names, so `EMBEDDING_MODEL` stays the app's name for the weights,
`embeddinggemma`, Ollama's, under which the vectors are stored and priced. Its vectors are
Ollama's: llama.cpp b11706 on these weights and Ollama 0.40.13 on its own at 16 bits embedded
the same texts in eight languages to a cosine of 0.9992 or closer, with the same token counts,
so vectors stored from Ollama's `embeddinggemma` are kept. A deployment that ran Ollama in this
compose file, before llama.cpp's server, can remove what it left behind (`docker volume rm
emudoc-deploy_models`, `docker image rm ollama/ollama:0.40.5`). Other weights are another GGUF
file named in `.env`,
`EMBEDDING_WEIGHTS_URL=https://huggingface.co/<repository>/resolve/<commit>/<file>.gguf` and
`EMBEDDING_WEIGHTS_SHA256=<the SHA-256 Hugging Face shows for the file>`, with a new
`EMBEDDING_MODEL`, its `EMBEDDING_DIM` and prefixes ("Changing the model", below); on AWS the two are settings like the others. A file that does not match the hash stops
`up` with "The file at EMBEDDING_WEIGHTS_URL is not the one EMBEDDING_WEIGHTS_SHA256 names."

**The model and its terms.** EmbeddingGemma reads more than a hundred languages into one
space, so a question finds the passage that answers it whichever language either is written in,
which the keyword half of a search cannot (it stems each passage in its document's language;
the README, "The workspace"). It replaced nomic-embed-text v1.5, which was trained on English,
on 2026-10-31: asked 42 questions in English about contract, invoice and delivery passages in
seven other languages, among 48 passages, v1.5 ranked the right one first for 2 of them and
EmbeddingGemma for 34, and asked in each passage's own language, for 22 and 40 (bge-m3 for 35
and 39; Qwen3-Embedding 0.6B, four times slower, for 28 and 38). It is Google's, under the
[Gemma Terms of Use](https://ai.google.dev/gemma/terms), not an open-source licence: it may be
used commercially, Google claims no rights in the vectors, and its use must keep to Google's
[Prohibited Use Policy](https://ai.google.dev/gemma/prohibited_use_policy), which Google may
change. Google's own repository on Hugging Face is gated behind accepting those terms; the
compose file downloads ggml-org's copy, which is not, but the terms hold for the weights
whichever copy, so the operator reads them before running it. A deployment that should run only
openly licensed weights names another model ("Changing the model", below): bge-m3 (MIT, 1,024
dimensions,
`https://huggingface.co/ggml-org/bge-m3-Q8_0-GGUF/resolve/9eba04c5d75ba5a1595e45de734d36bef4e5cb98/bge-m3-q8_0.gguf`,
`aa473d51f451a22f0fcf39ba3330c14bed38a385712b1113440f69df4047a173`, both prefixes empty)
ranked about as well, at twice the time a batch.

A deployment from before 2026-10-31 that left these settings to their defaults moves to
EmbeddingGemma at its next release: the server downloads the new weights, and the start queues
every document to be embedded again at bulk priority. The vectors keep their size, 768, so
nothing is rebuilt; the backlog is that of "Changing the embedding model or its size", and
until a document's turn a search by meaning ranks it poorly. To stay on nomic-embed-text v1.5,
set before that release `EMBEDDING_MODEL=nomic-embed-text`,
`EMBEDDING_QUERY_PREFIX="search_query: "` and `EMBEDDING_DOCUMENT_PREFIX="search_document: "`,
and for the server beside the app
`EMBEDDING_WEIGHTS_URL=https://huggingface.co/nomic-ai/nomic-embed-text-v1.5-GGUF/resolve/0188c9bf409793f810680a5a431e7b899c46104c/nomic-embed-text-v1.5.f16.gguf`
and `EMBEDDING_WEIGHTS_SHA256=f7af6f66802f4df86eda10fe9bbcfc75c39562bed48ef6ace719a251cf1c2fdb`.

The server makes embeddings alone (no chat page, no slots endpoint) and logs warnings and
errors, never what it is sent. It holds about 200 MB with EmbeddingGemma, and its image is 300
MB to download and 1.2 GB on disk. Its batches hold 24,576 tokens (`--ubatch-size`), more than
the 6,000 characters the app sends at most, even of characters the model has no token for and
reads as their UTF-8 bytes, four tokens each, so that an input past the model's context of
2,048 tokens, such as a passage of dense Chinese, a long row of a table or a line of emoji, is
refused as such (400, `exceed_context_size_error`) rather than as a failure of the server's,
which the app would try again; the app then embeds that input from its beginning, cut to what
the context holds, as Ollama does by itself. The larger batch takes no more memory than 8,192
did. llama.cpp has
no releases but a build a day under the `server` tag: Dependabot proposes the newest digest
every Monday, and CI's Embeddings job (`.github/scripts/check-embeddings.sh`) starts the compose
file's two services with it and fails when the vectors of `.github/scripts/embeddings.json` move
(a cosine below 0.9999), the texts' token count changes, or the refusal does. A build that fails
it is not taken; a later one that passes is. The vectors are the ones the deployments' databases
hold, so a build whose vectors moved would rank every stored passage against a query of another
model, without a word in any log; other weights chosen on purpose rewrite the file with the
script's `--write`. On the deployment's own network the server sends nothing to another company
and takes plain HTTP ("Encryption"); on another machine there, such as a GPU server for a large backlog
(llama.cpp's CUDA image), it listens on a private address, and across a network the deployment
does not own it sits behind TLS, as production requires.

On a CPU, embedding is the slow part of indexing. On a 6-core laptop CPU (Core i5-11400H, in
Docker, 2026-10-31), llama.cpp b11706 embedded a batch of 32 passages of 1,500 characters with
EmbeddingGemma in 18 seconds on every thread and in 29 on two, as many as the AWS instance has,
and of 2,300 characters (the longest passages, with their headings) in 29 and in 47;
nomic-embed-text v1.5 took 18 and 36, and 30 and 60. The app waits 120 seconds for a batch.
Earlier, on an 8-core laptop CPU (Ryzen 7 7435HS), nomic-embed-text v1.5 embedded the batch of
1,500 characters in 7 seconds and one of 6,000 in 23. Hugging Face's text-embeddings-inference also
serves `/v1/embeddings`, from a 240 MB image, but its CPU build (1.9) could not load
nomic-embed-text v1.5 on its fast backend and ran it five times slower than Ollama, so that a
batch of long passages outlasted the 120 seconds; it suits a GPU, or a model its ONNX backend
loads.

**A hosted endpoint** is any OpenAI-compatible service with a key in `EMBEDDING_API_KEY`, such
as OpenAI's: `EMBEDDING_BASE_URL=https://api.openai.com/v1`,
`EMBEDDING_MODEL=text-embedding-3-small`, `EMBEDDING_DIM=1536`, and both prefixes set empty.
It is sent every passage and every search, so it is a company like the model providers, and its
terms must forbid training on what it is sent, as OpenAI's API's, Azure OpenAI's and Vertex AI's
do. A model of more than 2,000
dimensions does not fit the vector index (pgvector's HNSW), and the app sends no `dimensions`
to shorten one: text-embedding-3-large's 3,072 do not fit. The tokens the endpoint reports
are counted for each document's embed stage and priced like a model's, by `MODEL_PRICES` under
the name `EMBEDDING_MODEL` gives (`text-embedding-3-small=0.02/0`), so their cost shows on the
document and counts toward the day's spending; the daily spending cap holds back the stages
that call a model, not this one, whose cost is small beside theirs. The searches' vectors are not
counted.

**Changing the model**: a new `EMBEDDING_MODEL` queues every document to be embedded again at
the next start, and until a document's turn a search by meaning compares a vector of the new
model with its old ones, which ranks it poorly; a new `EMBEDDING_DIM` needs `python -m
app.maintenance rebuild-embeddings` with the app stopped, which drops the stored vectors. Either
replaces every row of `passages`, so rebuild the vector index and vacuum after it ("Vacuum",
above), and embedding every passage again takes hours on a CPU and costs every token again
on a hosted endpoint.

## Drawing pages

The document pane shows a PDF's pages as images, the library's thumbnail of a PDF is its first
page, and a scanned page is drawn before the model reads it. A TIFF's pages and a HEIC photo,
which neither a browser nor the model takes as they are, are drawn the same way, by the same
children, so that their decoders (libtiff, and libheif with libde265 from the `pi-heif` package,
LGPLv3 libraries whose licences and sources its files in the image name) never run in the API or
the worker themselves. pdfium draws one page at a time in
a process (it is not thread-safe, even with a document in each thread), so the API and the
worker each keep `RENDER_PROCESSES` child processes for it (`python -m app.parsing.render`, 2 by
default), started when the first page is asked for and kept for the next: that many pages are
drawn at once, a page of text in about 50 ms. A request waiting for its page holds none of the
API's threads, so a member scrolling through a long PDF slows no other request.

Each child keeps within limits, since it reads files anyone with an account can upload:
`RENDER_MEMORY_MB` of address space (1024), `RENDER_TIMEOUT` seconds a page (30), and no page
drawn larger than 2,576 pixels on its longer side, whatever size the PDF gives it (a page of
200 inches a side would otherwise take gigabytes). A page past a limit is not drawn: the pane
shows none for it (a 404 whose detail says why), a scanned page fails the document's indexing
with a sentence that names the page, the log says "A render process was stopped: …", and a
fresh child draws the next page; a child is also replaced after 200 pages. A child holds about
50 MB while it waits, so size a container's memory for `RENDER_PROCESSES` children at
`RENDER_MEMORY_MB` at worst, beside the parse's (`PARSE_MEMORY_MB` per `PARSE_CONCURRENCY`).
`emudoc_renders_total` counts the pages by what they were drawn for and how each ended
("Metrics").

Once a PDF is parsed, a `render_pages` job draws its first `PRERENDER_PAGES` pages (3), ahead of
the uploads waiting to be indexed, so that opening the document shows them from the storage at
once; the others are drawn the first time a member looks at them. Each page drawn is kept for
`PAGE_IMAGE_DAYS` and counts as `render` usage: the member's when the pane asked for it, the
organisation's with no member when it was drawn ahead. A page that cannot be drawn ahead is
left for the pane, and the job ends done rather than failed.

When an organisation's owners or admins choose another language for its documents, an
`apply_language` job moves its passages to it, 500 a transaction, each generating the passage's
keyword index again (and, as any update does, its entry in the vector index), without asking a
model. It runs in the indexers' lane at the priority of bulk work, so uploads go first; an
organisation of 100,000 passages takes a few minutes of one thread.

From a bucket, a page is drawn from a copy of the original in the temporary directory. The
pages drawn ahead share one, and the API keeps its copy for the pages and the thumbnails it
draws next: five minutes after the last page drawn from it, up to `RENDER_CACHE_MB` of copies
(256), the least recently used going first, and a request that comes while the copy is being
made waits for it rather than make another. So a member scrolling through a long PDF waits for
one download of it, not one a page (from the compose bucket a page of a 62 MB scan took 2 s
before, "Load"). A copy being drawn from is never removed, so an API holds at most
`RENDER_CACHE_MB` or its `RENDER_PROCESSES` originals in use, whichever is more; a document
deleted through it loses its copy at once, and another API's copy goes within the five minutes,
unread, since every request reads the document first; an API that stops removes its copies. A
directory needs none of this: the pages are drawn from its file.

## The converter

A Word file, a presentation, a spreadsheet, an RTF or an HTML file is shown as it looks, page by
page, where `CONVERTER_URL` names a converter (`app/conversion.py`). Once the parse stage has read
such a file, a `convert_document` job sends it to the converter, which answers a PDF of it made by
LibreOffice; the worker measures its pages with a render child, keeps it beside the original as
the document's view (`view.pdf` under the version's prefix), and the pane, the library's
thumbnail and a share link draw its pages as they draw a PDF's ("Drawing pages"), kept for
`PAGE_IMAGE_DAYS`. The PDF never reaches a browser. The document is still searched, cited and
asked about by its text, which "Text" above the pages shows with the outline: the view is no
stage of the indexing, and nothing fails with it. A document uploaded before, or while no
converter answered, has its view made when a member first opens it. A small Word file takes
about half a second.

The converter is this image run with another command, `python -m app.converter`
(`app/converter.py`, the standard library alone, so that it needs none of the app's settings),
as the compose file's `converter` service. LibreOffice reads every upload of those types through
filters written in C++, and a document can name files and addresses for it to fetch: a linked
picture, a section linked to another file, a spreadsheet's external data or `WEBSERVICE`.
Tried on 2026-11-08 with LibreOffice's defaults, a .docx whose picture was linked to a path drew
that file into its PDF. So the converter runs where nothing is worth reaching: no env file (no
key, no database address), no volume, a read-only filesystem with a tmpfs at `/tmp`, no
capabilities, and only the network `converter`, which is `internal` (no gateway: neither the
internet, the instance's metadata nor the database) and which the worker alone joins. Within it,
each conversion runs LibreOffice as a process group of its own, killed past `--timeout`, in a
copy of a profile whose settings run no macro, follow no link a document makes to a file or an
address, update no linked section or field, and recalculate no formula (a spreadsheet shows the
values it was saved with), and reads the file with its type's import filter alone, so that a
file of another format named as a .docx is refused rather than read by a filter nobody checked.

Its limits are its command's options in the compose file: `--concurrency` conversions at once
(2), `--timeout` seconds one may take (120), `--max-mb` the largest file it takes (100, as large
as `MAX_UPLOAD_MB`: raise them together), and the container's memory (2 GB), past which the
kernel ends the conversion. The image carries LibreOffice without its windows or Java, with the
fonts documents name most (Carlito and Caladea for Calibri and Cambria, Liberation for Arial,
Times New Roman and Courier New, Noto for the other scripts), about 400 MB more for every
process's image; Debian's security updates reach it as the image is built again, which Audit
does every week. CI's image check converts a Word file in a container with no network.

A converter out of reach or busy is tried again three more times, after 2, 4 and 8 minutes; then
the document says its pages could not be made, with "Try again", and the job is a dead letter
mailed to `ALERT_EMAIL` ("What the worker mails"). A file LibreOffice cannot read, one past
`--timeout`, and a PDF of more than `PARSE_MAX_PAGES` pages are the member's to know of: the
document says so above its text, and the log has "Document … has no view: …". The worker says at
its start which LibreOffice makes the views, or warns that the converter does not answer.
Without `CONVERTER_URL` nothing is converted and those documents are shown by their text, as
before. In development `docker compose --profile converter up -d --wait` builds the image and
runs one on 127.0.0.1:8090, for `CONVERTER_URL=http://127.0.0.1:8090`.

## Large documents

What indexing one document may cost is bounded by settings, each a refusal or a shortcut with a
sentence the member reads:

- **Its text.** Every stage after the parse pays for it: the worker holds it, the structure stage
  cuts it into passages (one for every 1,200 to 2,000 characters), and each passage is written
  with its text index and embedded. `PARSE_MAX_CHARS` (20,000,000) is the most one document may
  hold, some 6,000 pages of prose or 140,000 rows of an eight-column order export. The parse
  child refuses a file with more before it replies, so the worker never holds it, and a sheet
  stops being read at the row that takes it past: "This file holds more than 20,000,000
  characters of text, the most one document can have. Split it and upload the parts." Before
  it, `PARSE_MAX_PAGES` (5,000) refuses a PDF or a TIFF by its pages, a presentation by its
  slides, and `PARSE_MAX_ROWS` (100,000) a sheet by its rows.
- **Its pages.** A PDF's pages are read one after another, about a tenth of a second each on one
  core (0.125 s a page of prose on the development machine, linear, more for pages of tables),
  while the memory hardly grows with them (93 MB at 1,000 pages). So a PDF of more than
  `PARSE_PART_PAGES` (250) pages is read that many pages a parse child, each within
  `PARSE_TIMEOUT` and `PARSE_MEMORY_MB`, which writes what it read to the temporary directory
  (2.4 MB for 250 dense pages, so some 50 MB for 5,000), and one more child makes the document of
  the parts, its running headers, body size and heading levels the whole file's, exactly as one
  child would. Each child holds one of the worker's `PARSE_CONCURRENCY` slots and lets it go
  when it ends, so a long PDF takes turns with the uploads behind it; 250 dense pages took 31 s
  here, and 5,000 took 11 minutes in 20 parts, the child that made the document of them holding
  252 MB and taking 4 s. After each part the scans read so far are checked against
  `OCR_MAX_PAGES`, so a file it cannot take is refused before the rest is read ("This file has
  at least 300 scanned pages to read, …"); the text the parts hold is added up against
  `PARSE_MAX_CHARS` as they are read, their margins included.
  A part that runs out of time names its pages ("Parsing pages 2,251 to 2,500 of the file did
  not finish within 5 minutes and was stopped. …"): lower `PARSE_PART_PAGES` if that is seen of
  files whose pages are heavy, rather than raise the time a parse may take. The stages after the
  parse took about 10 s together for 1,000 pages (6,000 passages) with the suite's stand-ins.
  `PARSE_PART_PAGES=0` reads every PDF in one child, as before 2026-10-25.
- **Its scans.** `OCR_MAX_PAGES` (100) is how many pages the model may read as scans, a request
  each; a document with more is refused before a page is read. `OCR_CONCURRENCY` (4) of them are
  read at once, in page order: each page is drawn by one of the worker's `RENDER_PROCESSES`
  children and sent to the model, and the next is started as one is answered, so a scan takes
  about a quarter of the time it took one page after another ("Load"). Each indexer reading a
  scan sends that many requests at once, so a worker whose 16 indexers all read scans sends 64,
  more than a provider's lowest tiers take: lower `OCR_CONCURRENCY` when
  `EmudocModelRateLimited` fires while scans are read, since a page the provider still refuses
  after its client's own retries, and no fallback reads, fails the stage, which the worker runs
  again from its first page. A page in flight holds its image, a few megabytes. A page that
  fails starts no more: those being read end and are counted, and the document says why the
  first of them in page order failed.
- **Its summaries and facts.** The enrich stage sends the model the document 40,000 characters a
  request, and a request to roll them up. A table of more than 100 rows, one block or blocks that
  repeat its header as a sheet's do, is shown in part: its header, its first 20 rows and its last
  5. What the model makes of it is the summary of a sheet, which those give, and no fact is kept
  from it, since a fact table with a few orders of thousands would answer a count wrong; its rows
  are searched and read in answers all the same. A sheet of 90,000 rows is so one request, where
  it was hundreds. `ENRICH_MAX_REQUESTS` (50, some 2 million characters, 500 to 700 pages of
  prose) caps the requests: a longer document is read from its start for 49 and rolled up, the
  sections after them have no summary, and its enrich stage says how far it went, as "312
  section summaries, 1,840 facts, from pages 1 to 640 of 980: the model reads no more of one
  document, and the rest is searched without summaries or facts" (or "the first 61 % of the
  document" without pages). The first request also lists the project's extraction templates,
  each field described while the listing is short and by name and type alone past 16,000
  characters, a few thousand tokens more a document, so that its facts are read for its kind
  (README, "How it works"). The day's spending cap is checked before the stage ("The models
  are spending fast"), so this is the most one document spends past it.
- **Its vectors.** The embed stage keeps each vector as the column stores it, 4-byte floats (3 KB
  at 768 dimensions, where the endpoint's reply as Python floats is 25 KB), and writes them 500
  a statement. Its time is the endpoint's and the database's: each vector written goes into the
  HNSW index, 3 to 4 ms a passage here ("Load").

Beyond these the worker's memory is the parse's, `PARSE_MEMORY_MB` for each of its
`PARSE_CONCURRENCY` children at worst, and the text of the documents its `WORKER_CONCURRENCY`
indexers hold: a workbook of 17.6 million characters (10,400 passages) took the worker to
280 MB beside its parse child's 220 MB. The worker also holds the model that tells a document's
language (py3langid's, about 75 MB with numpy once loaded), from the first document it parses;
the API's process, which parses nothing in production, never loads it. The indexers past `PARSE_CONCURRENCY` wait for a parse
to end (`emudoc_parses{state="waiting"}`, and `waited` in the run's `parse` span), so raising
`WORKER_CONCURRENCY` adds threads that wait on the model, not parses.

## The models

`INDEX_MODEL` reads every document, to infer its sections, write summaries, pull out facts and
transcribe scanned pages; `AGENT_MODEL` answers in the Ask tab. (Until 2026-11-11 the indexing
model also wrote the questions an empty chat offered, recorded as the usage kind `suggestions`,
whose rows stay in the costs.) Each is written
`provider:model`, each may have a fallback, and every one of them is a company the documents
are sent to ("What leaves the deployment").

**Pinned.** Production refuses to start with `AGENT_MODEL` or `INDEX_MODEL` unset: the default
(`google:gemini-3.5-flash-lite` in October 2026) moves with the app's releases, and Google
showed what a provider does with a model on its own schedule when, on 18 September 2026, it
stopped serving Gemini 2.5 to accounts that had not used it. It refuses as well, for any of the
four settings, an alias its provider moves to a newer model when it likes: a name ending in
`latest` (`gemini-flash-latest`, `chatgpt-4o-latest`, a `:latest` tag) and OpenRouter's
`openrouter/auto`. Behind an alias the model changes without a release of the deployment, and
with it the price (`MODEL_PRICES` is keyed by the name) and the answers. Name the version the
provider lists, as `google:gemini-3.5-flash-lite`. Changing a model is then a release of its
own: the setting, its key, its price in `MODEL_PRICES`, and `python -m app.maintenance models`,
below.

**A fallback.** `INDEX_FALLBACK_MODEL` and `AGENT_FALLBACK_MODEL` name the model asked when the
first cannot answer. It is asked when the first model's provider is down or overloaded (429,
5xx; after the provider's SDK has retried), cannot be reached, or refuses the key, the account
or the model itself (401, 402, 403, 404), which is how a retired model fails, and when the first
model cannot be set up or is switched off (`DISABLED_PROVIDERS`). It is not asked for a request
the provider refused (another 4xx, a content filter, a reply that would not validate), which the
next model would refuse too or which hides a fault of the app's, nor for a reply that took too
long, as a document too large for the time would be on the fallback as well; nor once the first
model has begun to answer a turn, since what it wrote may already be on a member's screen. Each
time it is asked, the process logs one line, `INDEX_MODEL google:gemini-3.5-flash-lite failed,
so INDEX_FALLBACK_MODEL anthropic:claude-haiku-5-5 is asked instead: The model
gemini-3.5-flash-lite was not found, or is served no longer.`, and for the next minute asks the
fallback first, so that a provider that is down costs one request a minute its retries rather
than every request. When both fail, the error says both sentences, which the log keeps and a
development checkout shows; a deployment's members read why the last model failed in words that
name neither ("The AI model is unavailable just now. Try again later."). What a fallback answered
is counted and priced under its own name, on the usage rows and under the answer or the document.

Take the fallback from another provider: one of the same provider covers a retired or
overloaded model, but not an outage, a revoked key or an unpaid bill. The kill switch then
keeps the app working: `DISABLED_PROVIDERS=google` with fallbacks of Anthropic's stops Google's
bill while Claude indexes and answers. A fallback is sent what its primary is, so it is held to
everything a primary is: its provider's terms must forbid training on what it is sent,
`GOOGLE_PAID_TIER` says so of a `google:` one, its address may not be plain HTTP off the
own network, and its key must be in the environment: a start refuses a fallback that cannot be
set up, as it would its primary. It needs its price in `MODEL_PRICES`, or what it answers
counts nothing against the spending cap: production refuses `DAILY_SPEND_CAP` without one. A production start without a fallback for a kind of work
warns, saying what stops while the model cannot answer; a deployment that keeps to one
provider on purpose lives with the line. The process says at start which models do the work:
"Documents are indexed by google:gemini-3.5-flash-lite, and by anthropic:claude-haiku-5-5 when
it cannot answer".

**Trying them.** A start builds every model, which catches a missing key or package, but asks
none: an answer at every start of every replica would cost tokens, and an outage of the first
model would hold a release that its fallback could serve. So a fallback would first be asked on
the day it is needed, unless the operator asks it before:

```bash
docker compose -f deploy/compose.yml run --rm --no-deps worker python -m app.maintenance models
```

asks each model what the app asks of it, alone, without its fallback: a model for indexing two
facts of a short delivery note and the number on a page the app draws (a model that cannot read
images would fail every scan), a model for answering one turn with the agent's tools, in which it
must call one (one that answers without would answer questions without reading the documents).
It prints each request's outcome, how long it took and what the model used, says when a model
has no price in `MODEL_PRICES`, records nothing as an organisation's usage, and exits with 1 when
a model failed or answered in a way the app cannot use:

```text
INDEX_MODEL: ollama:qwen3-vl:4b
  ok      an indexing request: read note 12 and 40 pallets (13.1 s)
  ok      a scanned page: read 'Invoice 4711' (41.1 s)
          2 calls, 1,278 tokens in, 2,177 out; MODEL_PRICES has no price for it, so the daily spending cap counts none
AGENT_MODEL: ollama:qwen3.5:4b
  ok      a turn with the agent's tools: called list_documents (11.5 s)
          1 call, 1,645 tokens in, 150 out; MODEL_PRICES has no price for it, so the daily spending cap counts none
AGENT_FALLBACK_MODEL: ollama:gemma3:4b
  FAILED  a turn with the agent's tools: The request to gemma3:4b failed (400). The provider's error code: invalid_request_error. (0.0 s)
ollama:gemma3:4b did not answer as the app asks; until it does, the work above fails or falls back. Fix the setting or the key, or name another model.
```

(Ollama refuses tools for gemma3, so that fallback would have failed every question it was
given.) It costs a few thousand tokens per model. Run it after changing a model or a key, before
launch, and monthly: beside the restore drill's timer ("The drill"), a unit that runs it and
mails on failure keeps a fallback from going stale unseen.

**The golden questions.** A model can answer every request of `models` and still answer
members' questions worse than the one before it, and so can the agent after a change to its
prompt or its tools. `python -m app.maintenance golden` asks the agent thirteen questions about
the sample project ("The sample project", below) whose answers are known
(`backend/app/golden.py`): three that read its invoices against the contract, the price list and
the delivery log, a detail of each kind
of document, two figures worked out from several (the three invoices' total, the end of the
contract's term), and one the documents do not answer. It copies the sample into an
organisation of its own, as the setup does, embeds it when the embeddings endpoint answers (the
search matches words alone otherwise, which its last lines say), asks each question as a
member's is asked, by `AGENT_MODEL` and its fallback, and deletes the organisation with whatever
it recorded: it costs the answers, about a dozen with a few tool calls each (USD 1.21 to 1.28 on
Claude Opus 5.5, 2026-10-31), and their judging (a cent on Claude Haiku 5.5), and no
organisation's usage. Each answer is graded by its words: it must say the figures and names a
right one says, in one of the ways the question lists ("43,476.65" holds "EUR 43 476.65" too,
"6%" never "16%"), and cite a passage of each document the question names, and never a
reference no tool showed it. Then `INDEX_MODEL`, which is not the model answering where the two
differ, judges each answer: it reads the question, the known answer in a sentence, the answer
and the whole of each passage it cites with where that passage is, and says whether the answer
is right and whether each passage states what the sentence citing it takes from it. The
judge's verdicts are reported and fail nothing, since a judge misreads too. The command prints
each question's verdict; a failed answer whole with what it lacks and what the judge made of
it; an answer the words pass and the judge doubts whole with the judge's reason; each citation
the judge finds unsupported with its sentence; the versions of the prompts that asked and judged
(`Prompt versions: answer fa439547, judge 99ad62fe.`, "The prompts' versions", below); what the
answers and the judging used and cost; the answers the judge found right and the citations it found supported, which is the answers'
citation precision; and exits with 1 when an answer was not as known by its words:

```bash
docker compose -f deploy/compose.yml run --rm --no-deps worker python -m app.maintenance golden
```

```text
  ok      What is the total due on invoice INV-2026-0458? (9.0 s)
  ok      How much notice must a party give to end the contract for convenience? (9.2 s)
          P180 does not support "It pays a penalty based on its average monthly fees multiplied by the months left in the term [P180]:": The passage says a penalty applies when the Customer terminates for convenience during the initial term, but does not state that it is based on average monthly fees multiplied by remaining months.
  ok      What was the on-time delivery rate in August, and what was the target? (8.1 s)
          judged wrong: The answer gives 94% in August against a 97% target, matching the known answer. It then hedges that the figure may be wrong and says the log does not match it, which the known answer does not support, so it is not right.
          | According to the supplier review meeting notes, on-time delivery in August was **94%**, which missed the **97% target** [P201]. …
  …
Answered by anthropic:claude-opus-5-5: 48 calls, 253,744 tokens in, 9,845 out, USD 1.21.
Judged by anthropic:claude-haiku-5-5: 13 calls, 33,849 tokens in, 16,119 out, USD 0.01.
The judge found 11 of 13 answers right, and 72 of 84 citations supported by the passage cited (86%).
Every golden question answered as known (13 asked).
```

(Claude Opus 5.5 answering and Claude Haiku 5.5 judging, 2026-10-31: the first citation sends
the reader to the clause that introduces the penalty table, not to the table, and the answer
the judge doubted was right, its doubt about the 94 % one the delivery log gives reason for.)
Run it after changing `AGENT_MODEL`, `INDEX_MODEL` or a fallback, after a change to the agent's
prompt or tools, and before a launch. A right answer that failed for its wording, a figure
written in a way its question does not list, is read in the printed answer (the judge's
"judged right" under it says as much), and the way joins the question's list in `golden.py`.
Words are a floor: an answer that names the right figure for a wrong reason passes, and "no" is
in many sentences, which is what the judge's doubts are read for. Read a doubt as a question,
not a verdict: the judge reads a passage alone, and calls a true detail or a fair doubt wrong at
times.

**The facts read.** `python -m app.maintenance fact-accuracy` asks the same of `INDEX_MODEL`:
it indexes the sample's files as uploads, with extraction templates on, in an organisation of its
own that it deletes after, and scores the facts read from the contract, the three invoices and
the price list against values labelled by hand (`backend/app/fact_accuracy.py`), a field of the
built-in template of each kind at a time: its recall, the labelled values some fact of the field
holds, and its precision, the field's facts that hold a labelled value, a field the document does
not state counting any fact of it wrong. A fact holds a value by its figure, its day or its words
in one of the ways listed. It prints each field's verdict, with the values missed and the facts
read wrong, the version of the prompts the facts were read with (`Prompt versions: enrich
62252e89.`), what the reading cost, and the recall and precision over every field, and exits with
1 when a field was not read as labelled. On 2026-10-31 Claude Haiku 5.5 read 71 to 73 of the 79
values, every fact it read right, for USD 0.01 a run; what it missed changed from run to run (an
invoice's unit prices and quantities, which its line items held, or the contract's early
termination rates). Run it after changing `INDEX_MODEL` or its fallback, the enrich stage's
prompts or a built-in template; a right reading worded in a way the labels do not list gets its
way added there.

**Planted instructions.** A document is whatever its writer put in it, and both models read it,
so what the agent's tools return reaches the model inside tags its prompt names as data, never
instructions, and the indexing model is told the same of the document it is shown; a tag of
theirs that a document writes reaches the model as none. `python -m app.maintenance injection`
tries that against the models the deployment names: it indexes, as uploads, a supplier's
documents that each tell whoever reads them what to do (`backend/app/injection/`: an invoice that
says it is approved and gives a code to repeat and a passage to cite that does not exist, an
agreement that asks for a draft and the organisation's other projects, a delivery note and an
email that close the prompts' tags and dictate a summary or the end of every answer, and a rate
card whose summary the run replaces with one that says to refuse every question), beside another
project's payroll, in an organisation of its own that it deletes after. It checks what
`INDEX_MODEL` wrote of each document (title, kind, summary, sections, facts) for the words only an
instruction followed would put there, then asks the agent a question about each as a member's
question is asked, offered a draft: an answer must say what a right one says and cite the documents it should, and fails for a planted word, a reference no tool
showed, or a draft nobody asked for. It prints each document's and each question's verdict, a
failed answer whole, the versions of the prompts and what the run cost, and exits with 1 when an
instruction was followed or an answer was not right. Run it after a change to the agent's or the
indexing model's prompts and after changing either model.

**The prompts' versions.** Each kind of model work (an answer, a question set's answer, a draft,
a discrepancy report, reading a scan, finding a document's sections, enriching it, the golden
questions' judge) asks with prompts whose version is the first
eight hex digits of a hash of what the model is told for it (`backend/app/prompts.py`): its
instructions, the tools it is offered and the schema of its reply, never the documents or the
member's words. Nobody bumps a version: an edit to any of them gives a new one with the release
that carries it, and a version that stayed means none was edited, so two golden or fact-accuracy
runs with the same versions differ by the model alone. The usage rows of each kind's model calls
carry its version, which `costs` splits each kind's cost by ("What each organisation costs",
below); the rows recorded before 2026-11-01 carry none.

**Every morning, from GitHub.** The Models workflow (`.github/workflows/models.yml`) runs the
same command at 05:47 UTC, and the golden questions beside it in a job of their own against the
development compose file's database (no embeddings endpoint runs there, so their search matches
words alone); both also run once a change to what they ask with (`llm.py`, `trial.py`,
`golden.py`, the agent, the search, the sample) is on `main`, and by hand from Actions, Models,
Run workflow, once the repository names the models: the variables `AGENT_MODEL` and `INDEX_MODEL`, with `AGENT_FALLBACK_MODEL`,
`INDEX_FALLBACK_MODEL` and `MODEL_PRICES` as the deployment sets them, and the
keys their providers need as the secrets `GOOGLE_API_KEY`, `ANTHROPIC_API_KEY`,
`OPENROUTER_API_KEY` or `OPENAI_API_KEY` (Settings, Secrets and variables, Actions, or `gh
variable set AGENT_MODEL --body google:gemini-3.5-flash-lite` and `gh secret set
GOOGLE_API_KEY`). Without the two variables it is skipped. GitHub mails a failed scheduled run
to whoever last changed the schedule. It is the one workflow that holds a key, so it runs from
`main` alone and never on a pull request. Give it keys of its own, made for it at each provider
with a low spending limit, rather than production's: one that leaks from GitHub is then revoked
without touching the deployment, and production's stay in its secret manager. Set the limit
with the golden questions in mind: about a dozen answers of `AGENT_MODEL` a morning and as many
calls of `INDEX_MODEL` judging them, and as many again for each change to the agent that
reaches `main`. The variables
follow production's settings by hand: change them with the model, or the workflow goes on
asking the one production stopped using. A model the deployment reaches on its own network
(`ollama:`, an `OPENAI_BASE_URL` there) or through a service account (`google-cloud:`) cannot be
asked from GitHub; run the command on the deployment for it, as above.

**Retirements.** Every provider retires models on a schedule of its own, and says so first in
these places (October 2026):

- **The Gemini API** (`google:`): the deprecations page,
  <https://ai.google.dev/gemini-api/docs/deprecations>, which lists every model's earliest
  shutdown date and its replacement, and the release notes,
  <https://ai.google.dev/gemini-api/docs/changelog>, where announcements land first. Neither
  offers a feed or a mailing list: put both on the operator's monthly list. A preview model can
  go with two weeks' notice; a `-latest` alias moves with every release, with two weeks' notice
  by mail only for a breaking change.
- **Vertex AI** (`google-cloud:`): "Model versions and lifecycle",
  <https://docs.cloud.google.com/vertex-ai/generative-ai/docs/learn/model-versions>, with dates
  of its own, which can differ from the Gemini API's for the same model. Point the project's
  notification contacts at an address the operator reads.
- **Anthropic** (`anthropic:`): <https://platform.claude.com/docs/en/about-claude/model-deprecations>.
  Anthropic mails the customers who use a model at least 60 days before it retires a publicly
  released one; the console's usage export shows which key used which model. Claude through
  Bedrock or Vertex AI follows those platforms' dates instead.
- **OpenAI** (`openai:`): <https://developers.openai.com/api/docs/deprecations>. OpenAI mails
  the customers who use a model, at least six months ahead for a generally available model and
  as little as two weeks for a preview.
- **OpenRouter** (`openrouter:`) serves a model while its provider does: follow the provider's
  page.

The mail goes to the provider account's address, so make it the operator's shared one (the
`ALERT_EMAIL` address serves), not one person's. When a retirement is announced, name the
replacement as the fallback first and run `models`; then make it the primary, put the old one's
replacement or another provider's model in the fallback, and move `MODEL_PRICES`. A model that
retires unnoticed fails with 404, and the fallback answers while the log says the model "was not
found, or is served no longer".

## The sample project

The workspace the setup makes ("The first run, and who signs up") starts with a project named
"Sample: Acme supplier contract": a supplier's contract, three of its invoices, the price list,
the delivery log, an email and meeting notes, which a question can read against each other (an
invoice that bills 340 pallets without the contract's discount, two late deliveries). Everyone
who joins later, by an invitation or a sign-up, finds it there; a member removed from their last
organisation is given one of their own without it. `SAMPLE_PROJECT=false` turns it off.

Nothing is indexed as the workspace is made. The image carries the files and what the pipeline
made of them (`backend/app/sample/index.json`: blocks, outline, passages, summaries, facts), and
the setup copies them in its own transaction: the files into the storage under the new
documents' keys, the rows with new ids. So the copy calls no model and costs the organisation
nothing of its month; only the vectors are the deployment's, made by each document's index job,
which runs the embed stage alone at once (a few seconds against the compose file's server, the
passages found by their words meanwhile) and records its `embed` usage as any document's does.
The scan stage says the file is the app's own. The usage page leaves the project and its
documents out of what the workspace holds, and it takes no uploads (409); its questions count as
any do, and a member deletes it like any project. The
audit log records the copy as the app's own `sample_added`.

A storage that refuses the files leaves the organisation without the sample, logged as
"Organisation 12 starts without the sample project: …", and the account is made all the same;
a file stored before the failure is the orphan sweep's. A start with `SAMPLE_PROJECT` on refuses
an image whose sample files are not those its index describes.

`python -m app.maintenance build-sample` makes the index again, in a checkout: it indexes the
files with the configured models in an organisation of its own that it deletes after, and
writes the file to commit. Run it after changing the files, or when a change to the parsers or
the pipeline should show in the sample; until then the index describes the same files with the
older code's outline, which is not wrong. The index first committed holds the enrich stage's
replies written by hand (`built.models` says `hand:`), each fact's quote checked against its
block by the stage as a model's is; the next build with a model replaces them.

## The way in

`/` is the app: a member finds their projects there, and a visitor without a session is sent to
the sign-in, which returns them to the page they wanted. An install is a team's own server, which
no search engine should list: `/robots.txt` turns every crawler away, and every page says
`X-Robots-Tag: noindex` besides, for a crawler that came by a link. The public site and its
booking link went on 2026-11-13. The versioned API and its contract at `/api/v1/openapi.json`
stay.

### The first run, and who signs up

An install starts with no account (`backend/app/signups.py`). Until one exists every page sends
to `/setup`, where whoever runs the install types the admin's name, address
and password and the team's name. The page makes the account, its address confirmed, an owner of
the workspace named so, which starts with the sample project ("The sample project"), and signs
it in. It is refused once any account exists, under a lock, so that two browsers at once make
one admin. Until then whoever reaches the install first makes themselves its admin, so set it
up before its address is public, or from the shell. A script makes the same without a browser,
with no password, and prints the link that sets one:

```bash
maintenance create-admin ada@example.com --name "Ada Lovelace" --workspace Acme   # refused once any account exists; the link works once, within a day
```

After that, people come in by invitation. The owner or admin who invites someone, or sends an
invitation again, is shown its link to copy and hand on as well as the mail sent with it, since
an install may have no mail relay ("Mail"); without one, the page says that nothing was mailed.
The address is then the inviter's word rather than the mail's proof. A member who forgot their
password where nothing is mailed is given a link that sets it: an owner or admin makes one from
the members list, for a member within their own role, which the audit log keeps, and the
server's shell prints one:

```bash
maintenance reset-password grace@example.com   # the link, working once within a day; the account's earlier ones stop
```

The install holds that one workspace: nobody starts another, leaves it, joins it by their
address's domain or deletes it. A member an owner or admin removes keeps their account, in no
workspace, and signing in shows them a page that says so and to ask an admin; an invitation to
their address brings them back.

`SIGNUP_OPEN` is off by default: the sign-up form refuses every address alike, with a sentence
that says to ask the team's admin, and a first sign-in through Google or Microsoft makes no
account; the sign-in page offers no account either, and says to ask the team's admin for an
invitation. Set to `true`, anyone who reaches the install signs up by the link mailed
to their address, or through a provider, and joins the workspace as a member; a sign-up link
stops working once the sign-up is closed again. Either change
takes effect at once, with no release.

## TLS at a reverse proxy

Nothing in the app speaks TLS. A reverse proxy holds the certificate, answers the browsers on
443, and forwards each request as plain HTTP to uvicorn on `127.0.0.1:8000`, which nothing
else can reach. The app reads three things from a request that the proxy, not the browser,
decides:

- **The scheme**, for the `Secure` flag on the session cookie and the OAuth handshake cookie.
  In production the flag is set whatever the scheme, since a deployment is always behind TLS;
  a request that still arrives as plain HTTP is logged as the misconfiguration it is, with
  what to fix.
- **The client's address**, kept on each session and shown on the account page beside the
  browser. Without the proxy's say, every session comes from the proxy.
- **The host**, for the links in emails and the OAuth redirect URI. Production takes it from
  `APP_URL`, which it requires, so no proxy header is needed for this one.

uvicorn fills the first two from the proxy's `X-Forwarded-Proto` and `X-Forwarded-For`
headers, and only when the connection comes from an address it was told to believe:
`--proxy-headers` is on by default, and `--forwarded-allow-ips` defaults to the
`FORWARDED_ALLOW_IPS` environment variable, or `127.0.0.1,::1` without it. A proxy on the same
machine therefore needs nothing more than the default command:

```bash
cd backend && uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

A proxy that connects from another address, such as another container or the gateway of the
image's network ("The image"), needs that address named: `FORWARDED_ALLOW_IPS=10.0.0.5`, or a
network as `10.0.0.0/24`, beside the app's own
variables (it is uvicorn's, not a setting of the app, so it does not go in `.env`). `*` believes
every connection, which is right only when nothing but the proxy can reach the port at all:
anyone else who can would choose the address their session shows and claim to be on HTTPS.
The proxy must also pass the browser's `Host` header on unchanged, which both examples below
do, so that the frontend's requests are served under the name the user opened.

The frontend and the API share that one name on purpose. The app answers no request from
another origin, neither with CORS headers nor with a preflight, and its defence against a
request forged from another site, the session cookie's `SameSite=Lax` and the
`X-Requested-With` header on every request that changes something, rests on that. So the
proxy serves both from the one site block, as the examples do, and the built frontend is not
served from a CDN or under a second name.

The browser's security headers are the app's, on every response: `Content-Security-Policy`,
`X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy` and `Permissions-Policy`, and
`Strict-Transport-Security` for a year once a request arrives over HTTPS by the proxy's
account, and always in production. The proxy adds none of them, and must not set
`Content-Security-Policy` across the board: a file the app sends itself (an original, a page
image, a thumbnail) carries `sandbox` in place of the page's policy, so a browser runs nothing
in an uploaded file, and an original comes as a download. The app's HSTS header names
neither `includeSubDomains` nor `preload`, which bind every subdomain of the name to HTTPS and
are the operator's decision; a deployment that wants them replaces the header at the proxy, as
`header Strict-Transport-Security "max-age=63072000; includeSubDomains; preload"` in the Caddy
site block, or `proxy_hide_header Strict-Transport-Security;` with an `add_header` of its own
in the nginx location.

How long a browser keeps the page's own files is the app's word too. The scripts and the
stylesheet under `/assets/` are named by a hash of what they hold, so a release that changes
one names it anew, and they carry `Cache-Control: public, max-age=31536000, immutable`: kept a
year, never asked about again. The page itself, `theme.js` and `favicon.svg` keep their names
from release to release and carry `no-cache`: kept, but asked about at each visit, which an
unchanged file answers with a 304 and no body. So a browser loads a release's page at its next
visit, and its scripts only when the release changed them. The API's answers carry `no-store`
and are kept by nobody. What the app does not do is compress: the proxy does that, for the
page's files alone, as both examples do. The script is 605 kB as built and a third of it
compressed, and CI holds it to a budget (`frontend/scripts/size.ts`). The API's answers are
left as they are: they are small, an answer's event stream must reach the browser as each
event is written, and an answer that holds a member's text beside words a visitor chose (a
search) would let someone watching the connection learn about the text from the compressed
length (BREACH).

An answer arrives as a stream of server-sent events (`GET
/api/chats/{id}/messages/{id}/events`), a response that stays open until the answer is
finished and carries nothing while the model thinks. The proxy must pass it on as it comes
rather than whole: the app sends `X-Accel-Buffering: no` on it, which nginx honours, and Caddy
streams a `text/event-stream` response as it is. And it must not end the connection as idle:
the app writes a comment line on the stream whenever nothing else was sent for 15 seconds, so
a proxy's or load balancer's idle timeout, a minute for most, never runs out, and the default
timeouts of both examples serve. Should a connection drop all the same, the browser follows
the answer again from the last event it saw and nothing is lost.

### Caddy

Caddy obtains and renews the certificate itself, sets the two headers and passes `Host`
through, keeps its connections to the app open between requests, and streams an answer's
events as they come with no read timeout unless one is set. This is the whole configuration,
as `/etc/caddy/Caddyfile`; the compose file's `caddy` service, with the `tls` profile, runs the
same with `{$APP_URL}` for the name (its `x-caddyfile`, which the container writes to its tmpfs,
since Compose cannot put a file into a read-only container), on the host's network so that it
reaches the API where this one does:

```caddyfile
emudoc.example.com {
    # Caddy keeps no access log without this block; with it, the query is cut from each
    # address, since it carries a search's words ("Logs" below).
    log {
        format filter {
            wrap json
            fields {
                request>uri regexp \?.*$ ?…
            }
        }
    }
    # The upload routes alone, the page's and the versioned API's, take a large body,
    # MAX_UPLOAD_REQUEST_MB (500 by default); the proxy refuses a larger one before the app
    # has read it. Every other request is small.
    @uploads path_regexp ^/api/(v1/)?projects/[0-9]+/documents$
    request_body @uploads {
        max_size 500MB
    }
    @others not path_regexp ^/api/(v1/)?projects/[0-9]+/documents$
    request_body @others {
        max_size 1MB
    }
    # The page's own files compressed, the script to a third of its size; the API's answers
    # as the app sends them, an answer's events among them.
    @page not path /api /api/*
    encode @page gzip
    # Each request's id, which Caddy's access log names `uuid` and the app's lines
    # `request_id`; one the browser sent is replaced ("Logs" below).
    reverse_proxy 127.0.0.1:8000 {
        header_up X-Request-ID {http.request.uuid}
    }
}
```

### nginx

nginx needs the headers set by hand, the body limit, and HTTP/1.1 with the `Connection` header
cleared towards the app: without both it opens a connection per request instead of keeping
the ones in the `upstream` block's pool. Its default `proxy_read_timeout` of a minute is
enough for an answer's stream, since the app's keep-alive comes every 15 seconds of silence.

```nginx
# The path without its query, which carries a search's words ("Logs" below), and no Referer;
# last, the request's id, which the app's lines name too.
log_format emudoc '$remote_addr - [$time_local] "$request_method $uri $server_protocol" '
                  '$status $body_bytes_sent $request_time $request_id';

upstream emudoc {
    server 127.0.0.1:8000;
    # Connections to the app kept open between requests.
    keepalive 16;
}

server {
    listen 443 ssl;
    server_name emudoc.example.com;
    ssl_certificate     /etc/letsencrypt/live/emudoc.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/emudoc.example.com/privkey.pem;
    access_log /var/log/nginx/emudoc.log emudoc;

    # Every request is small, except an upload: MAX_UPLOAD_REQUEST_MB, 500 by default, on
    # its routes alone, the page's and the versioned API's. The proxy refuses a larger body
    # before the app has read it.
    client_max_body_size 1m;
    # HTTP/1.1 and no Connection header towards the app: the pool above needs both.
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    # Each request's id, in place of any the browser sent.
    proxy_set_header X-Request-ID $request_id;
    # The page's own files compressed, text/html being always among the types; the API's
    # answers, JSON and an answer's events, are not among them.
    gzip on;
    gzip_vary on;
    gzip_comp_level 5;
    gzip_types text/javascript text/css image/svg+xml;

    location / {
        proxy_pass http://emudoc;
    }

    location ~ ^/api/(v1/)?projects/[0-9]+/documents$ {
        client_max_body_size 500m;
        proxy_pass http://emudoc;
    }
}

server {
    listen 80;
    server_name emudoc.example.com;
    return 301 https://$host$request_uri;
}
```

`$proxy_add_x_forwarded_for` appends the client to any `X-Forwarded-For` the browser sent, and
uvicorn takes the rightmost address it was not told to believe, so a header forged by the
browser changes nothing.

## Encryption

The proxy ends the browsers' TLS, above. Behind it the app hands the documents to the database,
the bucket, the scanner, the embeddings endpoint and the models, and sends sign-in links
through the mail relay; how each connection must be protected depends on where it goes. What
the deployment keeps on disk is the other half.

### Between the pieces

The app draws one line, the deployment's own network: the loopback, private addresses
(`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `fc00::/7`), link-local ones, a name without
a dot such as a compose service, and names under `.internal`, `.local`, `.localhost`, `.lan`
and `.home.arpa` (`on_own_network` in `backend/app/network.py`). A connection that leaves it
crosses networks nobody in the deployment controls, so it must be encrypted and must check
that the other end is the host it named, or anyone in the path could read it or stand in for
the server. Production refuses to start otherwise, and says which:

- **The database**: a host outside needs `sslmode=verify-full` on `DATABASE_URL`, which
  encrypts and checks that the server's certificate names the host:
  `?sslmode=verify-full&sslrootcert=system` for a certificate a public authority signed, or
  `sslrootcert=` the path of the provider's own authority (AWS publishes a bundle for RDS).
  `require` encrypts to whoever answers, and `verify-ca` takes any server the authority signed
  for, another customer's database at the same provider among them, so production takes
  neither. The API's pool, the worker's listener, alembic and the maintenance commands all
  connect by the one URL (the listener by `DATABASE_LISTEN_URL` when it is set, held to the
  same rule). `sslrootcert=system` needs libpq 16 or later; psycopg's binary
  package brings libpq 18, with an OpenSSL that looks for the system's authorities in a
  directory of the machine it was built on, so the process needs `SSL_CERT_FILE` naming the
  system's bundle: `/etc/ssl/certs/ca-certificates.crt` on Debian, Ubuntu and Arch,
  `/etc/pki/tls/certs/ca-bundle.crt` on Fedora and RHEL. The image sets it. Without it every
  certificate would fail, "SSL error: certificate verify failed", while `psql`, which has the
  system's libpq, connects; so production refuses to start with `sslrootcert=system` and no
  `SSL_CERT_FILE` or `SSL_CERT_DIR` that is there, naming this machine's bundle, and
  development warns.
- **The embeddings endpoint, a model's address and the bucket** (`EMBEDDING_BASE_URL`,
  `OLLAMA_BASE_URL`, `OPENAI_BASE_URL`, `AWS_ENDPOINT_URL`) need `https://` when they are
  outside. The HTTP clients check the certificate by default, and nothing in the app turns
  that off. The model providers' own APIs are HTTPS whatever the settings say.
- **ClamAV**: clamd's protocol has no encryption to ask for, so production takes a daemon on
  the own network only: a Unix socket, a private address, a compose service. A scanner on
  another network is reached through an encrypted tunnel that listens on the own network, such
  as WireGuard or stunnel.
- **The mail relay** is always reached over TLS, STARTTLS for `smtp://` and TLS from the start
  for `smtps://`, and its certificate must name the relay's host and come from an authority the
  machine trusts (the system's store, `/etc/ssl/certs`). A relay with a certificate of a private
  authority needs that authority added to the store (`update-ca-certificates`, or
  `update-ca-trust` on Fedora and RHEL). A relay that fails the check is sent nothing, and the
  API's log says which relay and why: the sign-in and password-reset links go this way.
- **Sign-in with Google and Microsoft** goes to their HTTPS addresses, which are fixed in the
  code.

Inside the own network the app takes plain connections: the proxy to uvicorn on the loopback, a
compose database, the embeddings server, ClamAV. That network is the operator's to protect. On
one machine or one compose network nothing crosses a wire. Where it spans machines over a
network the operator does not own, such as a cloud's private network between hosts, encrypt it
underneath (WireGuard, or the provider's encrypted private networking) or encrypt those
connections as well: `sslmode=verify-full` and `https://` work inside the network as anywhere.

### At rest

The app cannot see a disk, so most of this is the operator's to set up and to confirm before
launch. Where the deployment keeps what it was sent:

- **The database** holds the documents' text (every passage, section summary and fact), the
  questions and answers, members' names and addresses, password and session hashes, and the
  authenticator secrets of two-factor sign-in, which the app encrypts itself with a key the
  database never holds, since a secret makes its member's codes ("The encryption keys").
  Postgres has no encryption of its own, so the volume under it is encrypted: LUKS on a
  server, or a managed database's encryption at rest (on RDS it is chosen when the instance is
  created). Its backups and archived WAL carry the same data and need the same.
- **The bucket** holds the originals, the page images and the thumbnails, under its own
  default encryption. AWS encrypts every new object since January 2023 (SSE-S3, AES-256), and a
  bucket's default encryption can name a KMS key instead. Cloudflare R2 encrypts every object
  (AES-256-GCM). Hetzner's documentation (October 2026) names only SSE-C, a key sent with every
  request, which the app cannot use: a browser fetches page images by a signed URL that carries
  no key. The app asks the bucket at start (`GetBucketEncryption`, so its credentials need
  `s3:GetEncryptionConfiguration`) and logs the answer: "The bucket encrypts every file it keeps
  at rest (AES256)", a warning when the bucket says it has no default encryption, and a warning
  with the bucket's reason when it will not say. Neither warning stops the app, since a bucket
  on the own network may sit on an encrypted disk. The app sends no encryption with each object,
  so a bucket policy that denies uploads without `x-amz-server-side-encryption` refuses the
  app's; set the bucket's default encryption instead.
- **A directory** (`STORAGE_URL=file:///…`) is as encrypted as the disk under it.
- **Temporary files**: the API writes each upload to the temporary directory before storing
  it, and the API and the worker copy an original there from a bucket to render, scan and parse
  it, removing each copy when done. A project's export is written there whole before it is
  stored, so the worker's temporary directory needs room for the largest project's originals
  (a `tmpfs` is memory: size it, or point `TMPDIR` at an encrypted disk). The temporary directory (`TMPDIR`, `/tmp` unless set) needs
  the encryption the storage has, or to be a `tmpfs`, which keeps it in memory and in the swap,
  so encrypt the swap or turn it off; the image runs with one. Python passes over a `TMPDIR` it
  cannot write in for `/tmp`, so the API and the worker say at start where the files go
  instead, and refuse to start when they can write in no temporary directory at all.
- **ClamAV** writes each file it is streamed to its own temporary directory while it scans it
  (`TemporaryDirectory` in `clamd.conf`); on a machine of its own, that disk is encrypted too.

Keys chosen per organisation are a later step; until then the bucket's default encryption and
the disks' keys are the deployment's, the same for every organisation.

### The encryption keys

The disk's encryption protects a disk taken away, not a copy of the database: a dump, a backup,
a restore drill's scratch copy, a read through a leaked password. So what in the database would
let its holder act as a member, the authenticators' secrets of two-factor sign-in and the grants
members gave to read the folders they connected ("Connected folders"), the app encrypts itself (AES-256-GCM, each value bound to its row, so a value copied into another
account's row decrypts nowhere; `backend/app/keyring.py`, `backend/app/encrypted.py`), with
keys from `ENCRYPTION_KEYS`, which the database never sees. Passwords and session tokens are
hashes and need no key.

Make a key with `python -m app.keyring` (in the image, `docker compose run --rm api python -m
app.keyring`; `openssl rand -base64 32` makes one as good), which reads no settings and so runs
where they lack one, and put it where the other secrets are kept: on one machine in the
settings' file, on AWS under the secrets path ("The settings"). Production refuses to start
without one, and with development's, which is in the code. Keep it apart from
the database's backups, or encrypting gains nothing; and keep it as surely as the backups, since
a lost key loses every authenticator it encrypted. A member whose secret no key decrypts is told
to sign in with a recovery code and set the app up again, and a connected folder whose grant no
key decrypts pauses until its member connects it again; the start, the `encrypt-secrets` command
and the drill name how many there are and which key they need.

`ENCRYPTION_KEYS` holds several keys, separated by commas: the first encrypts, and every one
decrypts what it encrypted (a value names its key by the first eight hex digits of the key's
SHA-256, which the start's line "Secrets in the database are encrypted with key …" shows too).
To rotate:

1. Make a new key and put it first, the old one after it, then release the same tag. The
   authenticators keep working, and new ones are encrypted with the new key.
2. `python -m app.maintenance encrypt-secrets` (in the image, `docker compose run --rm --no-deps
   worker python -m app.maintenance encrypt-secrets`) encrypts every secret with the first key,
   beside the running app: a row is written only if it still holds what was read, so an
   authenticator set up meanwhile stays. It exits with 1 when a secret's key is not in the list.
3. Keep the old key in the list for `BACKUP_DAYS` more: a restore brings the rows back as they
   were, encrypted with it, and the monthly drill fails on a secret whose key is gone. Then
   remove it and release again.

A key that leaked is rotated the same way at once; a copy of the database taken with it still
holds what the old key decrypts. The secrets from before encryption (2026-10-20) stay readable
as they were until `encrypt-secrets` runs once, and the start warns while any are left.

## What leaves the deployment

What a team needs to know to choose its providers: which settings send what, and to whom. The
documents are invoices, contracts and bank statements, and three settings decide which
companies read them, with the fallbacks of the first two ("The models"), which are sent the
same:

- `INDEX_MODEL` is sent the text of every document, to infer its sections, write summaries and
  pull out facts, and an image of every scanned page and uploaded image, to transcribe it.
- `AGENT_MODEL` is sent each question, the project's name and description, and every passage,
  fact, outline and list of documents the agent reads to answer it.
- `EMBEDDING_BASE_URL` is sent every passage as it is indexed and each search. llama.cpp's
  server beside the app, as the compose file runs it, sends nothing out (its weights come in
  once, from Hugging Face); a hosted endpoint is a company like the others ("The embeddings
  endpoint").

Each must be on terms that let it use none of this to train or improve its models, under a data
processing addendum the team has accepted. Where the providers stood in October 2026 (read
their terms again before choosing; they change):

- **The Gemini Developer API** (`google:` models, `GOOGLE_API_KEY`). Google's paid terms apply
  only while the key's Google Cloud project has an active billing account: Google then uses
  neither the prompts nor the replies to improve its products, processes them under its data
  processing addendum, and logs them for a limited time to detect abuse. A key made in AI Studio
  is on the free tier until billing is on, and on the free tier Google uses what it is sent to
  improve its products and has people read some of it. Nothing in a key or a reply tells the
  two apart, so every production start warns about `google:` models until
  `GOOGLE_PAID_TIER=true` says billing is on. Set it after looking at the project's billing
  page, not before.
- **Vertex AI** (`google-cloud:` models). Google Cloud's terms and its data processing addendum,
  through the project's billing account, and a region of the operator's choosing for the
  requests (`GOOGLE_CLOUD_LOCATION`, `us-central1` unless set; `eu` keeps them in the EU). It
  signs in as a service account: `GOOGLE_APPLICATION_CREDENTIALS` names the account's key file
  and `GOOGLE_CLOUD_PROJECT` its project, or on Google Cloud's own machines the account the
  machine runs as. Leave `GOOGLE_API_KEY` unset beside it: without a key file, Vertex takes that
  variable as an express-mode key, whose free trial is not a production account.
- **Anthropic** (`anthropic:`). Its Commercial Terms, which every API key is under, forbid
  training on customer content, and its data processing addendum is part of them.
- **OpenAI** (`openai:`). API data is not used for training unless the organisation opts in;
  its data processing addendum is signed through OpenAI's own form.
- **OpenRouter** (`openrouter:`) forwards each request to one of the model's providers, and left
  to itself may choose one that stores prompts and trains on them. The app asks it on every
  request to choose among the providers that do neither (`data_collection: deny`, `DATA_POLICY`
  in `backend/app/llm.py`); a model none of them serves fails with OpenRouter's sentence rather
  than reach another. OpenRouter is a company in the path itself: keep the account's prompt
  logging off, and ask its business plans for a data processing addendum.

The rest receive less, and only where a setting turns them on:

- **A bucket** (`STORAGE_URL=s3://…`) holds every file uploaded, its rendered pages and the
  project exports; a directory keeps them on the deployment's own disk ("Backups").
- **The mail relay** (`SMTP_URL` or `RESEND_API_KEY`) is sent every mail the app writes: the
  members' names and addresses, the sign-in links, and the notices, which name projects and
  files ("Mail"). The provider that receives the projects' own mail ("Mail in") hands the
  messages to the app, so it holds what they say.
- **The services folders are connected from** ("Connected folders") are read from and sent
  nothing of the projects'.
- **Google and Microsoft**, when sign-in with them is set up, learn who signs in, as an
  identity provider does.
- **The error tracker** (`SENTRY_DSN`, "Error tracking") is sent what the log would show of an
  error nobody planned for, never a document's text, a question or an answer.
- **Have I Been Pwned** (`PWNED_PASSWORDS_URL`) is sent the first five characters of the SHA-1
  of each new password, never the password ("Rates").

A service on the deployment's own network (a private or loopback address, a compose service's
name, a name under `.internal` or `.local`) sends nothing to another company. The app sets only
the two cookies it needs to work and loads nothing from other sites.

## Mail

What the app mails, and to whom:

- **What an account needs said**, sent whatever its member chose: the link that makes an
  account and, to an address that has one already, the way back into it; the link that
  confirms an address or a new one, and to the address left the news that it was; the reset
  link; that sign-ins from new browsers were paused; that the account is set to be deleted;
  and an invitation.
- **Notices**, the mail a member may do without, about their work in the app rather than their
  account (`backend/app/notices.py`): to the member who uploaded files, once the last of them is
  ready, failed or set apart by the scan with no retry waiting, if that took five minutes or
  more (`NOTICE_AFTER`; an upload indexed sooner was most likely watched in the library, which
  says as much), one message for the whole upload naming the files that could not be indexed
  and why, as the library words it; to the members a project's automation names (its maker,
  everyone who sees the project, or nobody), once the run it made after documents arrived is
  worked out and its report written or failed, what its cross-checks found about them, in counts
  and file names, never a value a document states, and nothing when they found nothing about them
  (`tell_run` in `backend/app/check_runs.py`); to the member another assigned a document's review,
  who did and which file, in the change's transaction (`tell_assigned` in
  `backend/app/reviews.py`), or an automation's run several, once, naming the first three
  (`put_in_review`); to whom a project's question set names (its maker, everyone who sees the
  project, or nobody; a member's own run to them), once its report is answered, the set's name
  and how many of its questions were answered, never a question or an answer (`tell_report` in
  `backend/app/question_reports.py`); to a member who turned on the weekly digest (off until
  chosen, by them or as the organisation's default), on Monday from 08:00 of their zone, the
  documents that arrived over the week in each project of the organisation they see, by name
  and count, nothing for a week with none (`backend/app/digests.py`: a `send_digests` job that
  wakes at the top of every hour, queued again by the housekeeper when lost); to a member
  another mentioned in a comment, and to the earlier writers of a thread another replied in, who
  wrote and on which document, never the comment's words, in the comment's transaction and only
  while they see the project (`tell_comment` in `backend/app/comments.py`). Each notice
  is told twice, in the same transaction: in the member's notification centre, under the bell
  in the app's header (`notifications`, kept `NOTIFICATION_DAYS`, 90, read or not, and gone
  with the membership of the organisation it is about), and by email. A member chooses each
  way of each kind on the account page (`users.notices`, every way on until they choose but the
  weekly digest's, off until chosen); the
  link at the foot of a notice turns its kind's email off, and a mail program's own
  "Unsubscribe" does in one click: a notice carries `List-Unsubscribe` and
  `List-Unsubscribe-Post: List-Unsubscribe=One-Click` (RFC 8058), whose address
  `/api/mail/unsubscribe` takes the token in it, the member's id and the kind encrypted with
  `ENCRYPTION_KEYS`, as a mail program posts it, without a session (a token mailed before the
  kinds were chosen apart names the member alone, and turns every kind's email off). A notice
  is not mailed to an address the provider said to spare (below). A browser may show the
  notices as they come too, as the computer's own notifications while the app is open in a tab
  in the background: the browser's choice, on the account page, kept in its own storage.
- **The operator's reports** to `ALERT_EMAIL` ("What the worker mails").

Every message has a plain text and an HTML part, both made from the same template, in one
layout of the brand's colours; the only links in the HTML are the app's own, and the names a
member typed (an inviter's, an organisation's) stay text there, however much they look like an
address. A subject is the app's own sentence, which the log may repeat; names are in the body
alone. The links lead to `APP_URL`, which production requires; in development without it, a
request's mail links to the address the request came to, and the worker's to the frontend's
development server, `http://localhost:5173`.

An upload is the files of one request (`uploads`, with the member who sent them, made once the
request has stored them all). Whoever ends its last piece of work finds it indexed: the worker
once a document's index job has ended, the request itself for documents indexed before it was
done, and the housekeeper's round for one whose last word was lost with a worker that died. A
notice about a project the member no longer sees, or from an organisation they left, is not
sent. A document indexed again later ("Index again", a new embedding model) is told of by
nothing: whoever asked for it is watching.

### How it is sent

The request that calls for a message posts it to the queue, in the transaction of the change it
is about (`backend/app/postbox.py`): a `send_email` job, which a thread of the worker's own,
the mail lane, sends as it is queued, so that neither a backlog of documents nor one of
questions holds a link up, and no request waits on the relay. The mail lane is one thread per
worker (`MAIL_THREADS` in `backend/app/config.py`), counted in the pool the worker needs. Without a worker no mail goes
out: `EmudocNoWorker` and `EmudocMailWaiting` say so, and the queue keeps it.

A relay that says "later" (out of reach, a 4xx answer of SMTP's, Resend's 429 or 5xx) gets the
message again after 2, 4, 8 and 16 minutes; one that refuses it for good (a 5xx of SMTP's,
another of Resend's 4xx, a certificate this machine does not trust), or for the fifth time,
ends the job failed, which the operator hears of with the other jobs given up
(`EmudocJobsGivenUp`, the worker's digest, `python -m app.maintenance failed-jobs`, which names
it by its subject). Whoever waited for a link asks for another. The operator's reports skip
the queue, since the queue may be what they report on, and go through the relay at once.

A queued message with a link holds the link's token, which the database otherwise keeps only
as a hash, and a reset link signs in. So the job keeps the message encrypted with
`ENCRYPTION_KEYS` for the message's own key, and gives it up once it is sent or given up: a
finished job keeps the subject alone, for the operator, until the finished jobs are pruned
after a month. A key rotated meanwhile changes nothing, since the earlier key stays in the
list ("The encryption keys"); a message whose key is gone is given up and says so.

A worker that dies after handing a message over and before it recorded that, sends it again
once its lease runs out. Through Resend that is once: the message's key goes as its
`Idempotency-Key`, which Resend holds for a day. Through SMTP the member may get it twice.

### The providers

- **SMTP**: any relay, `smtp://user:password@host:587` (STARTTLS) or `smtps://…:465` (TLS), its
  certificate checked against this machine's authorities and for the relay's name. On AWS the
  module makes SES's, with credentials that may send from the domain and do nothing else.
- **Resend**: `RESEND_API_KEY` (an API key with sending access, `re_…`) sends through
  `https://api.resend.com/emails`, from `MAIL_FROM`, whose domain is added and verified in
  Resend's dashboard first.
- **Neither**: nothing is mailed. In development the log mailer writes each message, links and
  all, to the worker's log (where the API runs the worker). In production it writes a member's
  message by its subject alone, since its link may sign in, and the operator's reports to
  `ALERT_EMAIL`, which carry none, whole. A team runs so on purpose: an owner or admin hands
  invitations and password-reset links on from the members list (`python -m app.maintenance
  reset-password` prints one), the forgotten-password page says to ask them, and so does the
  account page for a member moving to a new address, whose link an owner or admin makes there
  too ("Change address…"), an owner or admin moving their own at once once their password
  confirms it. The help menu sends nothing and names whom to write to from one's own mail
  ("Support"), and the notices are told in the app alone, the account page and the
  workspace's offering no email choice. Nobody can finish a sign-up, which `SIGNUP_OPEN`
  would offer. Every start says so, and the Administration page's services show the mail as
  none.

Production refuses `SMTP_URL` and `RESEND_API_KEY` both set, a key that is not Resend's or a
webhook secret that is not one, quoting neither.

### The domain: SPF, DKIM and DMARC

A receiving server believes a message from `MAIL_FROM`'s domain when it can check it, and a
provider that is not believed lands in spam. Three records in the domain's DNS say so:

- **DKIM**: each message is signed with a key the DNS publishes, so that a receiver can check
  the domain sent it and nothing changed it.
- **SPF**: which servers may send for the envelope's domain (the Return-Path, where bounces go),
  a subdomain of the sending domain so that it aligns with the From domain.
- **DMARC**, `_dmarc.<domain>`: asks receivers to hold the domain's mail to SPF or DKIM aligned
  with the From domain, what to do with a message that fails (`p=none`, `quarantine` or
  `reject`), and where to send reports of it (`rua=mailto:…`).

On AWS the module makes all three for the `mail` domain: Easy DKIM's three CNAMEs, the envelope
from `bounce.<domain>`, whose MX is SES's feedback address and whose TXT is `v=spf1
include:amazonses.com ~all`, and `_dmarc.<domain>` with `v=DMARC1; p=quarantine`, the policy
from `mail.dmarc_policy` and the reports' address from `mail.dmarc_reports`; `dmarc_policy = ""`
makes none, for a zone that has its own already. With `dns_zone_id` they are made in Route 53,
and otherwise `tofu output dns_records` lists them. Until the MX is found SES sends from its own
envelope domain, and DMARC rests on DKIM alone. With Resend, its dashboard lists the domain's
DKIM record and the SPF and MX of its envelope domain; the `_dmarc` record is the
deployment's to add. Elsewhere, the relay's documentation says what to publish.

Check it by mailing a mailbox the deployment does not run (a reset link to an account of
one's own is enough) and reading the received message's `Authentication-Results` header:
`spf=pass`, `dkim=pass` and `dmarc=pass`, each for the sending domain. `dig TXT
_dmarc.<domain>` shows the policy.

### Bounces and complaints

A message to an address that does not exist bounces; a reader who marks one as spam
complains. A provider that keeps sending to such addresses is not believed for long: SES
reviews an account once 5 in 100 of its messages bounce, or 1 in 1,000 is complained of, and
may pause it at 10 in 100 bounced or 5 in 1,000 complained of. The provider says which addresses, and the app keeps each, as the
SHA-256 of the address and whether it bounced or complained (`mail_suppressions`,
`backend/app/bounces.py`), for a year, and sends it no notices meanwhile; a bounce the provider
calls transient (a full mailbox, a server that kept answering "later") is not kept. The mail an
account needs is still posted: the provider keeps a list of its own and drops what goes to an
address on it, and the app's list is for what the app chooses to send. A member who turns a
kind of notice on by email again on the account page has their address written to again.

- **SES**: the configuration set publishes its bounces and complaints to EventBridge, whose rule
  takes this environment's (by the configuration set, since the account's bus is both
  environments') into an SQS queue; the worker reads it every minute with the app role's
  credentials (`sqs:ReceiveMessage`, `sqs:DeleteMessage` on the queue, nothing else), from
  `MAIL_EVENTS_QUEUE`, which the module writes. A message the queue holds that is not an SES
  event is dropped with a warning. SES itself drops what goes to an address on the account's
  list; `aws sesv2 delete-suppressed-destination --email-address <address>` takes one off it
  once it works again.
- **Resend**: a webhook at `{APP_URL}/api/mail/events` in Resend's dashboard with the events
  `email.bounced` and `email.complained`, and `email.received` where Resend receives the
  projects' mail ("Mail in"; any other is taken and does nothing), its signing
  secret (`whsec_…`) in `RESEND_WEBHOOK_SECRET`. A request it did not sign, with that secret,
  within five minutes, is refused with 400 and a warning in the log; without the secret the
  route answers 404.
- **Another SMTP relay** says nothing to the app: the bounces go to the envelope's address, and
  every notice is sent.

`emudoc_mail_sent_total` and `emudoc_mail_events_total` count what was sent and what the
provider said ("Metrics"), and three rules watch them: `EmudocMailWaiting`, `EmudocMailRefused`
and `EmudocMailBounces` ("Rules on the metrics").

## Mail in

A project may have an address of its own, `<token>@INBOUND_DOMAIN`, whose mail becomes its
documents (`backend/app/inbound.py`; the library offers "Email in" only while `INBOUND_DOMAIN`
is set): the message's text as a `.txt` document named by its subject, with its From, To, Date and Subject
above it as an email reads, and each attachment as an uploaded file, through the checks an
upload's files go through (its type, its size, bytes the project holds already), into the
folder the address names or the project's root. Images an HTML message shows by their
Content-ID (a signature's logo) are the message's own and are left out. A message brings at most
`MAX_UPLOAD_FILES` files, and the largest a provider hands over is 40 MB, attachments and all.
The members who may change the project give it its address in the library's "Email in", choose
whom it takes mail from, give it a new address in place of one given out too widely (the old one
then reaches nothing) and take it away; each is a row of the audit log that counts the senders
and never names them.

Whom it takes mail from: the members who may change the project, from their own confirmed
addresses, whose upload it then is, and the senders listed, each an address or a domain (and
its subdomains), never a domain anyone may have an address at (gmail.com and the like), whose
documents are nobody's upload, so that no notice follows them. The From header is whatever its
sender wrote, so a message counts as its sender's only when the provider proved it: DMARC
passed for the From domain, or a DKIM signature of that domain did. One that is not proven, or
from anyone else, is turned away, and nothing is sent back, since a reply to a forged address
reaches someone who sent nothing; so is one for a project in the trash. The address's page tells its members why a sender's mail was
turned away: for want of DKIM, the sender's mail administrator can sign it, or a member can
forward it.

What became of each message is kept 30 days (`inbound_messages`, `INBOUND_DAYS`) for the
project's members, beside the address: its sender, its subject, the
documents kept, the files that were not and why, or why the whole message was turned away. A
message a provider delivers twice is kept once, by the provider's id for it. Nothing of a
message's text reaches the log, which names the project and the provider's id and what became
of it; `emudoc_inbound_messages_total` counts each by its `outcome` ("Metrics"), mail to no
project's address among them.

`INBOUND_DOMAIN` is a domain of its own for this, such as `in.example.com`, whose MX names the
provider that receives it: no mailbox of anyone's is there, and the domain's mail is the
projects' alone. Without it no project has an address and the page says so; with it,
production refuses to start unless a provider hands its mail over (below). `python -m
app.maintenance receive-mail <file.eml>` hands a message saved as it was sent to the projects
its To, Cc and Delivered-To name (or `--to <address>`, again for each), its sender taken as
proven since the operator vouches for it: for a message the provider kept that the app did
not take, and for trying an address by hand.

### Through Resend

Resend receives the domain's mail once the domain is added and verified in its dashboard, with
receiving turned on and the MX record it shows published for the domain (the lowest priority,
or Resend takes nothing). The webhook that tells the app of bounces (`{APP_URL}/api/mail/events`,
"Bounces and complaints") takes `email.received` too: an event for a project's address queues a
`receive_mail` job and the route answers at once, one for any other address is counted as
`unknown_address` and dropped. The worker then asks Resend's API for the message with
`RESEND_API_KEY` (a key with full access, since one that may only send cannot read what was
received) and downloads it as it was sent, with Resend's SPF, DKIM and DMARC verdicts: the
sender is proven when DMARC passed or DKIM did, which Resend passes only for a signature of
the From domain. A Resend that is out of reach or answers "later" (429, a 5xx) is asked again
after 1, 2, 4 and 8 minutes; a message it no longer holds, a refusal, or the fifth try ends the
job failed, among the jobs given up (`failed-jobs`, which names it by Resend's id), and the
message stays in Resend's dashboard, whence it can be downloaded and handed over with
`receive-mail`. Resend receives with sending (`RESEND_API_KEY`, not `SMTP_URL`).

### Through Amazon SES

SES receives the domain's mail where its MX is SES's receiving endpoint in the region (`10
inbound-smtp.<region>.amazonaws.com`; Frankfurt receives since 2023, and not every region does)
and SES has verified the domain, through a receipt rule of the account's one active rule set: SES
keeps one active a region, so staging and production, in one account and region, share the
account root's (`emudoc`, `deploy/aws/account/inbound.tf`) and each adds a rule of its own for
its domain. The rule writes each message as it was sent into a bucket of the environment's
(under `inbound/`) and tells an SNS topic, encrypted with a key of its own, whose notice, with
the object's key and SES's SPF, DKIM and DMARC verdicts, reaches an SQS queue
(`INBOUND_MAIL_QUEUE`) without SNS's envelope. A thread of the worker's waits on the queue (SQS's
long poll, 20 seconds, so a message is taken within seconds) with the app role's credentials and
queues a `receive_mail` job for each notice, deleting the notice once the job is committed; a
message on the queue that is no such notice is dropped with a warning. The job reads the message
from the bucket, takes it in and deletes the bucket's copy; a copy gone, or a bucket the role may
not read, ends the job failed, and a bucket out of reach is asked again after 1, 2, 4 and 8
minutes, as for Resend. The sender is proven when DMARC passed, or, for a domain that publishes
no DMARC policy (SES's GRAY), when DKIM passed and every DKIM signature the message carries is of
the From domain or of a domain it is under or over, so that the signature SES passed is one of
them. A message no job took is removed after 14 days, as the queue's notice of it is. With
`INBOUND_MAIL_QUEUE` set, SES hands the mail over, whatever Resend's settings say. On AWS, the
module's `inbound` makes all of it ("On AWS").

## Connected folders

A project may keep in step with a folder of a member's Google Drive, Dropbox, OneDrive or
SharePoint (`backend/app/connectors/`; offered only for the services the deployment has an OAuth
client for, and none on the sample project). In the library's "Connected folders", a member who may change the project connects a service: the browser goes to it for
their consent to read their files and comes back with a code, which the app redeems for a
refresh token, kept encrypted with `ENCRYPTION_KEYS` ("The encryption keys"); the member then
browses their own folders there and chooses one, once, and where its files go in the project.
A project holds at most 10 connected folders; another folder of the same account is another
connector.

The worker syncs each every 15 minutes (`SYNC_INTERVAL`), and at a member's "Sync now": its
heartbeat queues a `sync_connector` job for each connector whose time has come, at the bulk
priority, so that uploads and answers go first. A sync asks the service whether anything
changed since the last listing (Google's change log, Dropbox's cursor, the OneDrive drive's
delta: one request when nothing did), and otherwise reads the folder whole and compares it with what the last sync found: a
file not seen before, or whose version changed (its checksum; a Google document's last change),
is downloaded and taken in through the checks an upload's files go through (its type, its size,
bytes the project holds already), into the folders of its path below the target; a changed file is its document's new version, through the same checks, the earlier file kept with
the document (one whose document is being indexed waits for the next sync), and the document of
a file no longer below the folder goes to the project's trash. What has no bytes of its own is
exported where the service can (below), and listed as left out where it cannot. A file is read
once per version: one the project keeps, rejects or a member deleted is not read again until it
changes. A sync takes at most 100 files (`ROUND_FILES`) and queues the next at once while more
wait; a folder of more than 5,000 files below it (`MAX_FILES`) syncs nothing. Nothing goes to
the trash unless the folder was read whole: a service that stops answering, a folder deleted or
no longer shared, leaves every document as it is and the connector says why. The documents a
sync keeps are one upload, its member's, told of as their uploads are, which the project's
automations see arrive; their audit rows say which service they came from, and the documents a
sync puts in the trash are rows of their own.

A connector pauses, and its member connects it again, when the service no longer takes the grant
(the member took the access back, or it lapsed), or when the member no longer may change the project;
it goes, its grant with it, when they are removed from the workspace or their account is deleted, and
when a member disconnects it, which gives the grant back to the service. Its documents stay in the
library either way. A project in the trash holds its syncs back, which the page says. A connector whose
folder was never chosen is forgotten after a day. The log names a connector by its id, never a
file or a folder; `emudoc_connector_syncs_total` and `emudoc_connector_files_total` count the
syncs and the files they read ("Metrics"), and a sync that raised is among the failed jobs,
named "the connected folder <id>".

Each service needs an OAuth client of its own, registered with the one redirect URI
`{APP_URL}/api/connectors/callback`; it is read from, receiving nothing of the projects'.

**Google Drive.** Browsing starts at My Drive, the shared drives and what was shared with the
member; a folder is read a request per folder below it, in its shared drive's own corpus where it
is in one. Google's own documents are exported, a Doc as a Word document, a Sheet as an Excel
workbook, Slides and Drawings as PDFs (Google exports at most 10 MB); forms, sites and the like
cannot be. Its client is not sign-in's, since its consent asks to read the member's files: in the
Google Cloud console, enable the Google Drive API in the client's
project, add the scope `https://www.googleapis.com/auth/drive.readonly` to its consent screen,
and make a "Web application" client with the redirect URI `{APP_URL}/api/connectors/callback`,
whose id and secret are `GOOGLE_DRIVE_CLIENT_ID` and `GOOGLE_DRIVE_CLIENT_SECRET`; without an id
nothing is offered. `drive.readonly` is a restricted scope: until Google has verified the app
(restricted scope verification, with a security assessment by an assessor Google names, renewed
every year), only the test users listed on the consent screen may connect, at most a hundred,
and while the app is in testing, Google's grants lapse after seven days, so the connectors pause
weekly. `drive.file` with Google's Picker would need less verifying, but it reads only files
picked one by one, and the Picker is Google's script on the app's page.

**Dropbox.** Browsing starts at the member's Dropbox; a folder is read in one listing with its
subfolders, whose cursor is what the next sync asks from, and named by its id, so that it stays
connected as it moves. A Paper document is exported as Markdown; a file Dropbox will not let be
downloaded (a Google document kept there) cannot be read. In Dropbox's App Console, make a
"Scoped access" app with "Full Dropbox" access, give it the permissions
`files.metadata.read`, `files.content.read` and `account_info.read` (the consent asks for what
the app has), and add the redirect URI; its App key and App secret are `DROPBOX_APP_KEY` and
`DROPBOX_APP_SECRET`. An app in development may be linked by at most 500 accounts, and once 50
are linked Dropbox gives two weeks to apply for production status before it stops linking more:
apply from the app's page in the console before then.

**OneDrive and SharePoint**, through Microsoft Graph. Browsing starts at the member's OneDrive
("My files") and the document libraries of the SharePoint sites they follow (up to 20); a folder
is named by its drive and its item, read a request per folder below it, and asked after through
its drive's delta, since Graph keeps a delta below a drive's root for OneDrive alone. A file's
version is its `cTag`, which changes with its content; a OneNote notebook cannot be read. Microsoft
gives a new refresh token with each access token, and the app keeps the newest; it has no way to
give a single grant back, so disconnecting forgets it and the member removes the app from their
account's "Apps" page if they wish. In Microsoft Entra ID, register an app (its supported
accounts as `ONEDRIVE_TENANT` says: "common" for work, school and personal accounts, a tenant id
for one directory alone) with a Web redirect URI, a client secret, and the delegated Microsoft
Graph permissions `offline_access`, `User.Read`, `Files.Read.All` and `Sites.Read.All`; its
application (client) id and the secret's value are `ONEDRIVE_CLIENT_ID` and
`ONEDRIVE_CLIENT_SECRET`. Most directories let their users consent only to apps of a verified
publisher, and some to none: verify the publisher (Microsoft Partner Network), and an
organisation's administrator may have to grant the permissions for its users first. A secret
lasts as long as it was made for, two years at most: make the next before it runs out ("Rotating
a key").

`CONNECTOR_BASE_URL` points the connectors at the browser test's stand-in instead, which
production refuses.

## Webhooks

An organisation's owners and admins give its programs addresses to hear of its work at
(`backend/app/webhooks.py`; the organisation page's Webhooks section says how a program checks a signature): `document.ready` and
`document.failed` once a document's indexing ended and none is to run after it,
`check_run.completed` once a run of cross-checks is worked out and its report, when asked for,
written, and `report.completed` once a question set's report is answered or failed, its body
what the report is and never what an answer says. The end of the job that brings each about
announces it, and each delivery is a job of
its own, `deliver_webhook`, with the indexers: the body is the versioned API's document or run
in an envelope (`{id, type, created_at, data}`, listed in the contract's `webhooks`), signed
with the webhook's secret as Stripe signs its own (`Emudoc-Signature: t=…,v1=…`), the secret
kept encrypted with `ENCRYPTION_KEYS` ("The encryption keys", whose `encrypt-secrets` covers it).
Anything but a 2xx within 10 seconds is tried again, the wait doubling from a minute, 12 times
over about 34 hours, with the same body; a delivery the worker gives up on is in the
organisation's delivery log, never among the failed jobs, since what the receiver does is the
member's to fix. There is nothing to set.

The worker posts to addresses members chose, on the internet, so its machine must reach the
internet on port 443 (on AWS the instance's security group lets it out). It refuses what a
member must not reach through it: a name that resolves, wholly or in part, into the deployment's
own network (a private, loopback, link-local, shared or reserved address, IPv4 inside IPv6
included, so the instance's metadata too) and plain HTTP, in production; it connects to the
address it checked rather than to the name, follows no redirect and takes no proxy from the
environment (`HTTPS_PROXY` is not read), so a deployment whose egress must go through a proxy
sends no webhooks. Development, whose receivers run beside it, takes both. The log names a
delivery by its id and the event, never the address; `emudoc_webhook_deliveries_total` counts
the tries by how they ended ("Metrics"). What the addresses receive (a document's name, title,
summary and how far it is indexed; a run's counts and report) goes to a company the
organisation chose.

## Retention and deletion

What the app removes on its own, in the worker's housekeeping round (a minute after start, then
every six hours), and so what a team can tell its members is kept, and for how long:

- **Chats** are kept until a member deletes them, unless the workspace's owners and admins
  choose a number of days on its page (`organizations.chat_days`); then they go once their last
  question is older (`prune_chats` in `backend/app/retention.py`), and a chat whose answer is
  still being written stays until it is settled. A chat in the trash goes at whichever comes
  first, this or the trash's 30 days.
- **Runs of cross-checks** (`workflow_runs`, with their findings and reports,
  `backend/app/check_runs.py`) go as a chat would, counted from when the run was made
  (`prune_runs` in `backend/app/retention.py`), never one the worker is still working out or
  writing a report for.
- **Reports of question sets** (`question_reports`, with their answers,
  `backend/app/question_reports.py`) go as a chat would, counted from when the report was made
  (`prune_reports`), never one the worker is still answering. A member deletes one for good; the
  question sets themselves stay until a member deletes them or their project goes.
- **Drafts** (`drafts`, with their versions, `backend/app/drafts.py`) go as a chat would,
  counted from when one was last asked for, written or saved (`prune_drafts`), never one the
  worker is writing. A member deletes one for good; drafts have no trash.
- **The trash**: a project, folder, document or chat a member deleted goes for good, rows and
  files, 30 days after it was deleted (`TRASH_DAYS` in `backend/app/trash.py`, `purge_trash` in
  the housekeeping round), unless a member restores it or deletes it for good there first. Its
  originals count in `emudoc_stored_bytes` meanwhile, its documents as `trashed` in
  `emudoc_documents`, and the usage page leaves them out.
- **Sessions and one-time links** go 30 days after they expired or were revoked
  (`CREDENTIAL_HISTORY` in `backend/app/auth.py`), with the address and browser a session
  recorded and the address a link went to.
- **Idempotency keys** (`idempotency_keys`, `backend/app/idempotency.py`): the key a program sent
  to the versioned API with a request that made something, and the answer it was given, which
  names what it made, go a day after the key was claimed (`KEPT`), or with the API key.
- **Webhooks' delivery logs** (`webhook_deliveries`, `backend/app/webhooks.py`): which event
  went to which webhook, about which document or run, and how its address answered, go 30 days
  after the event (`DELIVERIES_KEPT`), or with the webhook; a try's job holds the event's body
  until the try is done with it.
- **Invitations** go when they are accepted or revoked, and an expired one 30 days after it
  expired, with the address it was sent to (`backend/app/invitations.py`).
- **Audit log rows** go once they are older than the workspace keeps them
  (`organizations.audit_days`, `prune_audit` in `backend/app/audit.py`): a year until its owners
  and admins choose fewer or more days, or none to keep them until the workspace goes. A row
  holds the client's address it was recorded from; an account's purge clears it from the rows
  of that account.
- **A deleted account** goes 14 days after its user asked (`DELETION_GRACE` in
  `backend/app/retention.py`), within six hours of the day, with every organisation it was the
  only member of: projects, documents and their files, chats, jobs and usage. In an
  organisation with other members, the user's membership, sessions and chats go and its usage
  and audit rows stay with the organisation without their name (and the audit rows without
  their addresses); the last owner of such an organisation
  cannot ask until another member is an owner, and one who became the last owner meanwhile is
  succeeded at the purge by the next member in role and seniority (logged as a warning). The
  log names accounts by id only.
- **What the notices were about** ("Mail"): an upload's row, which names who sent
  its files, stays as long as its project, and forgets them when their account goes; a document
  waiting for an automation's run (`automation_arrivals`) goes as the run is made, within a
  quarter of an hour of its arrival while a worker runs, or with the document, and so does one
  waiting for a question set's report (`question_set_arrivals`); a membership keeps the Monday
  of its last weekly digest (`memberships.digested_on`), as long as the membership; a notice
  in a member's notification centre goes after `NOTIFICATION_DAYS` (90), read or not, and those
  about the workspace when they are removed from it.

Backups (and a bucket's object versions) keep a purged account until they expire, so that window,
`BACKUP_DAYS`, is the outer bound of a deletion. A restore must not bring the account back, so each purge leaves a record in the storage, `erasures/users/<id>-<created>.json`,
naming the account by its id and creation time and nothing else, and `after-restore` (or, if it
is forgotten, the first housekeeping round) purges again every account that is back (the
worker's log says "Accounts […] were purged before and are back in the database"). A restore
must leave `erasures/` as it is: restored to an older point, it forgets the purges since
("Backups", below). The records are kept for good. They say nothing about the person, and
backups may be kept for any length of time.

The workspace itself is not deleted from the app, since it is the install: drop the database and
the storage to start over. Until 2026-11-12 an owner could ask for an organisation's deletion, and
a bucket of a deployment from before may still hold `erasures/organizations/`, which nothing reads
now.

Logs carry no email address of the app's making: a refused sign-in names the address by its
SHA-256, as the rate limits count it, and a mail that fails is logged with its subject and the
relay's answer, every address in it replaced by `<address>`. The log mailer of development
writes every message, links included; production's, where no relay is set, a member's message
by its subject alone ("The providers", under "Mail"). The worker must run for any of this to
happen: an API without one purges nothing.

## Backups

A deployment keeps two backups, of the database and of the files. Both hold every document, so
both are kept off the server and encrypted there, and both expire within `BACKUP_DAYS`, the
latest a deleted account is gone from them. The two must restore to the same moment: a database
row whose file is gone is a document that cannot be opened or indexed again, and a file whose
row is gone is an upload nobody can reach.

A team on one server takes both at once with the compose file's `backup` service, every night
("With the compose file"). A deployment that must lose minutes at most keeps point-in-time
backups instead: the database restorable to any moment within the window, and the files through
the bucket's versioning or the disk's snapshots (the rest of this section).

### With the compose file

```bash
docker compose run --rm backup     # in the folder of compose.yml and .env, while the app runs
```

Each run writes a folder of its own, named by the moment (`emudoc-2026-11-13T033000Z`), into
the `backups` volume, or into the directory `EMUDOC_BACKUPS` names in `.env`
(`backend/app/backup.py`):

- `database.dump`, the database as `pg_dump` saw it, in its custom format;
- `files/`, when `STORAGE_URL` is a directory: the originals, the views converted from them
  and the records of the accounts purged, without the rendered pages and thumbnails, which are
  drawn again. A file unchanged since the previous backup is a hard link to that backup's copy,
  so each night takes the space of what changed;
- `keys.txt` instead, for a bucket: its files are not copied, since it keeps them by its own
  versions ("The files", below), and the restore checks the bucket against the list;
- `backup.json`: the version of Emudoc that took it, the database's schema revision and the
  moment.

The database is dumped first and the files copied after, so the files hold at least what the
database names. Once the backup is whole, those in the directory older than `BACKUP_DAYS` (14 in
the example) are removed; a run that fails removes none, and leaves nothing half written. The
service reaches the database and the files as the worker does. Its `pg_dump` is PostgreSQL 17's,
which dumps a server of 17 or earlier; behind a pooler in transaction mode it uses
`DATABASE_LISTEN_URL`. Every night, from a systemd timer:

```ini
# /etc/systemd/system/emudoc-backup.service
[Unit]
Description=Emudoc's backup
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
WorkingDirectory=/srv/emudoc
ExecStart=/usr/bin/docker compose run --rm backup
```

```ini
# /etc/systemd/system/emudoc-backup.timer
[Unit]
Description=Emudoc's backup, every night

[Timer]
OnCalendar=*-*-* 03:30:00 UTC
RandomizedDelaySec=15min
Persistent=true

[Install]
WantedBy=timers.target
```

`systemctl enable --now emudoc-backup.timer`, with the folder of `compose.yml` and `.env` as
`WorkingDirectory`; `systemctl status emudoc-backup` says how the last run went and `journalctl
-u emudoc-backup` shows its lines. Or a line of root's crontab: `30 3 * * * cd /srv/emudoc &&
docker compose run --rm backup >>/var/log/emudoc-backup.log 2>&1`.

A backup on the server's own disk is lost with it. Copy the folders to another machine or a
bucket (restic, which encrypts its repository, or rsync to a disk that is), or set
`EMUDOC_BACKUPS` to a directory on another disk that the image's user may write in (`install -d
-o 10001 -g 10001 -m 700 /mnt/backups/emudoc`); the volume's folders are under `docker volume
inspect -f '{{.Mountpoint}}' emudoc-deploy_backups`. A copy kept longer than `BACKUP_DAYS` keeps
a deleted account longer than the members are told. Keep `.env` apart from the backups: a
restore needs its `ENCRYPTION_KEYS`, and with them a stolen backup's secrets can be read ("The
encryption keys"). A backup every night loses at most a day: that is the recovery point, where
"Recovery targets" below is the point-in-time way's.

To restore, with the app stopped and the database emptied:

```bash
docker compose stop api worker
docker compose exec postgres sh -c 'dropdb -U emudoc --force emudoc && createdb -U emudoc emudoc'
docker compose run --rm backup python -m app.maintenance restore /backups/emudoc-2026-11-13T033000Z
docker compose up -d --wait
```

On a new machine, `compose.yml` and the `.env` that was kept apart, with `EMUDOC_BACKUPS` naming
the directory the backup was copied into (readable by uid 10001); its database starts empty, so
the first two lines are left out. The restore refuses a database that holds tables, and a backup
from a later version than the image's. It puts the files back into the storage, restores the
database in one transaction, so that a restore that fails leaves it empty to run again, and
upgrades it when the backup is from an earlier version. Then it does what `after-restore` does
after any restore to an earlier point ("Restoring", step 5), from the backup's moment: every
session and unused link ends, the audit logs record the restore, the accounts deleted since are
purged again, and it names any document whose original is missing and any secret whose key
`ENCRYPTION_KEYS` no longer holds. Tell the members that what they did since the backup is
undone (step 6). Restore one on another machine once before relying on them; CI's image check
backs up and restores the compose file at every change ("The image").

The rest of this section is the point-in-time way.

### The database

A managed database keeps its own: on RDS, automated backups with a retention period of at most
`BACKUP_DAYS`, encrypted when the instance is (chosen when it is created), and a restore makes a
new instance at the moment asked for, whose address goes into `DATABASE_URL`. A database of the deployment's own is
backed up with pgBackRest, which archives every WAL segment as Postgres writes it and takes the
base backups the segments replay onto. In `postgresql.conf`:

```ini
wal_level = replica
archive_mode = on
archive_command = 'pgbackrest --stanza=emudoc archive-push %p'
# A quiet database still sends its last minute of changes to the repository.
archive_timeout = 60
```

and in `/etc/pgbackrest/pgbackrest.conf`:

```ini
[global]
repo1-path=/var/lib/pgbackrest
# Encrypted before it leaves the server; the passphrase comes from the secret manager, and the
# backups are worthless without it, so keep it apart from them.
repo1-cipher-type=aes-256-cbc
repo1-cipher-pass=<a long random passphrase>
repo1-retention-full-type=time
repo1-retention-full=28
start-fast=y
archive-async=y
spool-path=/var/spool/pgbackrest
compress-type=zst

[emudoc]
pg1-path=/var/lib/postgresql/17/main
# Only if the superuser is not called postgres.
pg1-user=emudoc
```

`pgbackrest --stanza=emudoc stanza-create` once, then a full backup every week and a
differential one every day from cron or a systemd timer (`--type=full backup`, `--type=diff
backup`), as the user Postgres runs as. Time-based retention keeps the newest full backup older
than its days, since the moments after it can only be restored from it, so the oldest data in
the repository is up to `repo1-retention-full` plus the days between two full backups old:
28 and a week is 35, `BACKUP_DAYS=35`. The repository can be a bucket rather than a directory
(`repo1-type=s3` and the `repo1-s3-*` options in pgBackRest's guide); either way it is not the
bucket of the documents, and not on the database's disk. `pgbackrest --stanza=emudoc info` says
`cipher: aes-256-cbc` and the range of WAL archived.

### The files

A bucket keeps its own when versioning is on: a delete leaves a marker over the file's last
version rather than removing it, and a lifecycle rule removes earlier versions for good after so
many days:

```json
{"Rules": [{
  "ID": "earlier-versions", "Status": "Enabled", "Filter": {"Prefix": ""},
  "NoncurrentVersionExpiration": {"NoncurrentDays": 33},
  "Expiration": {"ExpiredObjectDeleteMarker": true}
}]}
```

(`aws s3api put-bucket-versioning --versioning-configuration Status=Enabled` and
`put-bucket-lifecycle-configuration --lifecycle-configuration file://rule.json`). AWS adds the
days to the moment a file was deleted, rounds up to the next midnight UTC, and may remove the
version some time after that, so leave a margin: 33 days for `BACKUP_DAYS=35`. The rule must cover every file of
the app's, by no filter or a prefix (the prefix of `STORAGE_URL`, or `orgs/`), with no tag, no
size and no `NewerNoncurrentVersions`, which would keep the last version of a deleted file for
good. Object Lock keeps versions too, for its own retention, which must also be within
`BACKUP_DAYS`. The objects' versions are encrypted like the objects ("Encryption", above).

The app asks at start (its credentials need `s3:GetBucketVersioning` and
`s3:GetLifecycleConfiguration`) and logs the answer: "The bucket keeps a deleted or replaced file
for 33 days (versioning, within BACKUP_DAYS (35))". Production refuses to start with a bucket
that keeps earlier versions longer than `BACKUP_DAYS`, or for good, since a deleted account would
then outlive what the setting says; a bucket without versioning while `BACKUP_DAYS` says there are backups is
a warning, since its files may be copied another way (replication to a second bucket, with the
same rule). A directory (`STORAGE_URL=file:///…`) is snapshotted with its disk (ZFS, LVM, btrfs)
or backed up with restic, at least as often as a restore would need, and kept no longer than
`BACKUP_DAYS`.

### Restoring

1. Stop the API and every worker, so that nothing writes while the pieces are put back.
2. Restore the database to the moment, in UTC: with pgBackRest, the server stopped,
   `pgbackrest --stanza=emudoc --delta --type=time "--target=2026-10-15 09:30:00+00"
   --target-action=promote restore`, then start Postgres. Its log says "recovery stopping
   before commit of transaction …" and "archive recovery complete". (pgBackRest takes the time
   in this form only; `Z` is refused.) A managed database is restored to a new instance and
   `DATABASE_URL` pointed at it, with `DATABASE_LISTEN_URL` and a pooler's `[databases]`
   entry if there are any, and the role's timeouts come back with it.
3. `uv run alembic upgrade head`, if the code is newer than the database at that moment.
4. A directory is restored from its snapshot, of the same moment or later, never earlier.
   A bucket with versions is left as it is: the next step brings back from it what was deleted
   since. Either way `erasures/` stays as it is.
5. `uv run python -m app.maintenance after-restore "2026-10-15 09:30:00+00"`, the same moment
   (`backend/app/restore.py`). It brings back from the bucket's versions every file deleted
   since then, whose rows are back; moves the ids of organisations, projects and documents past
   every id the storage holds, since a document's files sit under its ids and a new document
   given a lost one's would be served its pages; ends every session, unused link, open
   invitation and share link, since a session signed out or an invitation or a share link
   revoked since would work again;
   records the restore in every organisation's audit
   log; purges again the accounts deleted since; and names the documents whose original is
   still missing, which means the storage is older than the database, and the authenticators
   whose key `ENCRYPTION_KEYS` no longer holds ("The encryption keys"). Running it again is safe,
   and `after-restore --dry-run` with the same moment says first what it would do, changing
   nothing.
6. Start the app, and tell the members that what they did after that moment is undone: their
   uploads, deletions, shares, a changed password. Everyone signs in again, and the owners and
   admins send again the invitations still wanted and check who sees each private project,
   since someone left out of one after that moment sees it again.

The files uploaded after the moment stay in the storage as the files of documents the database
does not know, and the worker's housekeeping removes them an hour after they were written
(`sweep-orphans` lists them first, if they are wanted). Postgres hands out sequence values in
batches of 32 that it logs ahead, so a restore skips up to 32 ids of each table by itself; past
that only `after-restore` keeps the ids apart, and an upload whose place in the storage holds a
lost document's files is refused ("the storage is not available" to the member, the reason and
the command in the API's log) rather than stored there.

### Recovery targets

What a restore may cost, written down so that the drill can measure it:

- **Recovery point: five minutes of the database, none of the files.** pgBackRest sends each
  WAL segment to the repository as Postgres fills it, and `archive_timeout = 60` closes a quiet
  one after a minute, so a server lost with its disk loses the last minute of changes and what
  the asynchronous push had not sent yet, seconds unless the repository was out of reach; five
  minutes is that with a margin. A versioned bucket keeps every file deleted or replaced within
  `BACKUP_DAYS`, so a restore to any moment of that window gets its files back. A directory
  loses what was written since its last snapshot: snapshot it every five minutes too, or write
  its own point here. A restore to an earlier moment on purpose, after a mistake or a bug,
  gives up everything after that moment by choice.
- **Recovery time: four hours** from the decision to restore to members working again. Its
  parts, most of which the drill times: a machine for the database, if its own is lost; the
  restore of the newest full backup and the differential after it, at the speed of the disk and
  the network; the replay of the WAL since that backup, at most a day of it with a daily
  differential; the migrations, if the code is newer than the backup; `after-restore`, one
  listing of every file and of the versions under `orgs/`; and the app's start. When the
  drill's parts come to more than half the target, take differentials more often (a shorter
  replay), restore with more processes (`--process-max`), or give the restore a faster disk.

What a restore gives up, the app cannot redo; the members are told (step 6 above): the uploads,
deletions, moves and shares since the moment, role changes, a changed password or name, recovery
codes used (they work again) and two-factor sign-in turned on or off, and the audit rows and the
usage of that time, which then goes unbilled. Everyone signs in again.

### The drill

A backup that was never restored is not known to restore. The deployment drills a restore before
it opens to anyone, then every month on its own, and keeps what each drill measured.

**Before launch, by hand, on staging**, with a bucket of its own: upload a few documents, write
down the moment (`date -u '+%Y-%m-%d %H:%M:%S+00'`), upload one more and delete one, and restore
to the moment as "Restoring" says, timing each step; then check as step 13 of "Checking it"
says. Next, a minute after a last upload, stop the database's server hard (`kill -9` on the
postmaster, or remove its data directory), restore to the end of the archive, and look for that
upload: whether it is there measures the recovery point. The steps' times, with the time it
takes to get a machine, are the recovery time. Do the same every year, and after any change to
how the backups are made.

**Every month, on its own, from production's backups**: `deploy/restore-drill.sh` restores the
newest backup into a scratch directory, starts a throwaway Postgres on it that archives nothing
and listens on no port, migrates it, and checks it against the live storage with `after-restore
--dry-run`, which changes neither: every document of the copy must have its original in the
storage, now or among the earlier versions the bucket keeps, and every secret it holds a key in
`ENCRYPTION_KEYS` ("The encryption keys"). It prints what a real `after-restore` would do and
how long each part took, and exits with 0 only when the restore is whole:

```text
The database reaches 2026-10-15 10:32:30 UTC (the last transaction its recovery replayed), 4 min 48 s before the restore point.
It holds 1 organisation, 1 account, 1 project, 2 documents and 0 chats.
…
The restore is whole: the storage holds every document's original as it was at 2026-10-15 10:37:19 UTC.

The drill took 11 s: the restore 2 s, the replay 3 s, the schema 2 s, the check 4 s.
The drill passed.
```

It runs on a machine apart from the database's, which a drill there would compete with for
memory and disk, and which a restore must not depend on. That machine needs:

- pgBackRest with production's `pgbackrest.conf`: the repository and its passphrase. The drill
  only reads the repository, and keeps its copy from ever writing to it (`--archive-mode=off`):
  a copy that archived would start a timeline there that the next restore to the end of the
  archive follows.
- The server binaries of the database's major version with pgvector (`postgresql-17` and
  `postgresql-17-pgvector` on Debian; `DRILL_PG_BIN` names their directory) and a free disk as
  large as the database. The copy is not encrypted while the drill runs, so that disk is kept
  like the database's (`DRILL_DIR`, `/var/tmp/emudoc-drill` by default, removed after).
- The app's checkout with uv (`DRILL_BACKEND`), or its image through `DRILL_APP` (below), and
  production's settings without the model keys, which the check does not use, with
  `ENCRYPTION_KEYS`, by which it checks the copy's secrets, and with the bucket's keys replaced
  by read-only ones: `s3:ListBucket`, `s3:ListBucketVersions`,
  `s3:GetBucketVersioning` and `s3:GetLifecycleConfiguration` on the bucket are all it asks for.
  A directory storage is mounted read-only: the dry run only lists it. The script sets
  `DATABASE_URL` itself, to the copy's socket.

With the image, `DRILL_APP` in the unit's environment is the `docker run` that each of the
app's commands is put after, on one line (the script reads one):

```ini
# /etc/emudoc/drill.env
DRILL_APP=docker run --rm --read-only --tmpfs /tmp --user 106:10001 -v /var/tmp/emudoc-drill/socket:/var/tmp/emudoc-drill/socket --env-file /etc/emudoc/drill-app.env -e DATABASE_URL emudoc:3f2c1ab
```

The copy's socket is mounted at the path it has on the machine, which the script names in
`DATABASE_URL`, and `-e DATABASE_URL` passes the script's own, which wins over one in the env
file. The app's settings go in a file of their own, since `docker run --env-file` takes each
value as it stands, quotes included, where the unit's `EnvironmentFile` strips them. The
container runs as Postgres's user (`id -u postgres`; 106 above), the only one let into the
socket's directory, and with the image's group, 10001, which may list a directory storage
mounted beside it (`-v emudoc-deploy_files:/var/lib/emudoc:ro`). Postgres's user then starts
containers, which with Docker is root's power over the machine: one more reason for the drill's
machine to be apart from production's.

It runs as the user Postgres runs as, from a timer:

```ini
# /etc/systemd/system/emudoc-restore-drill.service
[Unit]
Description=Emudoc restore drill
# A unit of the deployment's that mails the operator, as for the backups' own timers.
OnFailure=mail-operator@%n.service

[Service]
Type=oneshot
User=postgres
EnvironmentFile=/etc/emudoc/drill.env
ExecStart=/opt/emudoc/deploy/restore-drill.sh
# A large database takes hours to restore and replay; the script gives the replay 6 h
# (DRILL_RECOVERY_LIMIT).
TimeoutStartSec=12h
```

```ini
# /etc/systemd/system/emudoc-restore-drill.timer
[Unit]
Description=Emudoc restore drill, monthly

[Timer]
OnCalendar=*-*-01 05:00:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
```

`systemctl enable --now emudoc-restore-drill.timer`; `systemctl start emudoc-restore-drill`
runs one at once, and `journalctl -u emudoc-restore-drill` keeps each report. Given a moment
(`restore-drill.sh "2026-10-14 09:30:00+00"`), it restores to that moment instead, which checks
that the bucket's versions reach back as far as the database's backups do.

Reading a report:

- The restore point of a drill to the end of the archive is the moment its recovery ended, so
  "reaches … before the restore point" is how far behind production the archive was: the
  recovery point it measured. Longer than the target while members were working means the WAL
  is not reaching the repository, and `pgbackrest --stanza=emudoc check` on the database's
  machine says why; after a quiet hour it is that hour, and nothing was lost.
- "It holds …" is to be close to production's own counts at the time.
- The parts' times go into the drill's record with the date, against the recovery time.
- A failed drill is a backup that would not have saved the deployment: the restore failed
  (pgBackRest's error, or the last lines of the copy's server log: "recovery ended before
  configured recovery target was reached" for a moment newer than the archive), a migration
  did not run on production's data (the next release would have failed the same way), or the
  storage lacks a document's original (the bucket's versions do not reach back that far, or the
  file is gone).

**With a managed database** the provider keeps the backups and restores them only to a new
instance, so the monthly drill is `deploy/managed-restore-drill.sh` instead: it has the provider
make a copy at the latest moment it can (or at the moment given), brings the copy to the code's
schema, runs the same dry run against the live storage, and deletes the copy whatever happened,
failing with its name if the delete did not go through. Every provider restores its own way,
so its part is two commands the deployment names, each given the copy's name:

- `DRILL_RESTORE NAME [TIME]` restores to a new instance called `NAME`, waits until it takes
  connections, and prints `DATABASE_URL=` the copy's URL and `DRILL_MOMENT=` the moment it
  restored to, each on a line of its own;
- `DRILL_DISCARD NAME` deletes the instance and its backups, and succeeds when there is none.

`deploy/rds-drill.sh` is RDS's, configured by the variables its header lists (on AWS the
environment's instance runs it, set up by `deploy/aws`: "The drill on AWS", below):

```ini
# /etc/emudoc/drill.env, for a drill machine in production's VPC
DRILL_RESTORE=/opt/emudoc/deploy/rds-drill.sh restore
DRILL_DISCARD=/opt/emudoc/deploy/rds-drill.sh discard
AWS_REGION=eu-central-1
RDS_SOURCE=emudoc-production
RDS_SUBNET_GROUP=emudoc-private
RDS_SECURITY_GROUPS=sg-0b5d1e7c9a2f4e601
RDS_INSTANCE_CLASS=db.r7g.large
RDS_URL=postgresql+psycopg://emudoc:…@HOST:5432/emudoc?sslmode=verify-full&sslrootcert=/etc/emudoc/rds-global-bundle.pem
```

The copy has no public address, no standby and no deletion protection, is encrypted with
production's key when production is, and is tagged `purpose=restore-drill`, so that one left
behind is found. The latest moment is the source's `LatestRestorableTime`, read just before
the restore is asked for, which the copy reaches or passes by seconds. The drill's machine
needs the AWS CLI with credentials allowed only to describe, restore, tag and delete instances
(the four actions in the script's header), a way into the copy (the subnet group and a security
group that let it in), and the app's settings with the storage's read-only keys, as above; the
unit is the one above, with `ExecStart=/opt/emudoc/deploy/managed-restore-drill.sh` and a user
of its own in place of `postgres`. A copy costs an instance for the length of the drill, which
for a large database is mostly the provider's restore. Its report reads like the one above,
without a replay, which the provider's restore includes:

```text
Restoring the database to the latest moment the provider can as emudoc-drill-20261101-050000.
The copy is at 2026-11-01 04:58:41+00.
…
The drill took …: the restore …, the schema …, the check …; deleting the instance follows.
The drill passed.
Deleting the instance emudoc-drill-20261101-050000.
```

The script was checked with a stand-in for the provider (the hook copied a database on the
same server and printed its URL) and `rds-drill.sh` against a fake AWS CLI, not against RDS:
the first real drill is the check of the provider's part.

The monthly drill leaves out a bucket lost as a whole (its deletion, or its service's failure),
which only replication to a second bucket elsewhere survives (point `STORAGE_URL` at the replica
to drill it); a directory's snapshots, since it reads the live directory; and the app's start
and what the members see, which the yearly drill on staging covers.

The first drill ran on 2026-10-15, with pgBackRest 2.59 on Postgres 17 and pgvector, the
encrypted repository above, and MinIO with versioning and the rule above: two uploads, the
moment, then one upload and one deletion. The script, in a container that could only read the
repository and with bucket keys allowed only the four permissions above (a write was refused),
restored to the moment: the copy reached one second before it, held the two documents from
before it, and would have brought back the deleted one's file. A real restore to the moment
followed, then `after-restore`, which brought the file back from MinIO's versions: the document
downloaded with the bytes it was uploaded with, and the one uploaded after the moment was gone.
With every version of that file removed from the bucket, the drill failed and named the
document; a moment older than the oldest backup ends it with pgBackRest's "unable to find backup
set". Its times are a few seconds each for a database of 30 MB and are no measure of
production's; the drill on staging gives the first. versitygw, the compose file's bucket, stamps
a delete marker with the time of the file it deletes rather than the time of the delete, so no
listing of it can say what was deleted after a moment; MinIO and AWS stamp the delete.

## Upgrades

An upgrade is a new image: a version in `EMUDOC_TAG` (`0.2.0`; `0.2` takes that line's fixes
alone, and `latest` every release), whose changelog entry says what it changes. Take a backup to
go back to, then pull, run the release step alone, which stops there if a migration fails, and
start the rest ("The image"):

```bash
docker compose run --rm backup
sed -i 's/^EMUDOC_TAG=.*/EMUDOC_TAG=0.2.0/' .env
docker compose pull && \
  docker compose run --rm migrate && \
  docker compose up -d --wait embeddings && \
  docker compose up -d --wait
```

Migrations only go forward, and the app refuses to start on a database whose schema is not its
own, so an earlier image does not start on an upgraded database. Going back is the backup taken
before: `EMUDOC_TAG` set back to the version before, then the restore of "With the compose file"
with that backup's folder. What the members did between the backup and going back is lost, so go
back soon or not at all, and tell them.

Nothing waits on a timer of the host's but the backup: the worker's housekeeping empties the
trash after `TRASH_DAYS`, applies the retention of chats, runs, drafts and audit rows, purges the
accounts due, and sweeps the rendered pages and the files nothing names, every six hours
("Retention and deletion").

## Logs

The API, the worker and the release step's migrations write their log to standard error,
where Docker and systemd collect it. In production each line is one JSON object
(`LOG_FORMAT=json`, the default there; `text` gives the lines development writes):

```json
{"time": "2026-10-18T09:30:00.123Z", "level": "WARNING", "logger": "app.search",
 "message": "A search in project 7 matched keywords only: …",
 "request_id": "0f8fad5b-d9cb-469f-a165-70867728950e",
 "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736", "org_id": 2, "user_id": 4, "project_id": 7}
```

shown here on four lines. `time` is UTC, and the ids say what the line was logged about, as
far as that was known when it was written; an id not known is left out:

- **A request's** `request_id` and `trace_id`, and once the session was read its `user_id` and
  `org_id` (and `api_key_id` for a request made with an API key, which acts as that user), and
  once the project was found in that organisation its `project_id` (and a
  document's `document_id`, a question's or a followed answer's `message_id`). The id is the
  proxy's `X-Request-ID` when it sends a well-formed one (letters, digits and `-_.:`, 8 to 128
  of them), which both examples above do, so the proxy's access log and the app's lines name
  the same request (Caddy's `uuid` field, nginx's last); without one the app makes its own.
  Every response carries it back in `X-Request-ID`, for a member to quote. The trace is the
  app's own, and the jobs the request queues carry it ("Traces", below).
- **A job's** `job_id` and `job_kind`, its `org_id` and `project_id`, the `document_id` it
  indexes or the `message_id` of the answer it writes, with the asking member's `user_id`, and
  the `trace_id` of the request that queued it.
- **Neither**, for the start, the housekeeping and the alerts, which are the process's own.

uvicorn's lines go the same way, its access log included, whose lines add `method`, `path`
(the query's values hidden, below), `status`, `client` and `seconds`, from the request's
arrival to its response's start (uvicorn writes the line as the response begins, so an
answer's event stream counts until its first byte); a uvicorn `--log-config` changes
nothing, the app sets uvicorn's loggers up itself. The probes that succeed, `/api/livez` and
`/api/health` answering below 400, are left out of the access log, since an orchestrator asks
them every few seconds; a 503 from `/api/health` stays. There is no level to set: the app logs
from INFO, and below it the model and storage libraries would log what they send, documents
and questions included. The maintenance commands write text whatever `LOG_FORMAT` says, since
a person runs them and reads them.

The other processes beside the app keep their own: Caddy writes JSON, llama.cpp's server plain
lines, and only its warnings and errors (`--log-verbosity 2`; `--log-jsonl` would make them
JSON, in `deploy/compose.yml`), ClamAV plain lines.

What the app writes to its log never holds what a document says, a question, an answer or a
key (`backend/app/logs.py`), whatever collects it and however long it is kept:

- The app's own lines name documents, chats, jobs and accounts by id, and say what happened
  in its own words. Email addresses are hidden too (above).
- A traceback keeps its frames and the kinds of its exceptions, and drops their messages unless
  the app wrote them or they are about a connection, since that is where data slips in: a
  validation error quotes the value it refused, Postgres repeats the row it refused, a
  malformed model reply is carried in its error. A database error keeps its SQLSTATE and the
  names of the constraint, table and column; the engine never puts a statement's values in an
  error (`hide_parameters`). A failed job's `last_error`, which the operator's digest and
  `failed-jobs` show, is the kind and the line that raised it. To read a message, reproduce the
  failure where its data may be seen.
- A provider's error, a model's or the embeddings endpoint's, is told by its status and the
  codes in its body ("The API key for … was rejected. The provider's error code:
  INVALID_ARGUMENT, API_KEY_INVALID."), never by its words, which may echo what was sent
  (OpenRouter's moderation quotes the flagged text), part of the key, or the platform's account
  with the provider. Members see the same sentence.
- uvicorn's access log names each request's path and its query's parameters without their
  values: `GET /api/projects/3/search?q=…&limit=… 200`. A search's words travel in `q`, the
  provider's one-time sign-in code in `code`.
- The links in emails carry their token after `#`, which no browser sends, so a reset link in
  a log signs nobody in; and pages send no `Referer` (`Referrer-Policy: no-referrer`), since the
  library's address holds its search words and would otherwise ride along with every request.
- The line httpx writes for each request the app sends (to a model, the embeddings endpoint, an
  OAuth provider) names the address without the user and password it may carry and without
  its query's values, and the embeddings endpoint's errors name it the same way.
- A parse that fails names the document by its id and extension ("document 12 (.pdf)"), and the
  parse child's command line carries no file name, since `ps` shows it to every user of the
  machine.
- A request the API refuses as malformed (422) is answered with where and why, never the value,
  so a password that is too short or a reset link's token does not come back in the response
  for a proxy or an APM tool to record. A storage failure answers members with a sentence to try
  again and logs what failed, with the directory or bucket.

The proxy keeps a log of its own, and both nginx's default format and Caddy's write whole
addresses, queries included. The examples above give each one without the query: nginx logs
`GET /api/projects/3/search`, Caddy `/api/projects/3/search?…`. A load balancer or CDN in
front needs the same; most log the query unless told not to. On AWS, the documents bucket and
the network keep records of their own, with members' addresses ("The account's records"). The parse child prints the same
traceback as the app when a parser crashes, which the worker logs.

### Traces

A slow answer, or a document that took long to be ready, is read from the log: there is no
tracing service to run (`backend/app/tracing.py`). Every request starts a trace, `trace_id` (32
hex digits), which each of its lines names and each job it queues carries in its payload; the
worker logs the job's lines under it, and the retry a run queues as well. So one id finds an
upload's request and each of its documents' indexing, or a question's request and its answer's
runs (`message_id` finds those two as well). A job queued outside a request, by a maintenance
command or the housekeeping, starts a trace when it runs. The trace never comes from a header:
a client cannot put its request in another's trace.

Each run of a job ends with one line at INFO that says where its time went, here on several:

```json
{"time": "…", "level": "INFO", "logger": "app.worker",
 "message": "Job 812 (answer_question) ran 48.2 s (done), 0.3 s after it was due: 3 agent turns 40.1 s, 2 tool calls 6.2 s, 2 embeddings requests 5.8 s, 37 writes 0.4 s; the first text 42.0 s in.",
 "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736", "job_id": 812, "job_kind": "answer_question",
 "org_id": 2, "user_id": 4, "project_id": 7, "message_id": 4711,
 "timing": {"outcome": "done", "seconds": 48.214, "waited": 0.312, "first_text": 42.005,
  "totals": {"agent": {"count": 3, "seconds": 40.104, "slowest": 30.288}, "tool": …, "embed": …,
             "write": {"count": 37, "seconds": 0.402, "slowest": 0.051}},
  "spans": [
   {"kind": "agent", "at": 0.021, "seconds": 4.917, "model": "anthropic:claude-sonnet-5-5",
    "outcome": "ok", "first_event": 1.302, "input_tokens": 5120, "output_tokens": 61},
   {"kind": "tool", "at": 4.961, "seconds": 5.904, "tool": "search", "outcome": "ok"},
   {"kind": "embed", "at": 4.963, "seconds": 5.801, "model": "embeddinggemma", "outcome": "ok"},
   …]}}
```

- `waited` is from the moment the job was due to its claim (the backlog,
  `emudoc_job_wait_seconds`), `seconds` the run, and `first_text` how far into the run an
  answer's first text was written: what the member waited for it is `waited` and that.
- `spans` are the run's parts, each from `at` seconds into the run for `seconds`: the stages
  (`stage`, with its `outcome` as `emudoc_stage_runs` names them); the requests to the models
  and the embeddings endpoint, by the work (`agent`, a turn of an answer from its request to
  its stream's end, with `first_event`; `index`, an indexing request, the provider's own
  retries included; `embed`), each with the configured `model`, its `outcome` as
  `emudoc_model_requests` names them, and the tokens the provider counted; and the agent's
  tool calls (`tool`, `ok` or `error`); and a document's parse (`parse`, its child process,
  with `waited`, the seconds it waited for one of the worker's `PARSE_CONCURRENCY` parses, and
  `ok` or `failed`). A span inside another lies within it in time: the embeddings request above
  is the search's, which waited on it for all but a tenth of a second.
- `totals` counts every span of each kind, with the slowest. The writes of an answer's events
  (`write`, a commit each, so a slow database shows there) are counted and not listed, and a run
  of more than 50 spans, a scan's pages, lists its 50 slowest and says how many it left out
  (`unlisted`). A scan's pages are read `OCR_CONCURRENCY` at once, so their `render` and
  `index` spans overlap, and their totals come to more than the parse stage that holds them.
- The line's `outcome` is the job's, as `emudoc_job_runs` names them. A run that was taken over
  (`lost`) logs its own line, and the next run of the job another with the same trace.

A span holds the app's own words: a model's configured name, a stage, a tool among the agent's
own (one the model made up is `unknown`), how it ended, tokens and seconds; never what was
searched for, read or answered. The slowest answers of the last hour, then one's whole way,
with `jq` (on the AWS instance `sudo dnf install jq` if it is missing; `sudo emudoc-compose`
there is `docker compose -f deploy/compose.yml` on one machine), the lines that are not JSON
skipped:

```bash
sudo emudoc-compose logs --no-log-prefix --since 1h worker | jq -R 'fromjson? // empty' \
  | jq -s 'map(select(.job_kind == "answer_question" and .timing))
           | sort_by(-.timing.seconds) | .[:10][] | {message_id, trace_id, message}'
sudo emudoc-compose logs --no-log-prefix --since 1d api worker | jq -R 'fromjson? // empty' \
  | jq -c 'select(.trace_id == "4bf92f3577b34da6a3ce929d0e0e4736")'
```

There is no OpenTelemetry exporter, on purpose. The log is kept for a period the team chooses,
while spans sent elsewhere would be one more store of the members' ids to hold to that period,
or one more company to send them to; and a global tracer provider would turn
on FastAPI's and Anthropic's SDK's own spans, which record each request's path and query
("Metrics"). A collector that makes spans of log lines can take these: `trace_id` has the shape
of a W3C trace's, and `at` and `seconds` place each span. Whatever keeps them is held to the
log's period ("How long they are kept"), and a company that runs it receives the members' ids.

### How long they are kept

The server logs are what the API, the worker and the release step write (uvicorn's access log
among it, with each request's client address), the proxy's access log, and the database
server's own log. They hold the members' addresses, so a team keeps them for a period it chooses
and tells its members. The app keeps nothing of its log itself, so the period is the
deployment's to hold to: whatever collects the lines deletes those older than
it. Docker's `json-file` and `local` drivers rotate by size alone, so a quiet machine keeps
months of them; journald deletes by age, and Docker writes to it with `{"log-driver":
"journald"}` in `/etc/docker/daemon.json` (or `logging: {driver: journald}` on each service of
the compose file), after which `docker compose logs` reads it as before:

```ini
# /etc/systemd/journald.conf.d/emudoc.conf, for 30 days; then
# systemctl restart systemd-journald
[Journal]
Storage=persistent
MaxFileSec=1day
MaxRetentionSec=28day
```

journald deletes a file only once it has moved on to the next (`MaxFileSec`, a day here), and
only at such a move, when the file's first line is older than `MaxRetentionSec`. The moves come
a day apart, so the oldest line of a file goes a day at most after it passed `MaxRetentionSec`,
which two days short keeps a day within the period, and the newest go with it: each line is
kept from three days to one day short of the period (27 to 29 days for 30). A collector that
sends the lines elsewhere (Loki, CloudWatch Logs, a hosted service) is held to the same period
by its own retention; a company that runs it receives the members' addresses.

The proxy's log goes the same way when the proxy writes it to standard error in a container,
as `deploy/aws/compose.yml`'s Caddy does; nginx's file under logrotate keeps the period with
`daily` and `rotate` two short of it. Postgres's own log holds an error's DETAIL by default,
which repeats the key or the row it refused ("Key (email)=(…) already exists" on `users`),
so that the server logs would hold email addresses: `log_error_verbosity = terse`
keeps the error's first line and its statement, whose values the app binds as `$1`. A
Postgres in a container logs to standard error, into the journal; one that writes its own
files keeps less than a week with `log_filename = 'postgresql-%a.log'`, `log_rotation_age =
1d` and `log_truncate_on_rotation = on` (for a period of 7 days or more); a managed one has a
retention of its own, such as RDS's `rds.log_retention_period`.

On AWS the module does all of it. Every container logs to the instance's journal
(`deploy/aws/compose.yml`, each service, so that it comes with each release), which keeps
`server_log_days` (30, in `shared/`, written as `SERVER_LOG_DAYS` for the host) by the settings
above: each
release writes them (`host/settings`) and applies them when they changed (`host/release`), so
the host's own units' lines (the watch, the drill, the release) are held to it too. `sudo
emudoc-compose logs` and `journalctl CONTAINER_NAME=emudoc-deploy-api-1` (or `-t` with a
container's name) read them. RDS deletes Postgres's log a day short of `server_log_days`, at
most 7 days, the longest it keeps, and logs terse errors (`database.tf`). Prometheus keeps its
metrics 15 days and Alertmanager its silences, neither with a member's data.

## Error tracking

The log has every error. With `SENTRY_DSN` set, the API and the worker also report each error
nobody planned for to a tracker, which groups them, counts them and tells someone: Sentry, or a
server that takes its protocol, GlitchTip or a Sentry of the operator's own. One project can
serve every environment: `SENTRY_ENVIRONMENT` files the reports under `staging` or
`production` (the AWS module sets it to the environment's name; unset, it is `ENVIRONMENT`,
which is `production` on staging too), and each report names its release, the commit's full
SHA. A DSN that is not one stops the process at start with what to do.

An error is an exception no route or stage handled, which the member gets as a 500; a line
logged at ERROR, such as a job the worker gave up on or a stage that crashed; and an exception
that ended a thread or the worker. A refusal the app answers on purpose is none: a 4xx, and the
503s of a pool out of connections, a storage that is down or a statement the database stopped.

A report holds what the log would show of the error and nothing more (`backend/app/tracking.py`,
which rebuilds every event from what it may keep): the exception's kind and frames, with the
lines of code around each, its message only when the app wrote it (as in the log, above), the
line it was logged with, the route's pattern and the request's address with its query's
parameter names but not their values, the release, and the ids of what the request or job was
about as tags (`request_id`, `trace_id`, `user_id`, `org_id`, `project_id`, `job_id`,
`document_id`, …). It holds no frame's variables (a stage's hold the document's text), no
request body, cookie or header, none of the log lines before it, and no email address; the
SDK's own integrations are off, its AI integrations above all, which would record prompts
and replies, and so are its traces, sessions and log forwarding.

An error the page renders into replaces the part of the page that broke (the page below the
header, or in a project the library, the document or the chat) with a sentence, a Reload
button and a reference, and is reported to the app (`POST /api/page-errors`,
`RATE_PAGE_ERRORS` a minute per client address), not to the tracker: the browser runs no
SDK, so the page's address with a search's words, its requests and its console go
nowhere, and the page's policy needs no other origin.
The app logs one line and sends the tracker an event of the `javascript` platform: the error's
kind, the API's status when it was the API's refusal, the frames in the page's built scripts
and the page's path. A frame names a line and column of the minified script; to read it, build
the release's commit with its source maps, which gives the same script byte for byte and its
map beside it, and look the position up in the map with any source-map tool:

```bash
git -C emudoc checkout <sha>
pnpm --dir emudoc/frontend exec vite build --sourcemap hidden --outDir /tmp/emudoc-<sha>
```

A member who meets an error has its reference to quote: a 500's sentence ends with the request's
id ("give the reference 0f8fad5b-… when you report it"), which the body also carries as
`request_id` and the response as `X-Request-ID`, and the page's error screen shows the id of the
request that reported it. The log's lines and the tracker's report carry the same id.

The tracker is a company that receives data unless it runs on the deployment's own network
(Sentry's EU region is a DSN under `ingest.de.sentry.io`, in Germany; every other `sentry.io`
host is its US region). The reports hold no documents; a team that keeps its data in the EU
takes Sentry's EU region or a tracker of its own. How long the reports are kept is the
tracker's setting. The DSN carries the project's key: on AWS it goes into
`/emudoc/<environment>/secrets/SENTRY_DSN` by hand, like the model keys ("The settings"), and the
plan refuses it among the roots' settings.

## Metrics

The API and the worker serve their metrics in Prometheus's text format at `METRICS_ADDRESS`, a
host and port of their own: the image listens on `0.0.0.0:9464` of its container, and a
checkout serves none unless told (`127.0.0.1:9464` on one machine). Any path answers;
`/metrics` is the convention. An address the process cannot listen on stops it at start, as a
port another process holds would.

The port is for the deployment's own network alone, never one the proxy forwards or a host
publishes: the metrics say how large the deployment is and which models it pays for. The
compose file publishes nothing of it, and the image does not `EXPOSE` it, so `docker run -P`
leaves it alone too. No label names an organisation, a user, a document or anything a member
typed: a series per organisation would grow with every sign-up, and one organisation's figures
are its usage, `python -m app.maintenance usage <slug>`, every organisation's side by side
`costs` ("What each organisation costs", below). A label holds the app's own words: a
model's configured name, a kind of work, a stage, a job's kind, a status, a route's
pattern (`/api/projects/{project_id}/documents`, never the path with its ids).

What each process counts as it works, the API's and the workers' each their own (add them up
across the instances, `sum without (instance)`):

| Metric | What it counts |
| --- | --- |
| `emudoc_http_requests_total`, `emudoc_http_request_seconds` | Requests the API answered, by `method`, `route` (`other` for none) and `status`; an answer's event stream lasts as long as the answer is written |
| `emudoc_db_connections{state}`, `emudoc_db_connections_limit`, `emudoc_db_pool_timeouts_total` | The process's pool: connections `in_use` and `idle`, what `DB_POOL_SIZE` + `DB_MAX_OVERFLOW` allow, and the requests and answers refused for a full pool |
| `emudoc_model_requests_total`, `emudoc_model_request_seconds` | Requests to a model or the embeddings endpoint, by `model` (as configured), `role` (`index`, `agent`, `embed`) and `outcome`: `ok`, the provider's HTTP status (`401`, `429`, `503`), `timeout`, `unreachable`, `filtered`, `malformed`, `stopped` or `failed` |
| `emudoc_model_first_event_seconds` | How soon a model began an answer, which is when a member sees it start |
| `emudoc_model_tokens_total` | The tokens the providers counted, by `model`, `role` and `direction` |
| `emudoc_model_fallbacks_total` | Requests a model failed in a way that asked its fallback: its provider was down, or refused the key, the account or the model ("The models") |
| `emudoc_stage_runs_total`, `emudoc_stage_seconds` | Indexing stages, by `stage` and `outcome`: `done`, `skipped`, `failed`, `transient` (tried again), `deferred` (to the next UTC day), `quarantined`, `crashed`, `database_lost` or `lease_lost` |
| `emudoc_index_runs_total` | Indexing runs, by how the document stood at the end: `ready`, `failed`, `quarantined` |
| `emudoc_job_runs_total`, `emudoc_job_seconds`, `emudoc_job_wait_seconds` | Jobs a worker ran, by `kind` and `outcome` (`done`, `failed`, `crashed`, `abandoned`, `lost`, `unsettled`), how long each ran, and how long each waited for a worker once it was due |
| `emudoc_answer_first_text_seconds` | How long a member waited from the question to its answer's first words: the job's wait and its run up to its first text, once a run ("Service levels") |
| `emudoc_document_ready_seconds` | How long an upload took to be ready, from the upload to the end of its first indexing, retries and waits included, once a document ("Service levels") |
| `emudoc_worker_threads{lane}`, `emudoc_worker_busy{lane}` | A worker's job threads alive and running a job, by `lane` (`indexer`, `answerer`, `mailer`) |
| `emudoc_parses{state}` | A worker's parses `running`, each a child process (`PARSE_CONCURRENCY` at most), and indexers `waiting` for one to end ("Large documents") |
| `emudoc_worker_listening` | 1 while a worker hears of queued jobs as they are queued, 0 while it polls for them: behind a pooler in transaction mode without `DATABASE_LISTEN_URL`, or while its listener's lost connection waits to be made again |
| `emudoc_upload_bytes`, `emudoc_upload_rejections_total` | The sizes of the files the API stored, and the files it refused by `reason`: `too_many` (a whole request), `unsupported`, `empty`, `too_large`, `wrong_type`, `duplicate`, `folder` (a folder its path names that cannot be made), `unopened` (a message or a ZIP archive the page uploaded that cannot be opened, one inside another, or a file of an archive that does not unpack), `indexing` (a document's new version while it is being indexed) |
| `emudoc_rate_limited_total` | Requests refused for a rate, by the limit's `scope` ("Rates") |
| `emudoc_mail_sent_total` | Tries of the mail the app posted, by `outcome`: `sent`, `retried` (refused for now and queued again), `failed` (refused for good, or for the last time); the operator's own reports are not counted ("Mail") |
| `emudoc_mail_events_total` | Addresses the mail provider said not to write to, by `kind`: `bounce`, `complaint`; counted by the API for Resend's, by the worker for SES's ("Mail") |
| `emudoc_inbound_messages_total` | Messages sent to projects' addresses, by `outcome`: `kept`, `empty` (nothing in it to keep), `refused_sender`, `unauthenticated`, `unreadable`, `too_large`, `refused_project`, `unknown_address` (no project's) ("Mail in") |
| `emudoc_connector_syncs_total` | Syncs of connected folders, by `outcome`: `synced`, `unchanged` (nothing changed since the last), `failed` (stopped partway, or the folder gone), `paused` (the grant or the member), `held` (the project) ("Connected folders") |
| `emudoc_webhook_deliveries_total` | Tries of the events posted to the organisations' webhooks, by `outcome`: `delivered` (a 2xx), `retried` (answered otherwise or not at all, and queued again), `failed` (the last try), `refused` (not sent: an address on the deployment's own network or over plain HTTP, a secret no key decrypts) ("Webhooks") |
| `emudoc_connector_files_total` | Files of connected folders the syncs read, by `outcome`: `kept`, `rejected`, `trashed` (its file gone), `replaced` (its document given the file's new version) ("Connected folders") |
| `emudoc_renders_total`, `emudoc_render_seconds` | PDF pages drawn as images, by `purpose` (`pane`, `thumbnail`, `ocr`, `ahead`) and `outcome`: `drawn`, `refused` (a page that cannot be drawn), `timeout` (`RENDER_TIMEOUT`), `out_of_memory` (`RENDER_MEMORY_MB`), `crashed` or `unstarted` (no process could draw it); the seconds include the wait for a free process ("Drawing pages") |
| `process_*`, `python_*` | The process's CPU, memory and open files, and Python's collector |

What the database holds, which only a worker serves, reading it when asked, at most once every
15 seconds, so that the queries grow with the workers and not with the scrapers. Every worker
serves the same figures: take one, `max without (instance)`. A worker whose reading failed
serves none of them until the next and logs why, so the series go stale rather than hold an old
value.

| Metric | What it holds |
| --- | --- |
| `emudoc_documents{status}` | Documents `queued`, `processing`, `ready`, `failed`, `quarantined`, and `trashed` for those in the trash whatever their status (their originals still count in `emudoc_stored_bytes`) |
| `emudoc_jobs{kind,state}` | Jobs `due` (to be claimed now), `waiting` (a retry's backoff, or the next UTC day), `running`, and `failed` (kept 30 days for `failed-jobs`) |
| `emudoc_job_oldest_due_seconds{kind}` | How long the oldest due job has waited for a worker: the backlog's age |
| `emudoc_answers_in_flight` | Answers queued or being written (`MAX_ANSWERS` holds each organisation to 20) |
| `emudoc_stored_bytes` | The bytes of the uploaded originals the workspace keeps |
| `emudoc_spent_today{currency}`, `emudoc_daily_spend_cap{currency}` | The known cost of the models' work since midnight UTC (`MODEL_PRICES`), and `DAILY_SPEND_CAP` (0 for none) |
| `emudoc_db_table_rows{table,state}` | Each of the app's tables' rows, `live` and `dead` (waiting for vacuum), as the server's statistics estimate them ("Vacuum") |
| `emudoc_db_oldest_transaction_seconds` | How long the app's role's oldest open transaction has been holding vacuum back; 0 for none |
| `emudoc_wal_archived_total`, `emudoc_wal_archive_failures_total`, `emudoc_wal_last_archived_seconds`, `emudoc_wal_segments_pending` | The server's archiving of its write-ahead log ("Backups"): the segments archived and the attempts that failed, how long ago the last went, and the finished segments waiting; none of them from a server that archives nothing (`archive_mode` off) or is in recovery |

A Prometheus on the compose file's network finds every replica by the services' names, and
keeps which kind of process each is as `process`, with `deploy/prometheus/prometheus.yml` (on
AWS the instance runs it so: "Alerts"):

```yaml
scrape_configs:
  - job_name: emudoc
    dns_sd_configs:
      - names: [api, worker]
        type: A
        port: 9464
    relabel_configs:
      - source_labels: [__meta_dns_name]
        target_label: process
```

```bash
docker run -d --name prometheus --network emudoc-deploy_default -p 127.0.0.1:9090:9090 \
  -v "$PWD/deploy/prometheus:/etc/prometheus:ro" prom/prometheus:v3.16.0
```

(checked with Prometheus 3.15 against the image's API and worker under those names). The file
also loads the alerting rules beside it and sends what fires to an Alertmanager: "Alerts",
below. The
counters whose labels are known in advance start at zero: every stage with every outcome, every
job's kind with every outcome, the upload refusals, the rate limits and the renders, so that
`increase` sees the first failure after a start. A model's outcomes appear with their first request, since the
statuses a provider answers are not known beforehand.

A few of the questions it answers:

```promql
# The backlog's age, and the live workers
max without (instance) (emudoc_job_oldest_due_seconds{kind="index_document"})
count(emudoc_worker_threads{lane="indexer"})
# Stages that fail, and models that fail over, in the last quarter hour
sum by (stage, outcome) (increase(emudoc_stage_runs_total{outcome=~"failed|crashed|transient"}[15m]))
sum by (model) (increase(emudoc_model_fallbacks_total[15m]))
# How soon an answer begins, at the 95th percentile, and the pools' share in use
histogram_quantile(0.95, sum by (le) (rate(emudoc_model_first_event_seconds_bucket[5m])))
emudoc_db_connections{state="in_use"} / ignoring (state) emudoc_db_connections_limit
```

The metrics say that some answers were slow; which ones, and where each one's time went, is in
its run's line of the log ("Traces").

The image runs one uvicorn process per container, which the metrics take for granted: with
uvicorn's `--workers`, each process would count apart behind one port, and the scraper would
read whichever answered.

### What each organisation costs

Per organisation, the figures are the usage rows the app records as it works, not the metrics:
`python -m app.maintenance costs`, run as the operator's other commands are ("The image"),
prints what each organisation's model use cost over the last 30 days, or `--days`, up to today
in UTC, costliest first, all of them together against `DAILY_SPEND_CAP`. An install holds
one workspace, so its row and the total agree; a database that holds several, as development's
may, lists each:

```text
Model costs per organisation from 2026-10-13 to 2026-10-19 (UTC), in USD, costliest first:
  organisation    cost    limit       top day  at limit  answers  calls  pages
  northwind     1.7698                 0.2865                380  3,695  2,400  Northwind Traders
  all           1.7698  25.0000  0.2865 (1 %)         0      380  3,695  2,400  1 organisation
The top day is the costliest; all of them together are held to DAILY_SPEND_CAP, the
top day's share of it beside it. At limit counts the days that reached it.

By kind of work and model:
  kind    model                             cost  calls  tokens in  tokens out
  ocr     google:gemini-3.5-flash-lite    1.1730  2,550  4,590,000   1,785,000
  answer  google:gemini-3.5-flash-lite    0.7885  1,642  6,732,200     288,080
  enrich  google:gemini-3.5-flash-lite    0.1858    258  1,548,000      77,400
  answer  anthropic:claude-haiku-5-5      0.0465      9     36,000       2,100
  embed   embeddinggemma                unpriced      0  3,850,000           0

These models' tokens were recorded without a price, so no cost above counts them;
MODEL_PRICES prices a model per million tokens in and out from its next row on:
  embeddinggemma    3,850,000 tokens  (embed)
```

Between the two tables comes the cost of each kind of work by the version of its prompts ("The
prompts' versions", above), the latest seen first, with the first and last day of the span it
was seen on, so that what a change to the prompts did to a kind's calls and tokens reads from one
line to the next (here a release on 2026-10-17 that changed the answers' prompts):

```text
By kind of work and version of its prompts, the latest first, with the days seen:
  kind    prompts   first       last          cost  calls  tokens in  tokens out
  answer  b7e2049c  2026-10-17  2026-10-19  0.2310    498  1,976,000      84,620
  answer  41d9a6f3  2026-10-13  2026-10-16  0.6040  1,153  4,792,200     205,560
  enrich  0c58e1d7  2026-10-13  2026-10-19  0.1858    258  1,548,000      77,400
  ocr     e93f2a60  2026-10-13  2026-10-19  1.1730  2,550  4,590,000   1,785,000
```

The days are those within the span, and a version's calls are many answers' or documents' that
vary: compare tokens per call, and a golden run for what the answers say. A question set's
answers are `answer` work with a task of their own after the prompt, so they show as a version
beside the chats' at once. The embeddings, uploads and pages drawn ask no prompt and are in no
line.

A day that reached the cap is one on which every question was refused and indexing waited
for midnight UTC; the answers are those that called a model, counted once
however many runs they took, and the pages those its uploads brought in. A cost is what the
rows were priced at when they were recorded (`MODEL_PRICES`), so a price added later counts
from then on, and "unpriced" means tokens without one: expected of an embeddings server of the
deployment's own, as above, which no provider bills, and a gap in the daily cap for a
model a provider bills. `usage <slug>` breaks one organisation down by day, kind and unit,
the questions asked among them (`answer` in `questions`, one a question, written as it is
asked); the workspace's members read its pages and questions by day and by month on its usage
page (`/organization/usage`), and its owners and admins what the models cost there too.
The command reads the database and changes nothing; an organisation is named by its slug and
name, never a member's address. `costs --days 1` is today's, which the spending warning
("What the worker mails") names.

## Alerts

Three watchers, since none of them sees everything: the worker, which sees the database and
what it reaches and mails `ALERT_EMAIL`; rules on the metrics, which see every process from
outside and need a scraper; and on AWS the host, which sees the containers. On AWS the
instance runs the scraper with the rules, and CloudWatch watches the instance from outside and
hears when the rules fall silent ("On AWS", below).

### What the worker mails

With `ALERT_EMAIL` set and a mail relay, the worker's alerter thread reports to the address
every quarter of an hour: the jobs it gave up on ("Emudoc gave up on…"), the day's
spending at 80 % of `DAILY_SPEND_CAP`, the uploads the scan quarantined, and what its watch sees go wrong
(`backend/app/watch.py`). The watch writes once when a concern begins ("Emudoc alert: …", what
is wrong and what to do) and once when it is over ("Emudoc alert over: …", after how long),
listing what goes on meanwhile, whichever worker looked; what is raised and not over is a row of
`watch_alerts`. Every deployment has it, a scraper or not:

| Alert | When | Where to look |
| --- | --- | --- |
| Questions, documents or emails wait for a worker | The oldest due question or email has waited 5 minutes, the oldest due document an hour | The workers' logs; more workers, or `ANSWER_CONCURRENCY`, `WORKER_CONCURRENCY`; for mail, a relay that hangs ("Mail") |
| Indexing fails | Of the documents whose indexing ended in the last hour, 5 and half or more have a stage that failed, by stage; one waiting for a retry is not counted until it settles, and a summary or a vector that failed counts though the document is ready | Each document's stages; the worker's log; `python -m app.maintenance models` |
| Answers fail | Of the answers written in the last hour, 5 and a quarter or more failed | The worker's log, `app.llm` and `app.answers`; `models`; the daily spending cap, which mails its own warning |
| `AGENT_MODEL` (or `INDEX_MODEL`) fails over to its fallback | The fallback answered a request of the role's in the last hour (the usage events name the model that answered) | The worker's `app.llm` line: "was not found, or is served no longer" is a retired model, "was rejected" a key revoked or out of credit ("The models") |
| The embeddings endpoint, the malware scanner or the storage does not answer | Asked as the start asks them, at each round | Their sentence says what failed |
| The database fails to archive its log, or its archive falls behind | The archiver's last attempt failed, or finished segments of the write-ahead log have waited past the 5-minute recovery point (`pg_stat_archiver`; a server that archives nothing has neither) | `pgbackrest --stanza=emudoc check`, the server's log ("Backups") |
| The database needs looking at | What `python -m app.maintenance database` flags, every table looked at: pgvector before 0.8, production's role without timeouts, a collation made with another ICU, a table autovacuum is behind on, a transaction open over an hour | "The database", "Vacuum" |

A check that cannot run (the log says "The watch could not check …") keeps the alerts it raised:
a concern that cannot be seen is not over. The worker cannot report itself gone, nor an API
that is down, nor what one process alone knows (its pool, its listener): that is the rules'
and the host's. The mails name tables, models, services and counts, never a document, a
question or an address.

### Rules on the metrics

`deploy/prometheus/alerts.yml` holds 36 Prometheus alerting rules over the metrics ("Metrics",
above), each tested against the series that should and should not fire it
(`deploy/prometheus/alerts.test.yml`, `promtool test rules`, in CI). They expect the scrape
configuration of `prometheus.yml` beside them: job `emudoc`, and `process` `api` or `worker`.
`critical` is a member waiting now or a backup losing changes, `warning` is for the working day,
and `none` the watchdog's. The thresholds are a start; tune them, and their tests, to the
deployment.

| Group | Rules |
| --- | --- |
| The processes | `EmudocProcessDown` (a target unanswered for 5 minutes), `EmudocNoWorker`, `EmudocNoApi`, `EmudocRestarting` (a process started twice again in half an hour: a worker exits when a thread ended), `EmudocWorkerNotListening` (polling for 10 minutes) |
| The work | `EmudocQuestionsWaiting` (the oldest due question over a minute, for 5), `EmudocDocumentsWaiting` (the oldest due document over half an hour, for 15), `EmudocNoDatabaseFigures` (no worker serves them: none runs, or its readings fail), `EmudocStageFailing` (a stage failed or crashed in most of its runs of the last hour, 5 at least), `EmudocStageCrashing`, `EmudocJobsGivenUp` |
| The mail | `EmudocMailWaiting` (the oldest due email over 5 minutes, for 5: links wait with it), `EmudocMailRefused` (the relay refused every email of half an hour, for now), `EmudocMailBounces` (over 3 in 100 of a day's mail bounced or complained of, 5 at least) |
| The models | `EmudocModelsDown` (no model of a role answered in 15 minutes, its fallback included), `EmudocEmbeddingsDown`, `EmudocModelRefused` (401, 402, 403 or 404), `EmudocModelFallingBack`, `EmudocModelRateLimited` (10 refusals for the rate in 15 minutes), `EmudocModelErrors` (a fifth of the requests failing on the provider's side) |
| Capacity | `EmudocPoolSaturated` (90 % of a process's connections in use for 10 minutes), `EmudocPoolTimeouts` (members answered "busy"), `EmudocStorageGrowing` (10 GiB of new originals in a day), `EmudocSpendNearCap` (80 % of `DAILY_SPEND_CAP`), `EmudocSpendCapReached` |
| The database | `EmudocWalArchiveFailing`, `EmudocWalArchiveBehind`, `EmudocVacuumBehind` (10,000 dead rows and a fifth of a table, for an hour), `EmudocLongTransaction` (over an hour) |
| The service levels | `EmudocErrorBudgetFastBurn` and `SlowBurn`, `EmudocAnswersLateFastBurn` and `SlowBurn`, `EmudocDocumentsLateFastBurn` and `SlowBurn`: an objective's budget spent too fast ("Service levels", below) |
| The watchdog | `EmudocWatchdog`, which always fires: its silence is the alarm |

Some of them say what the worker mails too, from the other side: the mail works without a
scraper, the rules without a worker. An Alertmanager under the name `alertmanager` on the same
network takes what fires, with a receiver of the deployment's own, such as mail:

```yaml
# alertmanager.yml
route:
  receiver: operator
  group_by: [alertname]
  repeat_interval: 4h
  routes:
    # Every two minutes or so to the dead man's switch, and to nobody else.
    - matchers: ['alertname="EmudocWatchdog"']
      receiver: deadman
      group_wait: 0s
      group_interval: 1m
      repeat_interval: 2m
    - matchers: ['severity="critical"']
      receiver: operator
      repeat_interval: 1h
# A process that does not answer, said once: "No API has answered" covers the API's address.
inhibit_rules:
  - source_matchers: ['alertname=~"EmudocNoApi|EmudocNoWorker"']
    target_matchers: ['alertname="EmudocProcessDown"']
    equal: [process]
receivers:
  - name: operator
    email_configs:
      - to: ops@example.com
        from: alerts@emudoc.example.com
        smarthost: smtp.example.com:587
        auth_username: alerts@emudoc.example.com
        auth_password_file: /etc/alertmanager/smtp-password
  - name: deadman
    webhook_configs:
      - url_file: /etc/alertmanager/deadman-url
```

```bash
docker run -d --name alertmanager --network emudoc-deploy_default \
  -v "$PWD/alertmanager.yml:/etc/alertmanager/alertmanager.yml:ro" \
  -v "$PWD/smtp-password:/etc/alertmanager/smtp-password:ro" \
  -v "$PWD/deadman-url:/etc/alertmanager/deadman-url:ro" prom/alertmanager:v0.35.0
```

(the rules checked with Prometheus 3.15 against a running API and worker: every rule evaluated
without an error, and a stopped API put `EmudocProcessDown` and `EmudocNoApi` pending; the
configuration with `amtool check-config` of Alertmanager 0.35.0). Alertmanager alone is quiet
when Prometheus is down, and nothing on the machine reports the machine gone: the watchdog,
`EmudocWatchdog`, is for a dead man's switch, a service that calls when it stops hearing it
(a monitoring service's heartbeat check takes the webhook), whose address `deadman-url`
holds. On AWS the alarms of CloudWatch are that service ("On AWS", below).

### The host's and the timers'

Docker starts again a container that exits, never one whose health check fails, and the
compose file's checks only mark it. On AWS the host looks every five minutes
(`emudoc-watch.timer`, `deploy/aws/host/watch`) and reports to the alerts topic, and to the
pages topic ("On call"), a container that is missing, stopped, restarting or unhealthy, or a
one-shot that failed, once it was so at two looks in a row, and again when every container is
well; a release holds the watch off.
The drill and the credentials' renewal report their own failure (`OnFailure=`, "The drill on
AWS"). On one machine, a timer the deployment runs for its drill ("The drill") mails its
failure the same way, and a container's trouble is the rules' `EmudocProcessDown`.

### On AWS

The instance runs the rules itself. `deploy/aws/compose.yml` adds Prometheus 3.15 with
`deploy/prometheus` as the release's image carries it, so that a release brings its rules, and
Alertmanager 0.34 with `deploy/aws/alertmanager.yml`, which sends what fires to the alerts
topic: a message per rule, with its sentences and what to do, again every 4 hours (critical,
every hour) while it lasts, and once more when it is over; a `critical` one goes to the pages
topic as well, in a few words ("On call"). Both run read-only and keep only
their own data, in a volume of the instance's disk: Prometheus 15 days of samples, 2 GB at
most, and Alertmanager its silences; both are gone with a replaced instance. Alertmanager
publishes with a role of its own (`emudoc-<environment>-alertmanager`), which may publish to
the environment's three topics and do nothing else: the host renews an hour of it every quarter
of an hour into `/run/emudoc/aws/alertmanager`, as it does the app's, and Alertmanager reads
the file again for every message. Prometheus holds no credentials. Neither page leaves the
instance: Prometheus answers on its loopback at 9090 and Alertmanager at 9093, which Session
Manager forwards:

```bash
aws ssm start-session --target <instance> --document-name AWS-StartPortForwardingSession \
  --parameters portNumber=9090,localPortNumber=9090     # then http://localhost:9090/alerts
# On the instance: hold a rule back for the length of a piece of work.
sudo emudoc-compose exec alertmanager amtool --alertmanager.url=http://127.0.0.1:9093 \
  silence add --duration 2h --comment 'Reindexing' alertname=EmudocDocumentsWaiting
```

Rules on an instance cannot report the instance gone, so CloudWatch watches it from outside
(`deploy/aws/modules/environment/alerts.tf`), with three alarms that publish to the alerts
topic and the pages topic when they begin and when they are over:

| Alarm | When | What happens |
| --- | --- | --- |
| `emudoc-<environment>-alerts-silent` | No message on the heartbeat topic for three five-minute periods in a row. Alertmanager sends the watchdog there every two to three minutes, and nobody subscribes to it: the instance, its Docker, Prometheus or Alertmanager stopped, or Alertmanager's credentials were not renewed. A topic that heard nothing is in alarm too, so a new environment's is until its first release | Its description names where to look: `aws ec2 describe-instance-status`, then on the instance `sudo emudoc-compose ps prometheus alertmanager` and `systemctl status emudoc-credentials` |
| `emudoc-<environment>-instance-system` | AWS's system status check fails for 2 minutes: the hardware or AWS's network under the instance | EC2 recovers the instance onto other hardware, with its id, address and disk, and Docker starts the containers again |
| `emudoc-<environment>-instance-status` | The instance's own check fails for 3 minutes: out of memory, a kernel that hangs, a broken disk | EC2 reboots it, and its start runs the recorded release (`emudoc-boot`); `aws ec2 get-console-output --latest` says why |

CloudWatch publishes only to a topic whose key lets it, so the three topics (alerts, pages and
the heartbeat) are encrypted with a key of the environment's own
(`alias/emudoc-<environment>-alerts`), which the instance's role and Alertmanager's may use
through SNS alone and CloudWatch to encrypt its messages. The key costs a dollar a month (each
of its first two yearly rotations adds one), the alarms ten cents each, and the heartbeat's
15,000 messages a month are within SNS's free million; an SMS is SNS's price for its country.

Some things are said twice, on purpose. A container that stops is the host's watch's ("api:
exited with status 1", after ten minutes, with the commands to look) and the rules' ("No API
has answered", after five): the watch publishes with the instance's own role and sees
Prometheus and Alertmanager themselves, so it says what they cannot, and the rules see a
process that runs for Docker but does not answer. Within the rules, Alertmanager holds
`EmudocProcessDown` back while `EmudocNoApi` or `EmudocNoWorker` says it of the same process.
A failed instance is its status alarm's, and the silence's once it lasts a quarter of an hour:
the first says why, the second that nothing on the instance reports meanwhile.

## Service levels

What the deployment promises its members, measured by the app itself, so that an alert says the
promise is in danger before anything is broken outright. Each objective holds over 30 days, and
the share of requests, answers or uploads it lets miss is its error budget:

| Objective | Measured by | Budget |
| --- | --- | --- |
| **The app answers**: 99.5 % of the members' requests end without a server error | `emudoc_http_requests_total`, a 5xx status against every status, the probes left out | 0.5 %, a request in 200 |
| **Answers begin**: 95 % of answers write their first words within 30 seconds of the question | `emudoc_answer_first_text_seconds`: the job's wait for a worker, and its run up to the first text | 5 % |
| **Uploads are ready**: 95 % of uploads can be searched and asked about within 10 minutes | `emudoc_document_ready_seconds`: from the upload to the end of its first indexing, retries and waits included | 5 % |
| **The app is up**: `/api/health` answers 200 through the proxy in 99.5 % of the minutes | An uptime service outside the deployment, asking every minute | 0.5 %, 3.6 hours |

The numbers are a start, for a beta on one machine: tune them to what the members see, in the
rules and their tests too, as the alerting rules' thresholds are.

- **A server error** is a 500, the app's own fault, or a 503: a pool out of connections, a
  statement the database stopped, a database or a storage that does not answer. A 4xx is the
  request's (a rate, a file too large) and counts as answered. The probes
  (`/api/livez`, `/api/health`) are left out, since an orchestrator asks them more often than
  the members ask anything, and an answer's event stream counts once, by the status it began
  with. A request that never reached the app, the proxy's 502 while the API restarts or every
  request while the machine is down, is not counted at all: that is the last line's, which only
  a check from outside sees. Any uptime service does, a Route 53 health check on AWS or one of
  the hosted ones, asking `https://<domain>/api/health` from more than one place and calling
  after two misses in a row.
- **An answer's wait** runs from the question, when its job is due, to its first words. The
  agent's first tool calls are inside it, which the member sees as steps meanwhile. A run that
  was lost starts the answer over and counts again, from the question. An answer that failed or
  was stopped before any text counts nothing here: its failure is the watch's "Answers fail"
  and the rules' `EmudocModelsDown`.
- **An upload's wait** is measured once per document, when it is first ready
  (`documents.ready_at`), so "Index again" or a new embedding model measures nothing. A
  document that failed counts nothing here (the watch's "Indexing fails"). The day's spending
  limit counts: a document held until midnight UTC is late by the member's clock. A scanned
  page is a model request of its own, so a deployment whose members upload long scans sets the
  objective by them.

The figures over the period, and what a member waits at the 95th percentile over the last day:

```promql
# The share of the members' requests without a server error
1 - sum(increase(emudoc_http_requests_total{route!~"/api/(livez|health)", status=~"5.."}[30d]))
  / sum(increase(emudoc_http_requests_total{route!~"/api/(livez|health)"}[30d]))
# The share of answers begun within 30 seconds, and of uploads ready within 10 minutes
sum(increase(emudoc_answer_first_text_seconds_bucket{le="30.0"}[30d]))
  / sum(increase(emudoc_answer_first_text_seconds_count[30d]))
sum(increase(emudoc_document_ready_seconds_bucket{le="600.0"}[30d]))
  / sum(increase(emudoc_document_ready_seconds_count[30d]))
histogram_quantile(0.95, sum by (le) (rate(emudoc_answer_first_text_seconds_bucket[1d])))
histogram_quantile(0.95, sum by (le) (rate(emudoc_document_ready_seconds_bucket[1d])))
```

(`le="30.0"`: Prometheus 3 writes a bucket's bound as a float.) On AWS the instance's
Prometheus keeps 15 days, so its figures are the last fortnight's, `[14d]`; a month needs a
Prometheus elsewhere ("What it leaves out").

Six rules of `deploy/prometheus/alerts.yml` keep the three the app measures, two each, after the
multiwindow burn rates of Google's SRE workbook. A **fast burn** spends the budget 14.4 times as
fast as 30 days allow (2 % of it in an hour, all of it in two days), over the last hour and still
over the last 5 minutes, so that the alert ends soon after the burn does: `critical`, for
someone to look now. A **slow burn** spends it 6 times as fast (5 % in six hours, all of it in
five days), over 6 hours and the last 30 minutes: `warning`, for the working day. Each needs a
minimum in its window, 100 requests or 10 answers or uploads, so that one failure in a quiet
hour is no burn:

| Rules | Fire at |
| --- | --- |
| `EmudocErrorBudgetFastBurn`, `EmudocErrorBudgetSlowBurn` | Over 7.2 %, or over 3 %, of the members' requests ending in a server error |
| `EmudocAnswersLateFastBurn`, `EmudocAnswersLateSlowBurn` | Over 72 %, or over 30 %, of answers begun after 30 seconds |
| `EmudocDocumentsLateFastBurn`, `EmudocDocumentsLateSlowBurn` | Over 72 %, or over 30 %, of uploads ready after 10 minutes |

A budget spent before its 30 days are over is the sign to put the work that makes the app
answer, begin or index sooner ahead of the next feature, until the figures are back within it.

## Load

What one machine carries, as the load test measures it: `python -m loadtest` in `backend/`
(`backend/loadtest/`). It starts the app in production's shape, the API as the image's command
and the worker as a process of its own, each serving its metrics, against a database of its own
on the compose server (`emudoc_load`, made afresh) and a stand-in for the models and the
embeddings endpoint (`frontend/e2e/standin.py --fill`, which answers an indexing request with
what the request holds: a scanned page's text made up from its picture, a document's title,
kind, summary, section summaries and the facts its "Name: value" lines state), so that a run
costs nothing and its figures are the app's own time. Then one member, with the rates a
minute lifted (one member asks for what twenty would):

1. **library**: uploads 3,000 documents of the sample set's kinds (invoices, delivery notes,
   contracts, price lists, emails and supplier reviews, as Markdown, PDF and Word), 50 a
   request and two requests at once, and searches the project twice a second while it is
   indexed;
2. **search**: searches it 300 times one at a time and 300 times eight at a time, and asks
   for facts, and runs the agent's `list_documents`, by a word and by a piece of one, which
   only `ilike` finds; then ranks the passages for 100 of the searches as the search's vector
   ranking does, at `hnsw.ef_search` 40 (pgvector's default), 100, 200 and 400 and once through
   the vector index whatever the planner would choose, against the nearest by exact
   distance; then uploads a project of 30 other documents beside the library
   (`--small-documents`), searches it 100 times, searches it and the library together 100
   times as the search of every project (`GET /api/search`), and ranks its passages likewise;
3. **chats**: asks 20 questions at once in 20 chats, five times, following each answer as the
   page does, first with the stand-in answering at once, then at a hosted model's pace
   (`--first-token`, 1 s before each turn, and `--words-per-second`, 40);
4. **paced**: uploads 200 of the documents into a project of their own with each indexing
   request taking `--index-seconds` (3);
5. **large**: uploads a 300-page scan (62 MB) and asks the 20 questions while it is read, a
   50 MB workbook of seven sheets of 90,000 order rows, and the same orders in one sheet, then
   opens each page of the scan, four at a time as a member scrolls, twice; each indexing
   request, a page of the scan each, takes `--large-index-seconds` (0, at once).

It prints a table of what the member waited, at the 50th and 95th percentiles and the longest,
and notes of what the app did meanwhile: documents indexed a minute, each stage's average, the
indexing requests, and the most memory the API's and the worker's processes held with their
children; `--out` keeps the figures as JSON. A run takes about 12 minutes and needs the compose
database, and the compose bucket with `--storage s3` (`docker compose --profile s3 up -d
--wait`); the large files are made once, which takes longer, and kept in its directory
(`--dir`, by default `$TMPDIR/emudoc-load`). Every setting a figure depends on is set in
`backend/loadtest/deployment.py`, so `.env` changes none, and `--set NAME=VALUE` runs it with
one changed, `--set ANSWER_CONCURRENCY=20` say; `--phases` picks some. It exits 1 when its own
work went wrong (a library document not ready, an answer not done), since such figures are not
of that work. CI runs it at a size that takes a minute, for the harness's sake: a runner's
figures mean nothing.

### The figures

On 2026-10-19, on one machine (16 × AMD Ryzen 7 7435HS, 23 GB) with the compose Postgres 17
beside the app and the files in a directory, at the defaults (`WORKER_CONCURRENCY` 16,
`PARSE_CONCURRENCY` 2, `ANSWER_CONCURRENCY` 8, `DB_POOL_SIZE` 10 and `DB_MAX_OVERFLOW` 20,
`RENDER_PROCESSES` 2) with `OCR_MAX_PAGES` raised to the scan's 300 pages:

| What the member waited | n | p50 | p95 |
| --- | --- | --- | --- |
| An upload of 50 files | 60 | 1.54 s | 2.49 s |
| A search in the 3,000 documents (9,300 passages), one at a time | 300 | 71 ms | 79 ms |
| The same, eight at a time | 300 | 76 ms | 90 ms |
| The same, while the 3,000 were indexed | 254 | 42 ms | 59 ms |
| Facts by a word, or by a piece of one (15,210 facts) | 100 | 39 ms | 43 ms |
| The agent's `list_documents` by a word, or by a piece of one | 100 | 24 ms | 26 ms |
| A question asked, 20 at once | 100 | 227 ms | 406 ms |
| The question to the answer's first event, the step "Searching…", 20 at once | 100 | 635 ms | 964 ms |
| The question to its first words, 20 at once, the model at once | 100 | 783 ms | 1.08 s |
| The same, at the hosted model's pace | 100 | 4.58 s | 6.79 s |
| The same, with `ANSWER_CONCURRENCY` 20 | 100 | 2.76 s | 2.90 s |
| An upload of 3,000 documents to each one ready, the model at once | 3,000 | 83 s | 136 s |
| 200 documents to each one ready, at 3 s an indexing request | 200 | 22 s | 39 s |
| A page of the scan drawn, four asked at once | 300 | 602 ms | 629 ms |
| The same, from a bucket (the compose bucket, on the loopback address) | 300 | 675 ms | 730 ms |
| A page of the scan kept from before | 300 | 26 ms | 32 ms |
| The same, from the bucket | 300 | 52 ms | 63 ms |

The two rows at the hosted model's pace were measured with the words of an answer at once: the
stand-in ignored `--words-per-second` until 2026-10-23. The first token's second before each
turn was paced, so the row with `ANSWER_CONCURRENCY` 20, where no question waits for an
answerer, stands; the row above it is short of the time each answer now holds an answerer for
its words (about 0.4 s at 40 a second), which the questions waiting for one add up, and is to be
measured again on an idle machine (`--phases library,chats`). The row with
`ANSWER_CONCURRENCY` 20 is a run earlier that day (with `DB_POOL_SIZE` 22, the pool
the worker then needed), the bucket's a run of the large phase with `--storage s3` (`--phases
large --sheet-mb 0`) once the API kept its copy of the original for the next pages; before it
copied the original for every page, and a page from the bucket took 2.06 s (p95 2.11 s).

Indexing at a hosted model's pace, 3 s an indexing request, by the worker's indexers (the paced
phase with `--paced-documents`, `--set WORKER_CONCURRENCY=…` and the pool that needs; the row
of 2 is the day's earlier run, before the parses had a limit of their own):

| `WORKER_CONCURRENCY` | Documents | Indexed a minute | Ready, p50 | p95 | The worker held |
| --- | --- | --- | --- | --- | --- |
| 2 | 200 | 37 | 163 s | 309 s | – |
| 8 | 600 | 145 | 124 s | 231 s | 414 MB |
| 16 | 1,200 | 289 | 121 s | 224 s | 414 MB |
| 32 | 1,200 | 554 | 60 s | 109 s | 437 MB |

With the model at once, `PARSE_CONCURRENCY` 4 indexed the 3,000 documents no sooner than 2
(959 a minute against 952, ready at p95 139 s against 136 s), the worker at 491 MB against
418 MB.

The large files, one at a time, with the model at once:

- **The 300-page scan** was ready 101 s after its upload, 96 s of them in the parse: its pages
  drawn one after another for the model (about 89 s) and read (320 requests, about 9 s). The
  20 questions asked meanwhile began as soon as without it. Read four pages at a time
  (`OCR_CONCURRENCY`, a run of the large phase alone), it was ready after 51 s, 46 s of them in
  the parse, its pages drawn two at a time by the worker's `RENDER_PROCESSES` children, which
  are then the narrow part; the worker held 469 MB.
- **The 50 MB workbook in seven sheets** (625,000 rows, 88 million characters) was ready after
  604 s: the parse 98 s, the structure 48 s, the summaries and facts 193 s in 3,126 requests
  (one for each 40,000 characters of rows), the embeddings 264 s (about 52,000 passages in
  1,620 requests, which took 26 s; the rest is the database writing each vector into its
  index). The worker's processes held 2.6 GB. Bounded later that day ("Large documents"), it
  is refused by `PARSE_MAX_CHARS` after 36 s, the worker's processes at 470 MB (580 MB at 16
  indexers). With the limit
  lifted (`--set PARSE_MAX_CHARS=100000000`) it is ready after 396 s: the parse 82 s, the
  structure 32 s, the summaries and facts 31 s in one request (each sheet shown by its first
  and last rows), the embeddings 251 s (25 s of requests); the worker held 590 MB and its
  parse child 400 MB.
- **A 10 MB workbook** (125,000 rows in two sheets, 17.6 million characters, 10,400 passages)
  was ready after 95 s in 626 requests, the worker at 640 MB while it embedded; bounded, after
  64 s in one request, the worker at 280 MB.
- **The same orders in one sheet** were refused by `PARSE_MAX_ROWS` after 31 s, the worker's
  processes at 1.55 GB, most of it what the workbook before had left the worker holding;
  bounded, at 440 MB (550 MB at 16 indexers).

The API's processes held 210 to 225 MB throughout, the worker's about 420 MB while the library
was indexed.

The scan at a hosted model's pace, 3 s an indexing request (`--phases large --sheet-mb 0
--large-index-seconds 3`), by the pages read at once (`--set OCR_CONCURRENCY=…`):

| `OCR_CONCURRENCY` | Ready | The parse | The worker held |
| --- | --- | --- | --- |
| 1 | 1,068 s | 1,004 s | 466 MB |
| 4 | 317 s | 252 s | 587 MB |
| 8 | 192 s | 128 s | 654 MB |

Of the rest, 62 s are its summaries and facts, 20 requests one after another, which the pages
read at once leave as they were.

At ten times the size, on 2026-10-20: 30,000 documents in one project (93,141 passages,
151,193 facts, a database of 1.1 GB; `--phases library,search --documents 30000 --set
WORKER_CONCURRENCY=8 --set DB_POOL_SIZE=22`, indexed in 38 minutes, 780 a minute), with the
vectors of a real model, Ollama's `nomic-embed-text` on the same machine (`--set
EMBEDDING_BASE_URL=http://127.0.0.1:11434/v1 --set EMBEDDING_MODEL=nomic-embed-text`), whose
vectors are the embeddings server's: the stand-in's vectors of words crowd together and tie as
no model's do, and made the vector index look worse than it is.

| What the member waited | n | p50 | p95 |
| --- | --- | --- | --- |
| A search in the 30,000 documents, one at a time | 300 | 199 ms | 267 ms |
| The same, eight at a time | 300 | 245 ms | 366 ms |
| Facts by a word, or by a piece of one (151,193 facts) | 100 each | 92–94 ms | 133–137 ms |
| The agent's `list_documents` by a word, or by a piece of one | 100 each | 93 ms | 98–99 ms |
| A search in a project of 30 documents (99 passages) beside them | 100 | 19 ms | 21 ms |

The searches ran at `hnsw.ef_search` 100; 200, the setting since, adds 2 to 4 ms. Most of a
search's time was then its keyword ranking, which fetched and ranked every passage that held
any word of the question: "penalty late deliveries" matched 42,279, which took 236 of the
search's 241 ms, and the vector ranking 2.

On 2026-10-30 the same run was made twice: with the keyword ranking as it was, then with the
passages that hold every word of the question ranked first, then those that hold all but one,
and those that hold any only while the others are fewer than the 40 candidates ("Search"
below). The machine was busy with other work through both:

| What the member waited | n | p50 | p95 | Before, p50 | p95 |
| --- | --- | --- | --- | --- | --- |
| A search in the 30,000 documents, one at a time | 300 | 43 ms | 111 ms | 222 ms | 304 ms |
| The same, eight at a time | 300 | 89 ms | 181 ms | 291 ms | 433 ms |
| The same, while the 30,000 were indexed | 2,269 | 70 ms | 163 ms | 262 ms | 419 ms |
| The search of every project, the library and the 30 documents | 100 | 42 ms | 109 ms | – | – |
| A search in the project of 30 documents | 100 | 23 ms | 26 ms | 25 ms | 28 ms |
| Facts by a word, or by a piece of one | 100 each | 103 ms | 140–144 ms | 108–109 ms | 148–150 ms |
| The agent's `list_documents` by a word, or by a piece of one | 100 each | 100 ms | 105–106 ms | 102–104 ms | 111–113 ms |
| An upload of 30,000 documents to each one ready | 30,000 | 1,114 s | 1,416 s | 1,337 s | 6,967 s |

The statement alone, over 100 of the load test's questions, ranked in 20 ms at the median and
68 at p95, where it had taken 183 and 266: "penalty late deliveries" now ranks the 11,599
passages that hold its words, or all but one, of the 52,293 that hold any. What a search
finds changed with it: three in four of its first ten hits were the same, and its first hit
for 65 of the 100. A question of one word most passages hold still ranks them all:
"invoice", in 43,645, about 130 ms.

The second run indexed the library 601 a minute against 207 (780 on 2026-10-20), and the
upload's last documents were ready within 24 minutes rather than two hours. From 2026-10-25
each document indexed asked whether its project had settled, to write its suggested questions,
by a statement that scanned the queued jobs again for each of the project's documents: 22 s
after each document at 22,000, run by the indexers side by side, and the indexing fell to 16 a
minute. It looked the jobs up once after that, in about 25 ms, until the suggested questions
were taken out on 2026-11-11. The rest of the gap to 780 was not
looked into.

The vector ranking's 40 candidates for 100 of the searches, against the 40 nearest passages
by exact distance:

| `hnsw.ef_search` | The 10 nearest found | The 40 nearest found | Searches that missed the nearest | p50 | p95 |
| --- | --- | --- | --- | --- | --- |
| 40, pgvector's default | 83 % | 82 % | 11 | 3 ms | 4 ms |
| 100 | 88 % | 88 % | 7 | 4 ms | 6 ms |
| 200 | 93 % | 92 % | 3 | 6 ms | 10 ms |
| 400 | 95 % | 95 % | 2 | 11 ms | 16 ms |
| Every passage's distance, sorted | 100 % | 100 % | 0 | 148 ms | 153 ms |

Two more samples of 100 found the 10 nearest at 83 and 89 % at 40, 92 and 94 % at 100, 93 and
95 % at 200, and 97 and 98 % at 400. What the index found instead was near: at 40 its 40th
candidate was a median 0.0007 farther from the question than the exact 40th, where the 40
nearest spread over 0.034.

In the project of 30 documents the planner sorted its 99 passages by distance (1 ms) and
found all 40 nearest. Made to walk the vector index instead, it found 11 candidates at fewest
of the 40, and 45 % of the nearest, in 105 ms: `hnsw.max_scan_tuples` (20,000) ends the walk
long before a project of 0.1 % of the table fills its 40. The planner walks the index only for
a project of a few percent of the table: as passages were moved into the small project, it
sorted them at 2,000 (2.2 %) and walked the index at 4,000 (4.3 %), where 40 candidates take a
walk of about 1,000 passages.

With the library's passages spread over 602 projects (18,000 of them moved into 600 projects
of 30), one connection's searches of the large project went over to the one plan Postgres
keeps for every project after the fifth, which sorts the whole project by distance (495 ms
for its 75,141 passages): they took 470 to 540 ms where they had taken 150 to 250. Planned
each for its project, as every search is now ("The database"), they stayed at 100 to 215 ms.

### What the figures mean

- **Answers** run in a worker's `ANSWER_CONCURRENCY` threads (8), one an answer from its
  question to its end, and at a hosted model's pace an answer takes about 2.5 s of a thread:
  the 20 questions were answered in three rounds, and the last began about 4 s after the first.
  Twenty threads answered all 20 at once. A worker whose `emudoc_worker_busy{lane="answerer"}`
  stands at `ANSWER_CONCURRENCY` wants more threads or another worker; a thread costs little,
  as it waits on the model, but the pool must grow with it (the worker refuses fewer than
  `WORKER_CONCURRENCY` + `ANSWER_CONCURRENCY` + 4 connections). The API follows each answer
  by looking at the table every 0.1 s, a connection for each look: 20 members reading answers
  are 200 short queries a second, and the API's 15 connections carried them (30 now).
- **Uploads** are indexed by a worker's `WORKER_CONCURRENCY` threads (16). A small document
  takes an indexer a fraction of a second of the app's own time, and each request to a model
  adds the model's, during which the indexer holds no database connection and next to no
  memory: at 3 s a request the documents indexed a minute grew with the indexers, 37 with 2,
  145 with 8, 289 with 16 and 554 with 32, and the worker's memory did not. So at the defaults
  an upload of about 2,800 documents is ready within the 10 minutes of the objective ("Service
  levels") at that pace, where 2 indexers, the default until then, readied about 370 within
  them and took 80 minutes over 3,000.
  Only `PARSE_CONCURRENCY` (2) of them parse at once, the part that takes memory, each in a
  child of up to `PARSE_MEMORY_MB`, and the others wait their turn: with the model at once the
  parses were the narrow part (the parse stage took 0.42 s on average, over half of it
  waiting), but
  four of them readied the 3,000 no sooner, since the uploads and the database set the pace
  there, and held 70 MB more. More indexers cost a database connection each (the worker
  refuses fewer than `WORKER_CONCURRENCY` + `ANSWER_CONCURRENCY` + 4, 28 at the defaults) and
  the provider's rate: 16 at 3 s are about 320 indexing requests a minute, more than a
  provider's lowest tiers take (`EmudocModelRateLimited`, then lower `WORKER_CONCURRENCY`). A
  member who uploads thousands at once is served by more indexers, or another worker, with the
  pool and the provider's limits to match.
- **A long scan** is read `OCR_CONCURRENCY` pages at a time (4), each drawn (0.3 s) and then
  sent to the model: at a hosted model's 3 s a page the 300-page scan was ready in 5.3 minutes,
  where one page after another took 17.8, past the objective by itself. Eight at a time took
  3.2 minutes, but every page in flight is a request the provider's rate limit counts: 16
  indexers reading scans 4 pages at a time are 64 requests at once, about 1,300 a minute at
  3 s, beyond a provider's lower tiers, so such a deployment lowers it rather than raise it.
  The pages in flight cost the worker 120 MB at 4 and 190 MB at 8, their images and the
  requests that carry them. `OCR_MAX_PAGES` (100) holds one scan to about 2 minutes, and its
  summaries and facts, one request after another, are now a fifth of its time.
- **A large workbook** was thousands of model requests and hours of an indexer at a hosted
  model's pace; its sheets are now one request or a few, whatever their rows, and a workbook
  past `PARSE_MAX_CHARS` is refused in the parse ("Large documents"). What one within it costs
  is its passages: written, indexed and embedded, 3 to 4 ms each in the database and the
  endpoint's time on top, a minute or two for one at the limit here, and longer with an
  embeddings server on a few CPUs.
- **Pages** are drawn two at a time by each API, about 0.3 s each for a page of this scan, so
  four asked at once wait about 0.6 s. From a bucket the API copies the original, here 62 MB,
  once for the pages that follow (`RENDER_CACHE_MB`, "Drawing pages"), so a page waits about
  as long as from a directory; when it copied the original for every page, a page took 2 s and
  a member scrolling through the scan fetched 300 copies. A page kept from before comes from
  the storage in 25 ms, from the bucket in about 50 (the redirect and the bucket's answer).
- **Search** is quick at 3,000 documents: the full-text and vector search under 100 ms eight at
  a time, and `ilike` over their titles, file names and summaries and 15,210 facts under
  50 ms. At 30,000 in one project a search takes 43 ms (111 ms at p95), where it took 222
  (304) while the keyword ranking ranked every passage that held any word of the question: it
  ranks those that hold every word first, then all but one, and the rest only while those are
  too few, so its cost is the passages that hold the question's words and grows with them. A
  question of one word that most passages hold is the slow case (about 130 ms for the ranking
  at 30,000). The search of every project ranks its projects' passages together, 42 ms here for
  the library and a small project. The vector ranking takes a few milliseconds. `ilike` grew to
  about 100 ms (145 ms at p95) over 151,193 facts and the documents' names and summaries.
  Trigram indexes (`pg_trgm`, GIN) took the facts route to 55 ms at p95 and `list_documents`
  to 40, not a few milliseconds, since a supplier's name matches thousands of facts, and cost
  33 MB and an insert of the 151,193 facts in 1.4 to 16 s rather than 0.14 (GIN's pending
  list), so there are none yet. Look again when a project nears 100,000 documents or the facts
  route's p95 passes 300 ms. At pgvector's
  default the vector index left out one in six of the 10 nearest passages; `hnsw.ef_search`
  200 leaves out one in fourteen for about 5 ms more. A small project is sorted by exact
  distance and loses nothing, and each search is planned for its project, without which a
  table of many projects sorted a large one whole.

## On call

Who hears what, and how soon someone acts. Each way to the operator ends at an address or a
pager the deployment names, and only the worker's mail depends on the machine in trouble, which
the rules and, on AWS, CloudWatch back up:

| From | What | To |
| --- | --- | --- |
| The worker | The jobs it gave up on, quarantines, spending at 80 % of a limit, its watch's alerts ("What the worker mails") | `ALERT_EMAIL`, through the app's mail relay |
| Prometheus's rules | `critical` alerts every hour while they last, `warning` ones every 4 hours, and each once more when it is over | Alertmanager's receiver; on AWS the alerts topic, and the pages topic for `critical` |
| The watchdog | Its silence | The dead man's switch; on AWS the alarm `emudoc-<environment>-alerts-silent`, on the alerts topic and the pages topic |
| The host, on AWS | A container in trouble, a failed drill or renewal of the credentials, the instance's status checks | The alerts topic; a container in trouble and the status checks the pages topic too |
| The account, on AWS | The trail's alarms (`root`, `iam`, `keys` and the others of "The account's records") | `account_alert_email` |
| The uptime service | `/api/health` missed from outside | Its own notifications, and its status page for the members ("The status page") |
| GitHub | A scheduled workflow that failed: Audit's scans, the Models workflow's requests | Whoever last changed the schedule |
| The members | A problem, a question or an idea from the app's help menu, with the page and the reference of the request that failed there; or by mail, quoting the error screen's reference ("Support") | `SUPPORT_EMAIL`, else the workspace's owners and admins |

How soon:

- **`critical`**, the uptime service and on AWS the pages topic (the instance's alarms and a
  container in trouble besides the rules): a member waits now, or a backup is losing changes. The person on call acts within 30 minutes, day or night, starting from "A
  first look" below, and the paging service calls a second when the first has not answered in
  15.
- **`warning`**, the worker's mails and the account's alarms: within the working day. An
  account alarm that nobody's own `tofu apply` explains may be someone else in the account, and
  is looked at the same day ("The account's records").
- **A member's report**: answered within a working day, with what was found.

On one machine, the Alertmanager of "Rules on the metrics" gives `critical` a receiver of its
own, a paging service's webhook or its `pagerduty_configs`, and the mail the rest. On AWS what
cannot wait goes to a second topic beside the alerts topic, `emudoc-<environment>-pages`:
Prometheus's `critical` alerts, a container in trouble (the host's watch) and CloudWatch's three
alarms, each when it begins and when it is over. Its messages are a few words, since an SMS
carries no subject: the environment, and each rule's summary or the containers in trouble; the
alerts topic's message says where to look. A failed unit (the drill, a renewal of the
credentials, a start) stays on the alerts topic: none has a member waiting yet, and what a
failed renewal or start leads to, Alertmanager's silence, pages. The module subscribes nobody
to the pages topic, since a paging service's address carries its key, which the repository, the
plan and the state would hold: subscribe the pager by hand, once per environment.

```bash
pages=$(tofu -chdir=deploy/aws/production output -raw pages)
# A paging service's SNS integration, which confirms the subscription itself:
aws sns subscribe --topic-arn "$pages" --protocol https --notification-endpoint '<its url>'
# Or a phone, by SMS (E.164, with its country code):
aws sns subscribe --topic-arn "$pages" --protocol sms --notification-endpoint '+<number>'
aws sns list-subscriptions-by-topic --topic-arn "$pages"   # none PendingConfirmation
```

SNS sends an SMS only to verified numbers until the account leaves SNS's SMS sandbox, and to
some countries (the United States among them) only from an origination number registered first;
a paging service's integration needs neither, and calls the second person when the first does
not answer, which an SMS cannot. Each page's subject names the rule or alarm, and an
Alertmanager page's ends "is over" when it is over, which the paging service's rules can close
the incident by. A page repeats every hour while its rule fires: a silence ("On AWS" under
"Alerts") holds the repeats back once someone is on it.

Every alert says where to look; the runbooks below say what to do for those that need more than
a sentence. Each incident that called someone gets a short record: when it began and ended, what
the members saw, the cause, and what changes so that it does not come back. The service levels'
figures for each 30 days go into the same record.

## Support

The question mark in the header is the help menu, where members write for help, read what's
new and find the status page. The versioned API's contract, which `GET /api/v1/openapi.json`
serves to anyone, without a key, with `APP_URL` as its server, is for whoever writes a program
against the deployment: a tool that writes a client from it calls the deployment. It changes
with a release, never by hand (the contract is `backend/app/api/v1/openapi.json`).

Members write for help from that menu: a problem, a question or an idea, in their own words.
Where `SUPPORT_EMAIL` names an address, a team's helpdesk, the app mails it there; otherwise
it mails each of the workspace's owners and admins but the member who wrote, a mail each, and
a sole owner or admin is told the message would come back to them. It goes from `MAIL_FROM`,
with a `Reply-To` of the member's address, so a reply from whoever reads it goes to them. The
subject is the app's own words and a reference, "A member reports a problem, reference
3f9c…", and the body their words with what its reader needs to find what they met:

- their name and address, whether the address is confirmed, their user id and role;
- the workspace's name, slug and id, and the member's role in it;
- the page they wrote from, as its path with the ids its query names (an open chat's, a
  folder's), never a search's words;
- the reference of the last request the page saw refused or failed, and its status;
- the version the API runs, the browser's description of itself, when it was sent, and the
  message's own reference, the id of the request that sent it.

A reference is a request's id, so the log finds the request it names, with the user, the
organisation and anything it logged ("Logs"): `jq 'select(.request_id == "3f9c…")'`, and the
same over `trace_id` for the job it queued. The message goes through the mail queue like the
rest of the members' mail ("Mail"), and the queue gives it up once it is sent, so the app keeps
nothing of it: no table, no audit row, no log line holds the member's words. The help menu
answers with the reference and where the message went; a member writes `RATE_FEEDBACK`
messages an hour (10), past which the menu says when to try again. Where no relay is set
("Mail"), the message would reach the log by its subject alone, so the menu sends none: it names
`SUPPORT_EMAIL`, or else the workspace's owners and admins, to write to from the member's own
mail, each a link to a mail that carries the page and the failed request's reference. Until 2026-11-13 a member could also let Emudoc's
support see their account from the operator's area; a team that runs its own install asks its
members what they see instead.

### The status page

A status page tells members whether Emudoc is up when Emudoc itself cannot, so it runs outside
the deployment, on another provider's servers: never on the machine, the database or the proxy
it reports on. `STATUS_PAGE_URL` names it, and the help menu and the page a member sees when
the app cannot reach its server link to it; `GET /api/help`,
which tells the page the address, reads no database, so it answers while the database is down
(when the API itself is down, nothing of the app loads, and members find the page by its
address). Three things fill it:

- **The uptime service** of "On call" and "Service levels", which already asks
  `https://<domain>/api/health` every minute from outside. Most of them (the hosted ones, or
  Uptime Kuma, Gatus or Upptime run elsewhere) publish their checks as a public status page;
  show the `/api/health` check, which answers 200 only while the API reaches its database, not
  `/api/livez`, which answers through a database outage.
- **The person on call**, who posts an incident when members are affected ("On call"): what they
  see, what is being done and when the next word is due, and closes it with the incident's
  record.
- **The alerts**, where the service takes them: a receiver of `critical` alerts with the
  service's own webhook (`webhook_configs`) beside the pages route in Alertmanager's
  configuration ("Rules on the metrics"; on AWS `deploy/aws/alertmanager.yml`), its address and
  key in the deployment's secrets. A rule's summary names the app's parts, never a member's
  data.

The service receives nothing of the members' data: the link opens its site in a tab of its own,
and the app's pages load nothing from it.

## The Administration page

The workspace's owners and admins read on its Administration page (`/admin`, from the account's
menu or the workspace page) what the maintenance commands print, in a browser and without a
shell on the machine: the job queue, the running jobs with their leases, the dead letters with
why each failed and the workers seen in the last day, with a warning when none runs (`jobs`,
`failed-jobs`); the models and services: the models each variable names, the providers and
which are switched off, the embeddings endpoint, the scanner, the storage, the database, and
everything the worker's watch sees wrong now, asked afresh, with since when it has been mailing
each ("What the worker mails"); and what the workspace's model use cost over 7, 30 or 90 days
against `DAILY_SPEND_CAP`, by kind of work and model (`costs`). The workspace page's members
list says when each member was last seen, to the owners and admins alone. What the page shows is
what the commands print, never what a document, a question or an answer says. The jobs and the
costs are the workspace's own, with the jobs of no organisation such as mail about an account;
the workers, the models and the services are the install's.

It changes two things, as the commands do, each a row of the workspace's audit log naming the
admin:

- **Failed documents queued again.** Under Jobs, "Run again" on a failed index job, or "Run
  every failed document again", queues each document from the stage it stopped at, as
  `failed-jobs --requeue` does, once the cause is fixed; the jobs of other kinds are left.
- **A model provider switched off.** Under Services, "Switch google off" stops every process of
  the install asking `google:` models within five seconds (`SWITCH_SECONDS`), API and worker
  alike, with no restart, and "Switch google on" lifts it: the app runs meanwhile as it does
  with `DISABLED_PROVIDERS` ("A fallback"), and the members' status and the watch say so. A
  provider `DISABLED_PROVIDERS` names stays off whatever the page says, since the setting is the
  install's own floor; a provider the page switched off whose models the settings no longer
  name is listed to switch on. The switches (`provider_switches`, with who and since when) are
  also a line of the log, `User 7 switched the model provider google off`. `maintenance models`
  asks every model whatever the page says, which is how to try a provider before switching it
  on.

The page is the members' app's own, held by role: a member or a viewer is refused its routes
(`/api/organization/jobs`, `/services`, `/costs`, `/providers/…`) with 403 and sees no link to
it. Two-factor sign-in for the admins is the workspace's choice, "Require two-factor sign-in"
on the workspace page, which holds everyone who works in it. Until 2026-11-13 this was the
operator's area, a page of its own at `/operator` for the accounts an `operators` command named,
from the addresses `OPERATOR_NETWORKS` listed: a team runs its own install, so its admins are
the people who run it. A proxy rule that refused `/operator` by address may go.

## Runbooks

What to do, step by step, for what goes wrong most often, pointing at the sections that say
more. `maintenance <command>` below is `python -m app.maintenance <command>` as the operator's
other commands run ("The image"): `docker compose -f deploy/compose.yml run --rm --no-deps
worker python -m app.maintenance …` on one machine, `sudo emudoc-compose run --rm --no-deps
worker python -m app.maintenance …` on the AWS instance, `uv run python -m app.maintenance …`
in a checkout's `backend/`; `compose` is `docker compose -f deploy/compose.yml` there and `sudo
emudoc-compose` on the instance.

### A first look

When an alert or a member says something is wrong, and it is not yet clear what:

1. `curl -sS https://<domain>/api/health` answers `{"status":"ok","version":"…"}` with the
   release's commit. A 502 is the proxy without an API (step 2); a 503's cause is in the API's
   log ("Not ready: the database cannot be reached", or `BUSY` for the pool).
2. `compose ps -a`: the API and the worker `healthy`, the release step exited with 0, ClamAV and
   the embeddings server running. A container that keeps starting says why in its last lines,
   `compose logs --tail 50 <service>`.
3. What the app logged at WARNING and above in the last hour: `compose logs --no-log-prefix
   --since 1h api worker | jq -R 'fromjson? // empty' | jq -c 'select(.level != "INFO")'`. A
   member's reference is a `request_id`: `select(.request_id == "…")` finds its lines, and their
   `trace_id` the jobs the request queued ("Traces").
4. `maintenance jobs` for the queue, the running jobs and the workers, `maintenance database`
   for the server, Prometheus's alerts page for what else fires ("On AWS" under "Alerts" for
   the way to it on the instance). The Administration page shows the queue, the dead letters,
   the workers and what the worker's watch sees in one place, from a browser ("The
   Administration page").

### Members get server errors

`EmudocErrorBudgetFastBurn` or `SlowBurn`, or a member who quotes a reference:

1. Which requests: `sum by (route, status) (increase(emudoc_http_requests_total{status=~"5.."}[1h]))`.
2. A 500 is the app's own fault: the error tracker groups them, and the API's log has each one's
   traceback under its request id. If the last release brought it, release the one before
   ("A release"), as long as no migration came between.
3. A 503 is named in the log, and its sentence to the member says the same:
   - `BUSY`, the pool: `EmudocPoolSaturated` too. Raise `DB_POOL_SIZE` and `DB_MAX_OVERFLOW`
     within the database's `max_connections`, or put a pooler in front ("A pooler").
   - A statement the database stopped ("… was stopped by the database"): the line names the
     route; `maintenance database` shows vacuum and the oldest transaction, and on AWS Database
     Insights the statements' load.
   - `STORAGE_DOWN`: the line names the bucket or directory, and the watch mails "the storage
     cannot be used". On AWS, `systemctl status emudoc-credentials`: the containers lose the
     bucket within the hour of a renewal that failed.
   - The database out of reach: readiness fails on every replica. A managed database's events
     say whether it is failing over, which takes a minute or so ("The database").

### Answers are slow, or do not begin

`EmudocAnswersLateFastBurn` or `SlowBurn`, `EmudocQuestionsWaiting`, the watch's "Emudoc
alert: questions wait for a worker", or a member's report:

1. A backlog: `maintenance jobs` shows `answer_question` jobs due and how long the oldest has
   waited, and `emudoc_worker_busy{lane="answerer"}` stands at `ANSWER_CONCURRENCY`. Raise it,
   with the pool (an answer holds a connection each time it writes), or run another worker
   (`compose up -d --scale worker=2`). No worker at all is `EmudocNoWorker`.
2. A slow model: `histogram_quantile(0.95, sum by (le, model)
   (rate(emudoc_model_first_event_seconds_bucket[15m])))`, against its provider's status page.
   A fallback that answers slower than its primary shows here while `EmudocModelFallingBack`
   fires.
3. One slow answer: its run's line ("Traces"; the first recipe there finds the slowest of the
   hour) splits its time into the wait, each model turn, each tool call and the writes, whose
   total is the database's share.
4. One organisation asking a lot holds only its own: each has at most `MAX_ANSWERS` in flight,
   and the claim shares the threads between organisations.

### An upload is stuck, or slow to be ready

`EmudocDocumentsLateFastBurn` or `SlowBurn`, `EmudocDocumentsWaiting`, or a member whose
document "stays processing". Its id is in the library's address and in every line the log has
about it.

1. `maintenance jobs --document <id>` says where it stands, each stage with what its members
   read, its index jobs, and what the newest one means:
   - due, and `maintenance jobs` ends with "No worker has been seen running…" (the members'
     header says "Background work off"): no worker takes the queue's work. `compose ps -a`
     says whether the worker's container runs; started again (`compose up -d worker`), it takes
     the queue as it stands, the answers asked meanwhile among it;
   - due and waiting for a free indexer thread: a backlog, which more workers or
     `WORKER_CONCURRENCY` shorten ("Load");
   - waiting until a moment: a retry's backoff after a provider or a service failed, or the
     day's spending cap until midnight UTC, as the stage's detail says;
   - running under a worker's lease, which its run's line in that worker's log explains once it
     ends ("Traces");
   - a lease that ran out: the worker stopped or lost the database, and the next free thread
     takes the job over from the stage it stopped at, with nothing to do;
   - given up: `maintenance failed-jobs` says why. Fix the cause (a key, a model, the scanner),
     then `failed-jobs --requeue`, or let the member press "Index again".
2. A run that holds its lease and never ends: a parse ends at `PARSE_TIMEOUT` and a model's
   request at its own limit, so a stage that outlasts them is a fault. Its last lines are in the
   worker's log under its `document_id`; `compose restart worker` hands its jobs back.
3. Slow for everyone: `sum by (stage) (rate(emudoc_stage_seconds_sum[1h])) / sum by (stage)
   (rate(emudoc_stage_seconds_count[1h]))` says which stage grew. Each scanned page is a model
   request; embedding on the instance's CPUs is the slow part of a long document, for which a
   larger instance or a hosted endpoint is the cure ("The embeddings endpoint"). A parse stage
   that grew while `emudoc_parses{state="waiting"}` stays above zero is the indexers queuing
   for the `PARSE_CONCURRENCY` parses (each run's `parse` span says how long it `waited`):
   raise it if the worker's memory has room for as many more children, or add a worker.

### A model provider is down

`EmudocModelsDown`, `EmudocModelErrors`, `EmudocModelFallingBack`, the watch's "AGENT_MODEL
fails over to its fallback" (or `INDEX_MODEL`), or the provider's status page:

1. With a fallback of another provider there is nothing to do at once. The app asks the
   fallback first for a minute after each failure, the members notice nothing, and what it
   answers is priced under its own name. `EmudocModelsDown` fires only if the fallback fails
   too.
2. Without one, or with both down, the members read "The AI model is unavailable just now":
   uploads are still parsed, outlined and searched by their words, their summaries and facts
   wait for their retries, and questions fail. Name a fallback of another provider (its
   variable, its key, its price), run `maintenance models` and release the same tag again
   ("The models").
3. A provider that bills while it fails, or a key that leaked: switch it off under the
   Administration page's Services, which every process heeds within seconds, after which the
   fallback answers, or the app runs as it does without a key, until it is switched on; for
   good, `DISABLED_PROVIDERS=<prefix>` and a release.
4. Once it is over, the stages that gave up meanwhile are dead letters: `maintenance failed-jobs
   --requeue`.

### Switching models

A new model is a release of its own, tried as a fallback first:

1. It must keep the app's promises: terms that forbid training on what it is sent, billing
   on and `GOOGLE_PAID_TIER` for a `google:` one ("What leaves the deployment"), and a version
   in its name, not an alias.
2. Name it as the fallback of its kind of work, with its key where the secrets are and its
   price in `MODEL_PRICES` (production refuses `DAILY_SPEND_CAP` with a model it cannot price),
   and release.
3. `maintenance models`: every model answers as the app asks, the new one among them.
4. Make it the primary, with the old one or another provider's model as its fallback, and
   release. The Models workflow's repository variables follow ("Every morning, from GitHub").
5. Over the next days, `maintenance costs` by model and the answers' service level say what it
   costs and how soon it begins.

A retirement the provider announced is the same steps, before its date ("Retirements").

### Changing the embedding model or its size

Every passage is embedded again, which is the expensive part:

1. What it costs. `maintenance database` gives the rows of `passages`. A hosted endpoint is paid
   per token: the last full embedding's tokens are the `embed` rows of `maintenance costs` (or
   `usage <slug>`) since it, at the new model's price. On two CPUs llama.cpp takes 29 to 47
   seconds per 32 passages with EmbeddingGemma, by their length ("The embeddings endpoint"), so
   400,000 passages keep one server busy for four to seven days; a GPU server or a larger
   instance for the backlog shortens it. Until a document's turn, a search by meaning ranks it
   poorly, and its words still find it.
2. A new `EMBEDDING_MODEL` of the same size (with its prefixes, and for the server beside the app
   `EMBEDDING_WEIGHTS_URL` and `EMBEDDING_WEIGHTS_SHA256`): set it and release, and the start
   queues every document's embed stage at bulk priority, behind the members' uploads. A new
   `EMBEDDING_DIM`: stop the API and the workers, `maintenance rebuild-embeddings`, then release
   ("The embeddings endpoint").
3. The backlog is expected: silence `EmudocDocumentsWaiting` for its length ("On AWS" under
   "Alerts"); the watch mails "Emudoc alert: documents wait for a worker" once, and again when
   it is over. The uploads' service level stays as it was, since a document is measured once.
4. When it is done, rebuild the vector index and vacuum `passages` ("Vacuum").

### Rotating a key

Make the new key, put it where the settings are kept, release the same tag, check that it works,
then revoke the old one. A key that leaked is revoked first, and the app runs on the fallback, or
without, meanwhile. On AWS a key is an SSM SecureString under `/emudoc/<environment>/secrets/`,
put with the `read -rs` line of "The settings", and reaches the containers at the next release
of the same tag (the Release workflow).

| Key | The new one | Then |
| --- | --- | --- |
| A model provider's (`GOOGLE_API_KEY`, `ANTHROPIC_API_KEY`, …) | The provider's console | `maintenance models`; for a leak, the provider switched off on the Administration page first |
| `EMBEDDING_API_KEY` | The endpoint's provider | The start's line "Passages are embedded by …", then a search by meaning |
| The database's password | `ALTER ROLE emudoc PASSWORD '…'` and the new `DATABASE_URL`, with `DATABASE_LISTEN_URL` and a pooler's `userlist.txt`; on AWS `tofu apply -replace=module.emudoc.random_password.database`, which writes the new URL | Release at once: the processes keep the connections they hold, and new ones fail until then; `/api/health` |
| `SMTP_URL`'s password | The relay; on AWS `tofu apply -replace='module.emudoc.aws_iam_access_key.smtp[0]'` | A password reset's mail |
| `GOOGLE_OAUTH_CLIENT_SECRET`, `MICROSOFT_OAUTH_CLIENT_SECRET` | The provider's console, which holds two secrets at once | A sign-in with each, then the old secret deleted there |
| `GOOGLE_DRIVE_CLIENT_SECRET`, `DROPBOX_APP_SECRET`, `ONEDRIVE_CLIENT_SECRET` | The service's console | "Sync now" on a connected folder of each, then the old secret deleted there |
| `SENTRY_DSN` | A new client key in the tracker's project | An error reaches the tracker, then the old key disabled |
| `ENCRYPTION_KEYS` | `python -m app.keyring`, put first, the old key after it | `encrypt-secrets`; the old key removed after `BACKUP_DAYS` ("The encryption keys") |
| The bucket's (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`) | The bucket's provider; on AWS there are none, the host renews a role's session every quarter of an hour | The start's line "The bucket keeps …" |
| The Models workflow's | The provider, with a low spending limit | `gh secret set`, then a run by hand |
| pgBackRest's `repo1-cipher-pass` | Not for a repository that holds backups: a new repository under the new passphrase, the old one kept with its own until its backups pass `BACKUP_DAYS` | `pgbackrest --stanza=emudoc info` |

The KMS keys of the AWS roots rotate by themselves, every year.

### Restoring a backup

When the database or the files are lost or damaged, or a mistake must be undone:

1. Choose the moment, in UTC: the last good one, from the log or the audit rows. The targets
   are five minutes of the database and four hours to members working again ("Recovery
   targets").
2. Tell the members the app is down for a restore: the app cannot, being stopped.
3. Follow "Restoring": stop the app, restore the database, run the migrations if the code is
   newer, `after-restore --dry-run` and then `after-restore` with the moment, and start it. With
   the compose file's nightly backups, the moment is the last backup's, and "With the compose
   file" restores it.
4. Tell the members what is undone since the moment, and that everyone signs in again.
5. Write each step's time down beside the drill's, against the recovery time.

The monthly drill is this runbook rehearsed ("The drill").

### Draining the workers before a release

A release stops the worker as any stop does: it claims no more, gives its running jobs 8
seconds (`--grace`) and hands the rest back, which the new worker takes at once. An index job
goes on from the stage it was in; an answer starts over, which its member sees begin again and
which costs its model calls again. So nothing needs draining, but a release can choose its
moment:

1. `maintenance jobs`: the running jobs, and `emudoc_answers_in_flight` the answers being
   written.
2. To let the running answers end rather than start over, release when none runs, or give the
   worker longer to stop: `--grace 120` in the worker's command and a `stop_grace_period` above
   it in the compose file, at the price of a release that waits that long.
3. A migration that the running code cannot work beside, such as one that changes the jobs
   table or what a stage writes: `compose stop worker` first, then the release, so that no old
   worker writes into the new schema.
4. For planned work that sets off rules, a silence for its length ("On AWS" under "Alerts").

### The models are spending fast

The spending warning ("Emudoc has spent 80 % of …"), `EmudocSpendNearCap`, or a provider's own
budget alert:

1. `maintenance costs --days 1`: what today's work cost, by kind of work and model;
   `maintenance usage <slug>` breaks the workspace's down by day, kind and unit.
2. `DAILY_SPEND_CAP` holds the workspace at the day's cap (questions refused, indexing held
   until midnight UTC); raised in the settings, it takes effect at the next start.
3. A provider switched off on the Administration page (or `DISABLED_PROVIDERS`) stops its bill
   at once.
4. A model without a price counts toward none of it: the end of `costs` lists the tokens recorded
   without one, and `MODEL_PRICES` takes the price from the next request on.

## Rates

The app holds each caller to a rate of its own (the `RATE_*` settings in `.env.example`): so
many upload requests, questions and searches a minute per user, and downloads of a facts table,
a draft, an answer or a chat (`RATE_EXPORTS`, 10), each of which reads every row the table keeps
or lays the text out whole, and on the sign-in routes so many requests per email address and, with more room, per
client address, the one uvicorn read from `X-Forwarded-For`. The request past a rate is refused with 429, a sentence and a
`Retry-After` of the seconds until the minute turns, and the counts live in the database, so
every API replica counts the same. The proxy needs no rate limit of its own. One added anyway
(nginx's `limit_req`, Caddy's rate-limit plugin) sees addresses, not users, so it must allow
more than `RATE_SIGN_INS_PER_ADDRESS` a minute, or an office behind one address is held
before the app would hold it, and more than `RATE_API_REQUESTS`: a request made with an API
key (README, "The API") is counted for its key as well, every request it makes, 300 a minute
unless set, on top of the rates of the member who made the key, which its uploads, questions
and searches count against as their own.

A minute's rate still lets a patient guesser try thousands of passwords a day at one account,
so the wrong passwords and authenticator codes are also counted over the hour
(`RATE_FAILED_SIGN_INS`, 20). Past it, the sign-ins they came from pause until the hour
ends, the right password included, with a 429 that says for how many minutes. A pause must
not be a way to keep someone out, and anyone who knows an address can fail its sign-ins, so
the browsers that signed in to the account before are counted apart, each by itself, on the
minute's rate as on the hour's: they keep a cookie of their own, `emudoc_browser` (path
`/api/auth`, 90 days after their last sign-in, kept after signing out), and the account a row
for each (`browsers`, the cookie's hash with the user's id). A stranger guessing pauses only the
browsers the account never signed in with, which share the address's count whether or not it
has an account; the owner is mailed once when that happens, the audit log records
`sign_in_paused`, and the owner signs in from a browser of theirs as before, or from a new one
after a password reset, which ends the pause and leaves only the browser that set the new
password told apart (a change on the account page does the same). A signed-in request that
confirms a change with the password (a new password, an address, turning two-factor off,
deleting the account) is counted for its user instead. The 429 says nothing that differs
between an address with an account and one without, and the mail goes out after it.

A share link (README, "Accounts") opens with no account, so its openings are counted per
client address (`RATE_SHARED_LINKS`, 30 a minute), unknown tokens among them, and the wrong
passwords of a link over the hour, for the link from every address together and for each
address (`RATE_SHARED_PASSWORDS`, 10): past it the link opens with no password, the right one
included, until the hour ends, with a 429 that says for how many minutes. Each opening, and
each download of the file through it, is a row of the organisation's audit log with the
address it came from.

New passwords, at sign-up, from a reset link or on the account page, are refused when they
have appeared in a data breach. The check asks `PWNED_PASSWORDS_URL`, Have I Been Pwned's
range API by default, with the first five characters of the password's SHA-1, which hundreds of
passwords share, and finds the rest in the answer; it asks for padding, so the answer's size
tells nothing either. A copy of the list served on the deployment's own network in the same form (the
downloader Have I Been Pwned publishes, behind any static server answering `/range/<prefix>`)
sends nothing out; production refuses one reached over plain HTTP off that network. A service
that does not answer within 5 seconds lets the password through and logs "The
breached-password check at … failed"; nothing logs the five characters, which with a copy of
the database would let a guesser skip most candidates at SHA-1's speed. Empty checks nothing.

## On AWS

`deploy/aws` is this guide on AWS as OpenTofu code (1.12 or later): an environment is a network
of its own, Postgres on RDS, the documents' bucket, and one EC2 instance that runs
`deploy/compose.yml` from the image with Caddy in front. Staging and production are the same
module twice, with their own sizes, database, bucket, keys and spending cap, and run the same
image: every release runs on staging first.

```text
                       ┌─ EC2, Amazon Linux 2023 ───────────────────────┐
  browsers ─ 80, 443 ─▶│ Caddy ─▶ API ─┐   worker   ClamAV   llama.cpp   │
                       └───────────────┼────────────────────────────────┘
                                       ├─▶ RDS Postgres 17 (private subnets, TLS checked)
                                       └─▶ S3 bucket (versioned, encrypted)
  SSM /emudoc/<environment>/settings, /secrets, /release ─▶ each release on the instance
  SNS alerts ◀─ the host's watch, Prometheus's rules by Alertmanager, CloudWatch's alarms
  SNS pages  ◀─ the same, what cannot wait alone (critical rules, containers, the alarms)
```

The directories:

- `account/`, once per AWS account: the bucket the environments' state is kept in
  (`emudoc-state-<account>`), the KMS key OpenTofu encrypts that state with before it leaves the
  operator's machine (`alias/emudoc-state`; the state holds the database's password and SES's
  SMTP key), the image repository, `emudoc` in ECR, whose tags are immutable and scanned as
  they are pushed, and GitHub's sign-in with the role CI pushes with, the one role the
  repository takes images from ("Releases from CI"), and the account's trail, CloudTrail in
  every region into a bucket of its own, with alarms on its events ("The account's records").
  Its own state holds no
  secret and stays where it is applied; its tests are `tofu test` there.
- `modules/environment/`, an environment, with its tests (`tofu test` there, against a mocked
  AWS: what each resource is made with, and the inputs it refuses).
- `shared/`, the settings both environments take: the models and their prices, `BACKUP_DAYS` and
  how long the logs are kept, and the GitHub repository CI runs in, which the account root
  reads with the address its alarms go to. Staging names the
  models production does, which is what pinning them is for.
- `staging/` and `production/`, the two roots. They differ in the domain, the sizes (a
  `t3a.large` and a `db.t4g.small` without a standby against a `t3a.xlarge` and a
  `db.t4g.medium` with one), deletion protection and the spending cap.
- `push-image.sh` and `release.sh`, which CI runs; `compose.yml` and `host/`, which the image
  carries and each release runs on the instance: `compose.yml` over `deploy/compose.yml`, its
  Postgres left out for RDS and its Caddy for the environment's domain, and `host/compose`
  turning on the profiles of ClamAV, the converter and Caddy (a change of 2026-11-13 that no
  environment has released yet).

### What an environment is made of

- **The network**: a VPC (`network_cidr`, a /16), one public subnet for the instance and two
  private ones for the database, which have no route out. The internet reaches the instance on
  80 and 443 (UDP too, for HTTP/3) and nothing else: there is no SSH, and a shell is Systems
  Manager's (`aws ssm start-session --target <instance>`). The database takes connections from
  the instance alone.
- **The database**: RDS Postgres 17, whose builds bring pgvector 0.8 from 17.1, encrypted, on no
  public address, with `rds.force_ssl` on. The app connects as the master user, which may create
  the extension the first migration asks for. "The role's timeouts" above are the parameter
  group's here, for the whole server, which is the app's alone: the app reads them as its
  connection sees them, and its long work lifts them for its own transaction as before. RDS
  keeps point-in-time backups for `backup_days`, at most its 35, which is `BACKUP_DAYS`. With
  deletion protection (production), deleting the database leaves a final snapshot, which outlives
  `BACKUP_DAYS` until it is deleted by hand: do so once it is not needed, since a deleted
  account is meant to be gone from the backups within those days. `DATABASE_URL` asks for `verify-full`
  against RDS's own authority, which no public bundle holds: each release fetches the region's
  bundle from `truststore.pki.rds.amazonaws.com` into `/etc/emudoc/rds-ca.pem`, which the
  containers mount, and keeps the copy it has when the trust store cannot be reached. Its own log
  keeps an error's first line and statement alone (`log_error_verbosity = terse`) for a day
  short of `server_log_days`, 7 days at most ("How long they are kept").
- **The bucket**: versioned, with one rule over every key that removes an earlier version two
  days before `BACKUP_DAYS` run out ("The files", above), encrypted (SSE-S3), never public, and
  refusing any request without TLS.
- **The instance**: Amazon Linux 2023 on x86_64 (the image is built and checked for it) with
  Docker, an encrypted disk and an Elastic IP the domain leads to. Its first start
  (`cloud-config.yaml`) installs Docker, Compose 5.5.1 (checked against its SHA-256) and ECR's
  credentials helper, and runs the recorded release, if there is one. It holds nothing that is
  not made again: the documents are in the bucket, the rows in RDS, the settings in SSM, so a
  change to the boot replaces it, and so does a new Amazon Linux, which the instance does not
  take by itself (`dnf` stays at the release the image was made from). Replace it every month,
  beside the drill, with `tofu apply -replace=module.emudoc.aws_instance.app`: a few minutes
  of downtime, a new certificate from Let's Encrypt, and the embedding model's weights
  downloaded again, the same file by its SHA-256. Every container logs to its journal, which
  deletes the lines past `server_log_days`, the host's own units' with them ("How long they are
  kept"); a replaced instance takes its logs with it.
- **Credentials**: the instance metadata service answers the host alone (IMDSv2, one hop), so
  no container holds the instance's role, which reads the settings, pulls the image and runs
  the drill. ClamAV, which opens hostile files, and llama.cpp's server hold no credentials at
  all. The API
  and the worker reach the bucket with the app role's, which allow the bucket and nothing else:
  the host renews an hour's session of it every quarter of an hour into
  `/run/emudoc/aws/app` (`emudoc-credentials.timer`), and boto3 reads the file again whenever
  the session it holds nears its end. The drill's dry run gets the storage reader's, which
  can only list the bucket.
- **Mail**, when the root names a `mail` domain: SES with the domain verified by DKIM, its
  envelope from `bounce.<domain>` for SPF, DMARC's record, and SMTP credentials that may send
  from it and do nothing else, written into `SMTP_URL`; a configuration set every message of the
  domain goes under, which sends only over TLS, keeps the account's list of addresses not to
  send to, and publishes the bounces and complaints to EventBridge, whose rule puts this
  environment's into an SQS queue the worker reads, `MAIL_EVENTS_QUEUE` ("Mail"). A new SES
  account sends only to verified addresses until AWS grants it production access, which is
  asked for by hand in SES's console. Without `mail`, `SMTP_URL` and `MAIL_FROM` go into the
  secrets path for a relay of the deployment's own.
- **Mail in**, when the root names an `inbound` domain ("Mail in"; both roots do, `in.<domain>`):
  the domain's SES identity, verified by DKIM, and its MX at SES's receiving endpoint; a rule of
  the environment's (`emudoc-<env>`) in the account's active receipt rule set, which the account
  root makes since SES keeps one active a region (apply the account root first), for the domain
  alone, writing each message into the bucket `emudoc-<env>-inbound-<account>` (under
  `inbound/`, private, SSE-S3, over TLS only, its objects removed after 14 days) and telling the
  topic `emudoc-<env>-inbound` (under a key of its own, `alias/emudoc-<env>-inbound`, which SES
  may use), that SES alone may publish to and for this rule alone, whose notices reach the
  SSE-SQS queue `emudoc-<env>-inbound` (kept 14 days) without SNS's envelope; the app role may
  receive and delete the queue's notices and get and delete the bucket's messages, and nothing
  else of them; the module writes `INBOUND_DOMAIN` and `INBOUND_MAIL_QUEUE`.
- **DNS**: with `dns_zone_id`, the domain's record, the mail's DKIM, SPF and DMARC records and
  the inbound domain's MX and DKIM are made in Route 53; otherwise `tofu output dns_records`
  lists them for the DNS kept elsewhere. Caddy asks Let's Encrypt for the certificate once the name leads to the instance.
- **Alerts**: an SNS topic, under a key of the environment's own, that the instance reports a
  failed drill, a failed credentials renewal (the containers lose the bucket within the hour),
  a failed start or a container in trouble to (`emudoc-watch.timer`), that Alertmanager on the
  instance sends what Prometheus's rules fire to, and that CloudWatch's alarms on the alerts'
  heartbeat and the instance's status checks publish to ("Alerts", "On AWS"); it names units,
  rules and the instance, never a log line. `alert_email` subscribes an address, which must
  confirm it, and is the app's `ALERT_EMAIL` too, where the worker mails what it gave up on and
  what its watch sees, through the mail relay. A second topic, the pages, hears what cannot
  wait (the critical rules, a container in trouble, the alarms) for a pager the operator
  subscribes by hand ("On call").
- **Records**: the documents bucket's access logs and the network's flow logs in a logs
  bucket of the environment's own, kept `access_log_days`, and Database Insights on the
  database ("The account's records").

### The account's records

Three records of what reaches the deployment, beside the app's own log ("Logs"):

- **The trail** (`account/trail.tf`): CloudTrail's record of every call to AWS's APIs in
  every region, the console's included, with who made it and from which address, and of every
  request for an object of the state bucket, whose states hold the database's password. Its
  files go into a bucket of their own (`emudoc-trail-<account>`, the root's `trail_bucket`
  output), encrypted with a key of their own (`alias/emudoc-trail`) and signed: `aws cloudtrail
  validate-logs --trail-arn <arn> --start-time <time>` says whether any was changed or removed
  since. They are kept `trail_days` (365, the account root's variable). The first copy of the
  management events costs nothing, and the state bucket's data events are a handful per `tofu
  plan`. The console's event history shows the last 90 days of management events without the
  files; Athena queries the files. The trail holds what the operators, CI and the instances
  did, and nothing of a member's, whose requests reach no API it records. It goes to CloudWatch
  Logs as well, for the alarms on it (below).
- **The access logs and the flow logs** (`modules/environment/logs.tf`), in each environment's
  logs bucket (`emudoc-<environment>-logs-<account>`, the `logs` output): S3's server access
  logs of the documents bucket under `access/`, grouped by the day of the request, a line per
  request with the requester's address and browser, the key, the operation and the answer; and
  the VPC's flow logs under `flow/`, a line per connection to or from its subnets, accepted or
  refused, with the addresses, ports and bytes. The page images, thumbnails and downloads a
  member opens are fetched by their browser from the bucket by a signed address, so the access
  logs hold members' addresses and browsers, and the signed address's query, which names a
  download's file and carries its signature for its 15 minutes; the flow logs hold the address
  of everyone who reached the instance. Both are personal data, kept `access_log_days` (30, in
  `shared/`). The bucket's rule removes them two days before, as the documents
  bucket's does for `BACKUP_DAYS`, and the bucket keeps no versions, so a removed log is gone.
  S3 delivers access logs within a few hours, as best it can, and charges only for their
  storage, where CloudTrail's data events for the documents bucket would cost per request,
  every page a member opens among them. No role of the app's reaches the bucket.
- **Database Insights**, in its standard mode on the RDS instance: the database's load by
  wait, statement and host over the last 7 days, without charge, in CloudWatch's Database
  Insights. A statement is shown as the app sent it, with `$1` for every value, since the app
  binds them all, and the one host is the instance. `python -m app.maintenance database`
  remains the check of the server's settings and vacuum ("The database").

Without AWS, the host's own records of connections (a firewall's log, a provider's flow logs, a
bucket's access logs) hold members' addresses too, and are the team's to keep for a period.

The account root watches its trail (`account/alarms.tf`), after the monitoring section of CIS's
AWS Foundations Benchmark: CloudTrail sends every event to the log group `emudoc-trail` too
(kept `trail_log_days`, 30, and encrypted with the trail's key, which CloudWatch Logs may use
for that group alone), with a role that may write the account's streams there and nothing
else, and a metric filter per kind of event counts it into an alarm that publishes to the
account's own topic, `emudoc-account-alarms` (the `alarms` output; under a key of its own that
CloudWatch may use; `shared/`'s `account_alert_email` subscribes an address):

| Alarm (`emudoc-account-…`) | Sets off at |
| --- | --- |
| `root` | Any call of the root user's |
| `console-without-mfa` | An IAM user's sign-in to the console without a second factor |
| `console-failures` | 3 failed sign-ins to the console in 5 minutes |
| `refused` | 10 calls AWS refused for want of permission in 5 minutes |
| `iam` | A change to a policy, a role's trust, a user, a login profile or an access key |
| `trail` | A trail made, changed, stopped or deleted |
| `keys` | A key turned off, set to be deleted, or given another policy |
| `buckets` | A bucket's policy, ACL, public access, CORS, versioning, encryption, lifecycle or replication changed |
| `security-groups`, `network` | A security group, network ACL, gateway, route, VPC or peering made, changed or deleted |

Each counts events, not a state, so it says nothing when it is over. A change the operators made
themselves sets off its alarm too, which is the point: a `tofu apply` that changes a policy is
followed by an `iam` message within minutes, and one nobody applied is the one to look into.
The message names the kind of event; who made it, from where and what it was are in the
console's event history, or in CloudWatch Logs Insights over `emudoc-trail` with the alarm's
metric filter as the query. CloudWatch Logs charges for what it takes in, a few cents a month
for an account of this size, and each alarm ten cents.

### The settings

The app's settings are SSM parameters under `/emudoc/<environment>/`: what the module knows from
the infrastructure and the roots' `settings` under `settings/` (`DATABASE_URL` and `SMTP_URL`
as SecureStrings), and the secrets the module cannot know under `secrets/`, put there by hand so
that no key passes through a plan or the state:

```bash
read -rs key && printf '%s' "$key" | aws ssm put-parameter --type SecureString \
  --name /emudoc/staging/secrets/ANTHROPIC_API_KEY --value file:///dev/stdin
```

Each release writes them into the env files (`deploy/aws/host/settings`): a name set in both
places, or a value with a line break, stops it. A fresh checkout's plan refuses `AGENT_MODEL`
and `INDEX_MODEL` left empty in `shared/main.tf`, which production cannot start without; an empty value there is a setting left unset. Keys never go
into the roots: the plan refuses a name that ends like one, and `SENTRY_DSN`, which carries the
tracker's key. The module writes `SENTRY_ENVIRONMENT`, the environment's name, itself,
`ALERT_EMAIL` from `alert_email`, and `SERVER_LOG_DAYS` from `server_log_days`, by which the
release sets the journal's period too ("How long they are kept"). A changed setting reaches
the containers with the next release (the same tag again does).

### Setting it up

1. Once per account: fill in `shared/main.tf`'s `account_alert_email`, then
   `tofu -chdir=deploy/aws/account init` and `apply`, with credentials of the account's
   administrator, and confirm the subscription. Keeping staging and production in accounts of their own isolates
   them further; the module is the same, and the account root runs in each, but CI pushes to
   one repository and the second account's instance would need leave to pull from it, which
   the module does not give.
2. Fill in `shared/main.tf` and each root's domain, zone, `mail` and `alert_email`, then
   `tofu -chdir=deploy/aws/staging init -backend-config=bucket=emudoc-state-<account>` and
   `apply`.
3. Put the keys into the secrets path as above: `ENCRYPTION_KEYS` (`openssl rand -base64 32`,
   kept apart from the database's backups too, "The encryption keys"), each model's provider
   and each fallback's, the OAuth clients' secrets, and `SMTP_URL` without `mail`.
4. Make the DNS records if the zone is not in Route 53, ask SES for production access, and
   confirm the alerts' subscription. Its first message is the alarm
   `emudoc-<environment>-alerts-silent`, which stays in alarm until the first release starts
   Alertmanager, and is over a few minutes after it. Subscribe the pager to the pages topic
   ("On call"); it hears the same alarm.
5. Give CI the roles (the repository variables of "Releases from CI"), then run CI on `main`
   (Actions, CI, Run workflow, or the next push): it pushes the image and releases it on
   staging.
6. Check staging as "Checking it" says, and drill a restore by hand on it ("The drill").
7. The same for production, with the Release workflow and the commit staging ran.

### A release

`release.sh` has Systems Manager run `emudoc-release TAG` on the environment's instance, waits,
prints what it said, records the tag in `/emudoc/<environment>/release` and waits a minute at
most for `/api/health` over HTTPS to name the tag: a domain that leads elsewhere, or an API
that kept its old version, fails it. Any environment but staging takes only a tag staging's
release history holds (SSM keeps its last 100 releases), so every release runs on staging
first. On the instance, `emudoc-release` pulls the image, takes out the
`deploy/` directory it carries (`/opt/emudoc/releases/<tag>`), and runs its
`aws/host/release`, so that what a release does on the host comes with the image and runs on
staging before production. That writes the settings into `/etc/emudoc/next`, fetches RDS's
authority, renews the containers' credentials, runs the migrations and the embedding model's
pull each in a container of their own, then `up --wait` with `deploy/aws/compose.yml` over
`deploy/compose.yml`; once that succeeded it moves the settings into `/etc/emudoc`, points
`/opt/emudoc/current` at the release, and keeps the three releases before it. A migration that
fails ends the release with the one before it still serving and still recorded. The API is
away for a few seconds of each release, the time it takes to stop and start, during which the
page follows answers again as on any one machine.

Going back is releasing an earlier tag (the Release workflow; `release.sh` prints the one it
replaced), as long as no migration came between: an image refuses a database whose schema is
newer than its own.

On the instance, `sudo emudoc-compose` is Compose with the running release's files and
settings: `ps`, `logs --since 1h worker`, `run --rm --no-deps worker python -m
app.maintenance models`. `/etc/emudoc` holds `host.env` (what the module told the instance),
the env files and RDS's authority; the units are `emudoc-boot`, `emudoc-credentials` and its
timer, `emudoc-restore-drill` and its timer, `emudoc-watch` and its timer, and
`emudoc-alert@`. A release that needs what an older instance was not given (`host.env` is
written at the first start alone) says which and stops before it changes anything: apply the
root, which replaces the instance, then release again.

### Releases from CI

What an environment runs was built by CI from a commit on `main`, after every check passed;
no one releases from their own machine, and no AWS key is kept in GitHub.

```text
  push to main ─▶ CI ─┬─ Backend ×6, Frontend, End to end, Load test, … ───────┐
                      └─ Image: build, smoke test, scan, push to ECR <sha> ─────┴─▶ Staging:
                                                               release.sh staging <sha>
  by hand ─▶ Release: production <sha> ─▶ did staging run it? ─▶ release.sh production <sha>
```

- **The image** is built once, by CI's Image job, from the commit and named by its full SHA
  (`--build-arg VERSION`, which `/api/health` reports). On a push to `main` the job smoke-tests
  it (`check-image.sh`), scans it as the Audit workflow does (a high or critical
  vulnerability with a fix stops the release there, and Audit's morning run reports the same),
  and pushes it with `push-image.sh`, unless the repository holds the tag already.
- **Staging** is released by CI's Staging job once every job of the run passed, with
  `release.sh staging <sha>`, one release at a time.
- **Where the jobs run**: GitHub's runners, or while the repository variable `RUNNER` names
  them, the self-hosted runners of `ci-runner` on a developer's machine (CLAUDE.md, "CI
  on this machine"). Either way the job is CI's, for a commit on `main`, and AWS trusts its
  OpenID Connect token for an hour; the machine keeps no key between jobs. The Release
  workflow runs on GitHub's alone.
- **Production**, and staging again (a changed setting reaches the containers with a release
  of the same tag), are the Release workflow's: Actions, Release, Run workflow, from `main`,
  with the environment and the commit, its seven-digit SHA as GitHub shows it or the whole.
  A release to production then waits for the reviewer of the GitHub environment `production`
  (the run's "Review deployments", then "Approve and deploy"; "The repository's rules",
  below). `release.sh` refuses a commit staging never ran. Going back is the same with an
  earlier commit.
- **Credentials**: each job that reaches AWS asks GitHub for a token naming the repository and
  the branch, which AWS trades for an hour of a role through GitHub's OpenID Connect provider
  (the account root makes it). The push role (`emudoc-github-push`, account root) may push to
  the repository and nothing else; each environment's release role
  (`emudoc-<environment>-github-release`, `modules/environment/release.tf`) may find its
  instance, have Systems Manager run the release there, follow it, record the tag and read
  `APP_URL`, and production's also reads staging's release history. Staging's trusts a job of
  a workflow on `main` of `github_repository` (`shared/`) alone, and production's a job of the
  GitHub environment `production` alone (`github_environment` in its root), whose token names
  the environment instead of the branch: a pull request's token, another branch's, or a job of
  `main` that skipped the environment's review, is refused.
- **The repository takes images from CI alone**: its policy denies the pushes to every
  principal but the push role, the account's administrators included, so an image built on
  someone's machine cannot reach an environment. While GitHub cannot run the workflows, an
  operator names their own role in the account root's `pushers` (`tofu apply -var
  'pushers=["arn:aws:iam::<account>:role/<role>"]'`), pushes and releases with the two scripts
  from a clean checkout of the commit, and applies the root again without it afterwards.
- `tofu apply` stays the operator's: it needs rights over IAM, KMS and the state, which no
  workflow should hold while anyone who can merge into `main` controls what the workflows run.

The repository variables, from the roots' outputs once they are applied (Settings, Secrets and
variables, Actions, Variables, or `gh`):

```bash
gh variable set AWS_PUSH_ROLE --body "$(tofu -chdir=deploy/aws/account output -raw push_role)"
gh variable set AWS_STAGING_ROLE --body "$(tofu -chdir=deploy/aws/staging output -raw release_role)"
gh variable set AWS_PRODUCTION_ROLE --body "$(tofu -chdir=deploy/aws/production output -raw release_role)"
gh variable set AWS_REGION --body eu-central-1      # the roots' region, which is also the default
```

Without `AWS_PUSH_ROLE` CI checks the image and pushes nothing, and without it or
`AWS_STAGING_ROLE` the Staging job is skipped, so CI runs before AWS is set up.

### The repository's rules

`main` takes a commit only through a pull request, and production takes only a release its
reviewer approved. Both are settings GitHub keeps, which a private repository has on a paid
plan (Team or Enterprise; the organisation is on Enterprise since 2026-10-24, though GitHub's
rulesets API has answered 403 since the payment hold of 2026-10-26, so the ruleset holds nothing
until it answers again):

- **`main`** is held by the ruleset `main` (Settings, Rules, Rulesets), written in
  `.github/rulesets/main.json`: nothing reaches it but a pull request's merge, rebased or
  squashed so that the history stays one line; it is never deleted or rewritten; and nobody
  goes past it, the organisation's owners included. A pull request needs no approval (its
  author may not approve it, and the organisation has one member) and, while the app is in
  development (since 2026-10-31), no check: each check runs once for a change. The developer
  runs the backend's suite and the frontend's checks on their machine and merges at once; CI
  runs the heavy jobs (End to end, Load test, Image, Secrets, Scripts and configuration) once
  the change is on `main`, OpenTofu, Embeddings, Pooler (the suite through pgbouncer) and Audit
  run once a change to what they check is there (Audit every Monday too, for advisories
  published meanwhile), and Dependabot's pull requests run everything, on GitHub's runners, for
  the owner to merge. A red run on `main` is fixed by the next change. Before the app has users,
  take the conditions off CI's jobs (`if: *checked_here`, `if: *heavy` in `ci.yml`), put the
  checks back in the file as `required_status_checks` (each by its check run's name, from GitHub
  Actions, on the branch brought up to date with `main`), apply it, and wait for them again.
  `tests/test_repository.py` holds the file to CI's jobs and pins where each runs, so a job
  renamed, added or moved changes the file in the same pull request; with checks required,
  that pull request is mergeable once its checks passed and its file was applied (the second
  command below, from its branch), and the other open ones once brought up to date.
- **`production`** is a GitHub environment (Settings, Environments): a job of it waits until its
  reviewer approves, only `main` deploys to it, and administrators do not skip the review.
  The organisation's one member is the reviewer and approves their own releases
  (`prevent_self_review` false); with a second, make it true.

```bash
gh api -X POST repos/emudocai/emudoc/rulesets --input .github/rulesets/main.json     # once
gh api -X PUT "repos/emudocai/emudoc/rulesets/$(gh api repos/emudocai/emudoc/rulesets --jq '.[] | select(.name == "main") | .id')" --input .github/rulesets/main.json   # after a change to the file
gh api -X PUT repos/emudocai/emudoc/environments/production --input - <<EOF   # the environment, its reviewer by their GitHub login
{"reviewers": [{"type": "User", "id": $(gh api users/<login> --jq .id)}], "prevent_self_review": false, "can_admins_bypass": false,
 "deployment_branch_policy": {"protected_branches": false, "custom_branch_policies": true}}
EOF
gh api -X POST repos/emudocai/emudoc/environments/production/deployment-branch-policies -f name=main -f type=branch
```

While GitHub's checks cannot run and a fix cannot wait for them, an owner sets the ruleset's
enforcement to Disabled, merges, and sets it back to Active; the organisation's audit log keeps
both changes. A production root applied before 2026-10-24 trusts `main`: `tofu apply` it once,
or the environment's jobs are refused.

### The drill on AWS

The instance runs the managed drill every month ("The drill", above) as root:
`managed-restore-drill.sh` of the running release with `rds-drill.sh` as its hooks, the copy in
the environment's subnet group, security group and parameter group, at the environment's
database class unless `drill_instance_class` says otherwise, and the dry run in a container with
the storage reader's credentials. The instance's role may restore only its own database, and
tag and delete only copies called `emudoc-drill-…` that carry `purpose=restore-drill`.
`journalctl -u emudoc-restore-drill` keeps each report; a failed one reaches the alerts topic.

### What it leaves out

- The app's own high availability: one instance in one zone. RDS's standby covers the database;
  a lost instance or zone is downtime until `tofu apply` makes another, which runs the recorded
  release by itself.
- Throughput beyond one machine: llama.cpp embeds on the instance's CPUs, and the app gives a
  batch of 32 passages 120 seconds. Two vCPUs are slow for long passages; embed stages that keep
  failing on time call for a larger instance or a hosted endpoint ("The embeddings
  endpoint").
- The metrics' history: Prometheus keeps 15 days on the instance's disk, for the rules and a
  look on the day, and loses them with a replaced instance; dashboards over months need a
  Prometheus elsewhere (Amazon Managed Service for Prometheus takes `remote_write`).
- A CDN or firewall in front, IPv6, and the bucket's replication to a second region.

It was checked without an AWS account: the module and the account root by `tofu test` against
a mocked provider and by `validate`, the instance's part on Docker 25 (Amazon Linux's) with
Compose 5.5.1, fakes for the AWS CLI and systemd, a TLS Postgres under an RDS name and MinIO
with the module's policies as session policies, `push-image.sh` and `release.sh` with a
fake AWS CLI, a registry of their own and a health check that answered each version, and
Prometheus and Alertmanager from `deploy/aws/compose.yml` against a fake SNS (the watchdog on
the heartbeat topic every three minutes, `EmudocNoApi` and `EmudocNoWorker` on the alerts
topic and `EmudocProcessDown` held back, each request signed with the credentials of the file
the host renews, read again once renewed). The first
`apply` and release on a real account are the check of the rest, GitHub's sign-in and the
repository's policy among them.

## Checking it

1. `curl -sS https://emudoc.example.com/api/health` answers `{"status":"ok","version":"…"}`
   with the commit released, which the user menu shows too, and the browser shows the
   certificate. Plain `http://` redirects to it. From the image, `docker compose -f
   deploy/compose.yml ps -a` shows the release step exited with 0 and the API and the worker
   healthy, and
   `docker compose … exec api touch /app/x` fails with "Read-only file system".
2. Sign in. The account page lists the session with your own address, not the proxy's, and
   the API's log has no line saying a request "reached the app as plain HTTP": that line
   means the proxy did not send `X-Forwarded-Proto`, or uvicorn was not told to believe it.
3. Upload a document and ask a question about it: the upload goes through the proxy's body
   limit, and the answer appears as it is written rather than all at once. In the browser's
   network tab the events request stays open across the whole answer, with a `: keep-alive`
   line every 15 seconds while the model thinks, and no second events request follows it.
4. Search the library for a word, then look for it in the API's log and the proxy's: it is in
   neither, and the search request is logged as `search?q=…` or without its query.
5. The browser's developer tools show no "Refused to load" line in the console while you work,
   and the response headers of any request carry `Content-Security-Policy` and, over HTTPS,
   `Strict-Transport-Security`. With a bucket, the policy's `img-src` names the bucket's
   origin, which the page images and thumbnails redirect to. "Download original file" on the
   PDF saves it rather than opening the browser's viewer, and its response carries
   `Content-Security-Policy: sandbox` (with a bucket, the signed URL's
   `response-content-disposition` says `attachment` instead).
6. In a private window, sign in with a wrong password until the form says to wait a minute:
   the eleventh attempt does (`RATE_SIGN_INS`), and the API's log line names the email address
   only by its hash (`email:` and 64 hex digits), never as it was typed. A browser that signed
   in to the account before is counted by itself, and its line names `browser:` and a number.
7. The worker's log says at start which scanner it uses ("Uploads are scanned by ClamAV
   1.5.4/…"), with the signature version and date after the slashes. Upload a `.txt` file
   whose whole content is the EICAR test string (any antivirus vendor publishes it; it is
   harmless, and ClamAV flags it only as a file of its own, not inside another): within a
   moment the library shows it quarantined with the verdict `Eicar-Test-Signature`,
   "Download original file" is off, and `ALERT_EMAIL` gets "Emudoc quarantined 1 upload" at the next
   quarter of an hour. Delete it.
8. The API's and the worker's logs say at start "The bucket encrypts every file it keeps at
   rest" (with a bucket), and no line from `app.storage` warns about it; and "Secrets in the
   database are encrypted with key …, the first of ENCRYPTION_KEYS", and no line from
   `app.encrypted` warns. Turn two-factor sign-in on and sign in again with the app's code: the
   account's `totp_secret` in the database begins `v1.` and that key's eight digits. Request a
   password reset: the mail arrives, and the API's log has no line saying the relay "presented
   a certificate this machine does not trust". With a database off the own network, `psql` given
   the same URL without `+psycopg` opens with `SSL connection (protocol: TLSv1.3, …)`, and
   with `sslrootcert` pointed at an authority that did not sign the server's certificate it
   refuses to connect. `psql` has the system's libpq; that the app started at all is the check
   of its own connection, which outside the image needs `SSL_CERT_FILE` ("Encryption").
9. On the account page, "Download your data" saves `emudoc-account-<date>.json` with your
   account, sessions and chats and no password hash. Invite a throwaway account, delete it
   with its password: the notice arrives by mail, the page offers only to keep it, and signing
   in elsewhere shows the same. Keep it, delete it again, and check after the 14 days (or move
   `users.deletion_due_at` into the past and wait for a housekeeping round) that signing in
   with it fails, the worker's log says "Deleted account", and the bucket or directory holds
   its record under `erasures/users/`. Export a project from its card
   on the projects page: the notice comes, and the ZIP file holds `project.json`, the
   originals and `documents/<id>.json`; the bucket or directory holds it under the project's
   `exports/`.
10. Whatever collects the logs deletes them after the period the team chose ("How long they
    are kept": on AWS, `sudo journalctl -q -o short-iso | head -n 1` on an instance that has run
    for `server_log_days` shows a line no older). Any `/api` response carries `Cache-Control:
    no-store`, and signing out answers with `Clear-Site-Data: "cache"`.
11. The API's and the worker's logs say at start how long the bucket keeps a deleted file,
    within `BACKUP_DAYS`, and `pgbackrest --stanza=emudoc info` says `status: ok` and `cipher:
    aes-256-cbc` with a full backup from the last week and WAL archived up to the last minutes.
    Before launch, drill a restore on staging as "The drill" says: a document deleted after the
    moment opens again, one uploaded after it is gone from the library, and a browser signed in
    before the restore is sent to the sign-in page. `systemctl list-timers
    emudoc-restore-drill.timer` shows the monthly drill's next run, and its first one, started
    by hand, ends with "The drill passed.", reaching within five minutes of its end while
    members work.
12. Ask a question whose answer takes a while and, while it is being written, `docker compose
    -f deploy/compose.yml restart api` (or roll the API's pods): the answer pauses and goes on
    from where it was once the API is back, the API's log says "Shutting down" and "Finished
    server process" within a second of the stop, and has no `Cancel … running task(s)` line,
    which would mean a request outlasted the 8 seconds.
13. The API's and the worker's logs say at start "The database is Postgres 17.… with pgvector
    0.8.…; the role emudoc has statement_timeout 30s and idle_in_transaction_session_timeout
    5min.", with no warning from `app.postgres`, and `python -m app.maintenance database` (from
    the image, `docker compose -f deploy/compose.yml run --rm --no-deps worker python -m
    app.maintenance database`) ends with "Nothing to look at." and exits with 0. Behind a pooler
    in transaction mode, the worker's log has no line saying the threads "find queued jobs by
    polling", and an answer starts within a second of the question. From a machine outside the
    deployment's network, `pg_isready -h` the database's host gets no answer.
14. The API's and the worker's logs say at start "Passages are embedded by" the model at the
    endpoint, with no warning from `app.startup` about it. Upload a document and search the
    library for something it says in other words than its own, such as "late fees" for a
    contract that speaks of a penalty for delay: the passage is found, the document's embed
    stage is done, and the API's log has no line saying a search "matched keywords only".
15. The API's and the worker's logs say at start "Documents are indexed by" and "Questions are
    answered by" the models, each with its fallback "when it cannot answer", and no line says a
    model "has no fallback" unless the deployment keeps to one provider on purpose. `docker
    compose -f deploy/compose.yml run --rm --no-deps worker python -m app.maintenance models`
    ends with "Every model answered as the app asks it" and exits with 0, with no line saying
    `MODEL_PRICES` has no price for a model.
16. On AWS, `release.sh` ends with "is released on staging, and … is ready". On the instance
    (`aws ssm start-session`), `sudo emudoc-compose ps` shows the API and the worker healthy
    on the tag, `systemctl list-timers 'emudoc-*'` the credentials' renewal within the quarter
    hour and the drill's next run, and `sudo docker run --rm <image> python -c 'import boto3;
    print(boto3.Session().get_credentials())'` prints `None` after a second: a container does
    not reach the instance's role. A release of a tag whose migration fails ends with "was not
    recorded", and `/api/health` answers throughout.
17. The API's and the worker's logs say at start "Metrics are served at http://0.0.0.0:9464/metrics",
    the worker's "with the database's figures". From the machine, `docker compose -f
    deploy/compose.yml exec worker python -c "import urllib.request;
    print(urllib.request.urlopen('http://127.0.0.1:9464/metrics').read().decode())" | grep
    '^emudoc_documents'` lists the documents by status; from outside the deployment's network
    the port gets no answer, and `https://emudoc.example.com/metrics` is the page, not them.
    Upload a document and ask a question: the scraper shows `emudoc_stage_runs_total` with
    `outcome="done"` for every stage and `emudoc_model_requests_total` with `outcome="ok"`, and
    no series names a member, an organisation or the question.
18. With `ALERT_EMAIL` set, the worker's log has no line saying it is not, and no line from
    `app.watch` saying it "could not check" something. Stop the scanner (`docker compose -f
    deploy/compose.yml stop clamav`): within the quarter of an hour the address gets "Emudoc
    alert: the malware scanner does not answer", and after `start clamav` and the next round
    "Emudoc alert over: …". With a scraper, its rules page lists the 33 rules of
    `deploy/prometheus/alerts.yml` without an error and none firing but `EmudocWatchdog`, which
    the dead man's switch hears; with the API stopped, `EmudocNoApi` fires after 5 minutes. On
    AWS, Prometheus's page through Session Manager ("Alerts", "On AWS") shows the same, and
    CloudWatch shows the alarm `emudoc-<environment>-alerts-silent` OK.
    `systemctl list-timers emudoc-watch.timer` shows the next look, and stopping a container for
    ten minutes (`sudo emudoc-compose stop clamav`) reaches the alerts topic and the pager, and
    its start again "the containers are well again". Stopping Alertmanager for twenty minutes (`sudo
    emudoc-compose stop alertmanager`) brings the watch's message and CloudWatch's alarm, and
    its start the alarm's OK; `sudo emudoc-compose stop api` brings "Emudoc staging:
    EmudocNoApi" after five minutes, to the pager too, and no `EmudocProcessDown`.
19. On AWS, CloudWatch lists the account's ten `emudoc-account-…` alarms, none in alarm, and the
    log group `emudoc-trail` receives the trail's events. Making and deleting a security group
    no one needs (`aws ec2 create-security-group --group-name emudoc-alarm-check --description
    'An alarm check'`, then `aws ec2 delete-security-group --group-name emudoc-alarm-check`)
    brings `emudoc-account-security-groups` to `account_alert_email` within minutes.
20. Ask a question: the worker's log has its run's line, "Job … (answer_question) ran … s
    (done)", whose `timing` lists the model's turns with the configured model and the tool
    calls by name, and the second recipe of "Traces" with its `trace_id` finds the API's lines
    of the question too, the access line of its `POST` with its `seconds`. Neither holds the
    question's words or the answer's.
21. Upload a document and ask a question about it: the scraper shows
    `emudoc_document_ready_seconds_count` and `emudoc_answer_first_text_seconds_count` at 1 or
    more, and its rules page the six rules of `emudoc-service-levels`, none firing. The uptime
    service outside the deployment shows `/api/health` up. `maintenance jobs` (as "Runbooks"
    runs it) prints the queue, and `maintenance jobs --document` with the document's id says
    "It is ready."

## Every setting

Every variable the app reads, with its default and what production asks of it. `.env.example`
says more of each, and the sections above say how they fit together. "required" is what
production refuses to start without (`production_problems` in `backend/app/config.py`, which
`backend/tests/test_config.py` holds this list to, as it holds the list to every setting there
is); "needed for" is what one choice brings with it. On AWS the module writes some of them and
the roots' `settings` the others ("The settings").

### The process

| Variable | Default | In production | What |
| --- | --- | --- | --- |
| `ENVIRONMENT` | `development` | `production`, which the image sets | Whether production's checks apply; staging is production too |
| `LOG_FORMAT` | `json` in production, `text` in development | | How each line is written ("Logs") |
| `RUN_WORKER` | false in production, true in development | | Whether the API runs the worker's threads too ("The pieces") |
| `METRICS_ADDRESS` | none; `0.0.0.0:9464` in the image | | Where the metrics are served, on the own network alone ("Metrics") |
| `SENTRY_DSN` | none | a secret | The error tracker ("Error tracking") |
| `SENTRY_ENVIRONMENT` | `ENVIRONMENT` | | What the tracker files the reports under |
| `LOCAL_ACCOUNT` | `false` | refused when true | Requests without a session act as a local owner, for development |
| `APP_URL` | the request's origin | required | Where members reach the app: the links in mail, the OAuth redirect |
| `SIGNUP_OPEN` | `false` | | Whether anyone who reaches the install may make an account, which joins the workspace ("The first run, and who signs up") |
| `SESSION_DAYS` | `30` | | How long a sign-in lasts; an organisation may sign its members in again sooner |
| `SAMPLE_PROJECT` | `true` | | Whether the workspace the setup makes starts with the sample project ("The sample project") |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1,::1` | the proxy's address | uvicorn's: whose `X-Forwarded-*` headers are believed ("TLS at a reverse proxy") |
| `TMPDIR` | `/tmp` | | Where uploads pass through and a worker copies an original to ("The image") |
| `EMUDOC_VERSION` | `unknown` in an image built without `VERSION`, `development` in a checkout | | The image's `VERSION`, which `/api/health` names; not set by hand |

### The database

| Variable | Default | In production | What |
| --- | --- | --- | --- |
| `DATABASE_URL` | none: every process stops without it | required | The database, with `sslmode=verify-full` off the own network ("The database", "Encryption") |
| `DATABASE_POOLER` | none | | `transaction` behind a pooler in transaction mode ("A pooler") |
| `DATABASE_LISTEN_URL` | none | needed behind such a pooler | Where the worker listens for queued jobs |
| `DB_POOL_SIZE` | `10` | | Connections each process keeps open |
| `DB_MAX_OVERFLOW` | `21` | | Connections beyond them while those are in use |
| `DB_POOL_TIMEOUT` | `5` | | Seconds a request waits for a connection before its 503 |
| `SSL_CERT_FILE`, `SSL_CERT_DIR` | Debian's bundle in the image | needed for `sslrootcert=system` outside the image | Where OpenSSL finds the authorities ("Encryption") |
| `PGSSLROOTCERT` | none | | libpq's default for `sslrootcert` |

### Files and uploads

| Variable | Default | In production | What |
| --- | --- | --- | --- |
| `STORAGE_URL` | `backend/data` in a checkout | required | The directory or bucket of the files ("The pieces", "Backups") |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN` | none | a secret, for a bucket | boto3's credentials for the bucket |
| `AWS_DEFAULT_REGION` | none | needed for an AWS bucket | The bucket's region, which URLs are signed for |
| `AWS_ENDPOINT_URL` | AWS's | needed for another provider's bucket | MinIO's, R2's, Hetzner's address, over `https://` off the own network |
| `AWS_CONFIG_FILE`, `AWS_EC2_METADATA_DISABLED` | none | | boto3's; on AWS the role's renewed session (`/run/aws/config`) and no metadata service ("What an environment is made of") |
| `PAGE_IMAGE_DAYS` | `30` | | How long a rendered page or thumbnail is kept; 0 for good |
| `MAX_UPLOAD_MB` | `100` | | The largest file |
| `MAX_UPLOAD_FILES` | `100` | | Files one request may carry |
| `MAX_UPLOAD_REQUEST_MB` | `500` | | The bytes of one request's files together |
| `SCANNER_URL` | none | a warning without, `off` alike | The ClamAV daemon every upload is scanned by |
| `CONVERTER_URL` | none | `http://converter:8080` beside the app | The converter that shows Word, PowerPoint, Excel, RTF and HTML documents as they look ("The converter"); none shows them by their text |
| `PARSE_TIMEOUT` | `300` | | Seconds a parse may take |
| `PARSE_MEMORY_MB` | `2048` | | The memory a parse may hold |
| `PARSE_MAX_PAGES` | `5000` | | The pages of a PDF or a TIFF, the slides of a presentation |
| `PARSE_MAX_ROWS` | `100000` | | The rows of a sheet |
| `PARSE_MAX_CHARS` | `20000000` | | The characters of text one document may hold ("Large documents") |
| `PARSE_PART_PAGES` | `250` | | The pages of a PDF one parse child reads, the document made of the parts by one more; 0 reads it whole in one ("Large documents") |
| `RENDER_PROCESSES` | `2` | | The pages a process draws at once, each in a child of its own ("Drawing pages") |
| `RENDER_TIMEOUT` | `30` | | Seconds a page may take to draw; 0 for no limit |
| `RENDER_MEMORY_MB` | `1024` | | The memory a drawing child may hold; 0 for no limit |
| `RENDER_CACHE_MB` | `256` | | The copies of a bucket's originals an API keeps a few minutes for the next pages it draws; 0 for none ("Drawing pages") |
| `PRERENDER_PAGES` | `3` | | A PDF's first pages drawn once it is parsed; 0 for none |
| `OCR_MAX_PAGES` | `100` | | The pages of one document read as scans |
| `OCR_CONCURRENCY` | `4` | | The pages of one scan read at once, a model request each ("Large documents") |
| `ENRICH_MAX_REQUESTS` | `50` | | The requests one document's summaries and facts may take; 0 for no limit |

### The models

| Variable | Default | In production | What |
| --- | --- | --- | --- |
| `AGENT_MODEL` | `google:gemini-3.5-flash-lite` | required, by its version | The model that answers ("The models") |
| `INDEX_MODEL` | `google:gemini-3.5-flash-lite` | required, by its version | The model that reads the documents |
| `AGENT_FALLBACK_MODEL` | none | a warning without | Asked when `AGENT_MODEL` cannot answer |
| `INDEX_FALLBACK_MODEL` | none | a warning without | Asked when `INDEX_MODEL` cannot answer |
| `AGENT_EFFORT` | `medium` | | How hard the agent's model thinks |
| `INDEX_EFFORT` | `low` | | How hard the indexing model thinks |
| `AGENT_MAX_TURNS` | `12` | | The rounds of tool calls one answer may make |
| `MODEL_PRICES` | none | needed for `DAILY_SPEND_CAP` | Each model's price per million tokens in and out |
| `PRICE_CURRENCY` | `USD` | | The prices' currency |
| `DAILY_SPEND_CAP` | `0` | | What every organisation together may spend a day; 0 for no limit |
| `DISABLED_PROVIDERS` | none | | The kill switch: providers switched off, by prefix |
| `GOOGLE_PAID_TIER` | `false` | a warning without, for a `google:` model | Billing is on for the Gemini key's project, so Google trains on nothing it is sent |
| `GOOGLE_API_KEY`, `GEMINI_API_KEY` | none | a secret, for a `google:` model | The Gemini API's key |
| `GOOGLE_APPLICATION_CREDENTIALS`, `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION` | `us-central1` for the location | needed for a `google-cloud:` model | Vertex AI's service account, project and location |
| `ANTHROPIC_API_KEY` | none | a secret, for an `anthropic:` model | Anthropic's key |
| `OPENROUTER_API_KEY` | none | a secret, for an `openrouter:` model | OpenRouter's key |
| `OPENAI_API_KEY` | none | a secret, for an `openai:` model | OpenAI's key, or that of the server `OPENAI_BASE_URL` names |
| `OPENAI_BASE_URL`, `OLLAMA_BASE_URL` | OpenAI's; an Ollama on this machine | over `https://` off the own network | Another address for `openai:` models, or Ollama's for `ollama:` ones |

Other providers Pydantic AI supports read their own variables (`MISTRAL_API_KEY`, `GROQ_API_KEY`
and the like), named on its models' pages.

### Embeddings and search

| Variable | Default | In production | What |
| --- | --- | --- | --- |
| `EMBEDDING_BASE_URL` | `http://127.0.0.1:11434/v1` | required | The OpenAI-compatible endpoint ("The embeddings endpoint") |
| `EMBEDDING_API_KEY` | none | a secret, for a hosted endpoint | Its key |
| `EMBEDDING_MODEL` | `embeddinggemma` | | The model's name, under which the vectors are stored and priced |
| `EMBEDDING_DIM` | `768` | | The vectors' size; a change needs `rebuild-embeddings` |
| `EMBEDDING_QUERY_PREFIX` | `task: search result \| query: ` | | What a search is prefixed with |
| `EMBEDDING_DOCUMENT_PREFIX` | `title: none \| text: ` | | What a passage is prefixed with |
| `FTS_LANGUAGE` | `english` | | The text search configuration of new passages, for an organisation that chose no language of its own |

### The worker and the limits

| Variable | Default | In production | What |
| --- | --- | --- | --- |
| `WORKER_CONCURRENCY` | `16` | | Documents a worker indexes at once |
| `ANSWER_CONCURRENCY` | `8` | | Questions a worker answers at once |
| `PARSE_CONCURRENCY` | `2` | | Files a worker parses at once, each in a child process ("Large documents") |
| `AUTOMATION_WAIT` | `120` | | Seconds an automation waits for the documents it watches to stop arriving before it runs a project's cross-checks |
| `MAX_ANSWERS` | `20` | | Answers one organisation may have in flight |
| `MAX_QUESTION_CHARS` | `20000` | | The longest question |
| `RATE_UPLOADS` | `20` | | Upload requests a minute, per member ("Rates") |
| `RATE_QUESTIONS` | `20` | | Questions a minute, per member |
| `RATE_SEARCHES` | `120` | | Searches a minute, per member: a project's search and facts, and the search of every project |
| `RATE_SIGN_INS` | `10` | | Sign-in requests a minute, per address named |
| `RATE_SIGN_INS_PER_ADDRESS` | `60` | | Sign-in requests a minute, per client address |
| `RATE_FAILED_SIGN_INS` | `20` | | Wrong passwords and codes an hour, past which those sign-ins pause ("Rates") |
| `RATE_PAGE_ERRORS` | `10` | | The page's error reports a minute, per client address |
| `RATE_FEEDBACK` | `10` | | Messages from the help menu an hour, per member ("Support") |
| `RATE_EXPORTS` | `10` | | Facts tables, drafts, answers and chats downloaded a minute, per member, each every row its conditions keep or the text laid out whole |
| `RATE_SHARED_LINKS` | `30` | | Share links opened a minute, per client address ("Rates") |
| `RATE_SHARED_PASSWORDS` | `10` | | Wrong passwords an hour per share link, from every address and from each, past which it opens with none ("Rates") |
| `RATE_API_REQUESTS` | `300` | | Requests a minute, per API key, on top of its member's rates ("Rates") |

### Mail and sign-in

| Variable | Default | In production | What |
| --- | --- | --- | --- |
| `SMTP_URL` | none: nothing is mailed, and the log has each message (development) or its subject | a warning without it or `RESEND_API_KEY` | The relay the mail goes through ("Mail") |
| `RESEND_API_KEY` | none | a secret, or `SMTP_URL` | Resend's API key, which sends the mail through Resend instead ("Mail") |
| `RESEND_WEBHOOK_SECRET` | none | a secret | The signing secret of Resend's webhook, which says what bounced ("Mail") |
| `MAIL_EVENTS_QUEUE` | none | | The SQS queue of SES's bounces and complaints, which the worker reads ("Mail"; the AWS module writes it) |
| `INBOUND_DOMAIN` | none | with a provider that hands its mail over | The domain of the projects' addresses, whose mail becomes their documents ("Mail in") |
| `INBOUND_MAIL_QUEUE` | none | | The SQS queue of SES's notices of the mail sent to projects' addresses, which the worker waits on ("Mail in"; the AWS module writes it) |
| `PWNED_PASSWORDS_URL` | `https://api.pwnedpasswords.com` | | Where new passwords are checked against breaches ("Rates"); empty checks nothing, for a server without the internet |
| `ENCRYPTION_KEYS` | development's key, which is in the code | required, a secret | The keys the authenticators' secrets and the connected folders' grants are encrypted with, the first encrypting ("The encryption keys") |
| `MAIL_FROM` | `Emudoc <no-reply@localhost>` | | The sender |
| `ALERT_EMAIL` | none | | Where the worker reports ("What the worker mails") |
| `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET` | none | the client secret is a secret | Sign-in with Google |
| `MICROSOFT_OAUTH_CLIENT_ID`, `MICROSOFT_OAUTH_CLIENT_SECRET` | none | the client secret is a secret | Sign-in with Microsoft |
| `MICROSOFT_OAUTH_TENANT` | `common` | | Whose Microsoft accounts may sign in |
| `GOOGLE_DRIVE_CLIENT_ID`, `GOOGLE_DRIVE_CLIENT_SECRET` | none | the client secret is a secret | Folders of Google Drive connected to projects ("Connected folders") |
| `DROPBOX_APP_KEY`, `DROPBOX_APP_SECRET` | none | the app secret is a secret | Folders of Dropbox connected to projects ("Connected folders") |
| `ONEDRIVE_CLIENT_ID`, `ONEDRIVE_CLIENT_SECRET` | none | the client secret is a secret | Folders of OneDrive and SharePoint connected to projects ("Connected folders") |
| `ONEDRIVE_TENANT` | `common` | | Whose Microsoft accounts may connect their folders |
| `CONNECTOR_BASE_URL` | none | refused | Where the connected folders' services are reached in their place: the browser test's stand-in |

### Support and backups

| Variable | Default | In production | What |
| --- | --- | --- | --- |
| `SUPPORT_EMAIL` | none | | Where members write for help, from the app's help menu, in place of the workspace's owners and admins ("Support"); the mail to an address an account left names it |
| `STATUS_PAGE_URL` | none | | The status page outside the deployment that the help menu links to ("The status page") |
| `BACKUP_DAYS` | none | | How long the backups are kept: `backup` removes its older ones, and the bucket's earlier versions are held to it ("Backups") |

The compose files read a few of their own: `POSTGRES_PASSWORD` (the database's, which
`DATABASE_URL` repeats), `EMUDOC_TAG` (the image's tag on GHCR, `latest` by default),
`EMUDOC_IMAGE` (another image to run, such as one built here), `EMUDOC_ENV` (the app's settings'
file, `.env` beside the compose file by default), `EMUDOC_BACKUPS` (where `backup` writes, the
`backups` volume by default), `COMPOSE_PROFILES` (Compose's own: `scan`,
`office`, `tls`) and `APP_URL` for Caddy, in `deploy/compose.yml`, with `EMBEDDING_WEIGHTS_URL`
and `EMBEDDING_WEIGHTS_SHA256` for the embeddings server's weights;
`EMUDOC_DOMAIN`, `EMUDOC_ENVIRONMENT`, `EMUDOC_ALERTS`, `EMUDOC_PAGES` and `EMUDOC_HEARTBEAT`
in `deploy/aws/compose.yml`, which each release writes. The drill's `DRILL_*` and `RDS_*` are in
its scripts' headers ("The drill").
