# Deploying Emudoc

How to run the app on a server. The README's "Run it" is the development path, one `uvicorn`
on one machine; this is what changes for a deployment. The processes below run from the image
("The image") or from a checkout with `uv`, as in development: the commands are the same, with
`uv run` in `backend/` before each in a checkout.

## The pieces

- **Postgres 17 with pgvector 0.8 or later**, named in `DATABASE_URL`, managed or with a
  standby of the deployment's own, on no public address, its role given a statement and an
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
- **A reverse proxy** that holds the certificate and forwards to the API: Caddy or nginx, below.
- **ClamAV**, named in `SCANNER_URL`, which scans every upload before a worker parses it and
  before the API serves it to the other members of a project. The `clamav/clamav` image with
  the signatures on a volume is enough: the workers stream each file to the daemon over its
  port, so it needs no access to the storage and can run on any machine the workers reach,
  one for all of them. It wants about a gigabyte of memory for the signatures and a minute to
  load them at start, during which the workers hold uploads in the queue and try again. Its
  own limits must allow a file as large as `MAX_UPLOAD_MB`: `StreamMaxLength`, `MaxFileSize`
  and `MaxScanSize` in `clamd.conf` (the compose file's `clamav` service sets them through
  the image's `CLAMD_CONF_*` variables; an older image takes a mounted `clamd.conf`). A
  deployment that runs without a scanner says so with `SCANNER_URL=off`, and the worker
  warns at every start.
- **An embeddings endpoint** in `EMBEDDING_BASE_URL`, which turns every passage and every
  search into a vector for search by meaning: llama.cpp's server beside the app, as the
  compose file runs it, or a hosted one: "The embeddings endpoint", below.
- **An SMTP relay** in `SMTP_URL` for the sign-in links.
- **The model providers** named by `INDEX_MODEL` and `AGENT_MODEL`, and by their fallbacks,
  each on terms that forbid training on what it is sent, each model named by its version and
  tried with `python -m app.maintenance models`: "The models" and "What leaves the deployment",
  below.
- **A region**, if the deployment promises one: `DATA_REGION=eu` or `us` holds the models, the
  bucket and the embeddings to it, and every server and database must be in it too: "Keeping
  the data in one region", below.
- **The operator's word** for the terms of service and the privacy policy: `OPERATOR_NAME`,
  `OPERATOR_EMAIL` and `BACKUP_DAYS`: "Terms of service and privacy policy", below.
- **Backups** of the database and of the files, encrypted and kept for `BACKUP_DAYS`, which
  restore to the same point: "Backups", below.
- **A scraper for the metrics**, if the deployment keeps them: Prometheus or anything that reads
  its format, asking the API and every worker on a port of their own, with Alertmanager for the
  rules in `deploy/prometheus`: "Metrics" and "Alerts", below.
- **An address for the alerts**, `ALERT_EMAIL`, where the worker reports what it gave up on and
  what it sees go wrong, through the same relay: "Alerts", below.

`deploy/aws` builds all of it on AWS, staging and production alike: "On AWS", below.

The settings arrive through the process environment or a secret manager, never a `.env` file:
`ENVIRONMENT=production` makes the app copy nothing from one, and makes it refuse to start with
development's conveniences (the compose database's password, `LOCAL_ACCOUNT=true`, no
`STORAGE_URL`, `APP_URL`, `SMTP_URL`, `SCANNER_URL`, `EMBEDDING_BASE_URL`, `HOSTING_PROVIDER`,
`OPERATOR_NAME`, `OPERATOR_EMAIL` or `BACKUP_DAYS`, no `AGENT_MODEL` or `INDEX_MODEL` or one
its provider moves, a free-tier Gemini key, no key for a model or its fallback, an embeddings
endpoint that turns its requests down, with
`DATA_REGION` anything outside the region, and a connection that would carry the documents
unencrypted off the deployment's own network), naming each.
`.env.example` documents every setting; the README's "Configuration" says what the ones that
matter do.

## The image

`Dockerfile` builds one image for every process: the API is its default command, and the
worker, the release step and the maintenance commands are the others. Build it from a clean
checkout of the commit to deploy, tagged with its full SHA and told it as `VERSION`, which
`/api/health` reports, the user menu shows and each process logs as it starts ("Starting
Emudoc 3f2c1ab…"); an image built without it says `unknown`, a checkout `development`:

```bash
commit=$(git rev-parse HEAD)
docker build -t emudoc:$commit --build-arg VERSION=$commit .
```

`.github/scripts/check-image.sh emudoc:<tag>` tries it against the development database of
`docker-compose.yml`: it runs the release step, the API and a worker from the image, read-only
with a tmpfs at `/tmp` as below, and checks the probes, the page and the files the page names.
CI does the same for every change.

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
  (`MAX_UPLOAD_REQUEST_MB`, and `MAX_UPLOAD_MB` again, per upload at a time) and for what a
  worker works on (`MAX_UPLOAD_MB` per `WORKER_CONCURRENCY`). A tmpfs counts as the
  container's memory and keeps the documents in the clear ("Encryption", below). A process that
  cannot write a temporary file refuses to start: "No temporary directory can be written in
  …".
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
in the `Dockerfile`, and uv's), as are the images beside it in the compose files (ClamAV,
llama.cpp's server, Caddy), so a commit builds the same image whenever it is built and a tag
moved on a registry changes nothing until a commit does. A security fix in Debian therefore
reaches a deployment as a commit and a release. Dependabot proposes the digest a base was
rebuilt with every Monday (`.github/dependabot.yml`), and the Audit workflow builds the image
every morning and fails on a high or critical vulnerability that has a fix (trivy), which is the
sign to take that pull request, or to move the digest by hand (`docker buildx imagetools inspect
python:3.13-slim-trixie` prints the tag's), and to release the new commit. The same scan by
hand, of an image built here, with what was accepted in `.trivyignore.yaml`:

```bash
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock -v "$PWD:/src:ro" -w /src \
  aquasec/trivy:0.73.0 image --scanners vuln,secret --severity HIGH,CRITICAL --ignore-unfixed \
  --ignorefile .trivyignore.yaml emudoc:<tag>
```

The images that run beside it, ClamAV's, llama.cpp's and Caddy's, get the same scan every
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
EMUDOC_ENV=/dev/null EMUDOC_IMAGE=emudoc:app EMUDOC_DOMAIN=example.org \
  docker compose -f deploy/compose.yml -f deploy/aws/compose.yml config --images
docker run --rm -v "$PWD:/src:ro" -w /src aquasec/trivy:0.73.0 image --image-src remote \
  --platform linux/amd64 --timeout 20m --scanners vuln,secret --severity HIGH,CRITICAL \
  --ignore-unfixed --ignorefile deploy/.trivyignore.yaml caddy:2.11.7@sha256:…
```

`deploy/compose.yml` puts it together on one machine: the release step, the API, a worker,
ClamAV, and llama.cpp's server for the embeddings, with the settings in
`/etc/emudoc/emudoc.env` (`EMUDOC_ENV` names another file):

```bash
export EMUDOC_IMAGE=emudoc:$(git rev-parse HEAD)
docker compose -f deploy/compose.yml up -d --wait                 # also updates to a new tag
docker compose -f deploy/compose.yml up -d --scale worker=3       # more workers
docker compose -f deploy/compose.yml run --rm --no-deps worker python -m app.maintenance failed-jobs
```

`up` runs `alembic upgrade head` first and starts the API and the worker once it has
succeeded, and `--wait` returns once the API answers its health check and the worker keeps
its alive file. It replaces the running API and worker before it runs the migrations, though,
so a migration that fails there leaves neither running; an update runs them alone first, and
stops there if they fail, and so does the embeddings server, whose download and start the API
and the worker wait for in the same way:

```bash
docker compose -f deploy/compose.yml run --rm --no-deps migrate && \
  docker compose -f deploy/compose.yml up -d --wait embeddings && \
  docker compose -f deploy/compose.yml up -d --wait
```
 The file gives its
network a subnet of its own, `10.73.0.0/24`, so that `FORWARDED_ALLOW_IPS` can name the gateway
(change both if the subnet collides with one the machine reaches), and its project another name
than the development file's, so one machine can run both. The database, the mail relay, a
bucket and the proxy are the deployment's: the env file names them, with
`SCANNER_URL=clamd://clamav:3310` for the daemon beside the app and
`EMBEDDING_BASE_URL=http://embeddings:8080/v1` for the embeddings. Compose expands `$` in the
env file's values unless they are in single quotes, which a password may need. Wherever this
guide says `uv run …`, the image runs the same command through `docker compose … run --rm
--no-deps worker`: without `--no-deps`, `run` runs the release step first, with whatever image
`EMUDOC_IMAGE` names.

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

`GET /api/status` is no probe: it is the status line the members see, signed in only, and it
asks the embeddings endpoint (at most once a minute per process, waiting 10 seconds at most).
In production it names no model, address or setting: while the models cannot be used it says
that questions cannot be asked and documents get no summaries or facts for now, and while the
embeddings endpoint does not answer that search matches keywords only, each with "Try again
later". The operator reads why in the start's log, the metrics and the alerts (below).

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

New connections take them, so restart the app after. In production a role without them is a
warning at every start, with these two statements. A member's request whose statement runs
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

Each process holds up to `DB_POOL_SIZE` + `DB_MAX_OVERFLOW` connections (15 by default), and a
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
(migrations, housekeeping, `after-restore`, the alerts), and the one setting it changes,
search's `hnsw.iterative_scan`, is `SET LOCAL`. The release step and the maintenance commands
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
replaced since it began, in every table), when pgvector is older than 0.8, and in production
when the role has no timeouts. Run it from a timer that mails on failure, as for the drill, or
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
model's prefixes (`EMBEDDING_QUERY_PREFIX`, `EMBEDDING_DOCUMENT_PREFIX`, nomic-embed-text's by
default). Without one, search matches keywords only, so neither a member nor the agent finds a
passage worded otherwise than the question, and every document's embed stage is tried again
after 1, 2, 4 and 8 minutes and then given up, each a failed job for `ALERT_EMAIL`. The default
address, an Ollama on 127.0.0.1, is in the image the container itself, so production refuses to
start with `EMBEDDING_BASE_URL` unset or blank.

Every process asks the endpoint for one vector at start and says what it found: "Passages are
embedded by nomic-embed-text at http://embeddings:8080/v1". An endpoint that turns the request
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
keeps it under it, so a first `up` needs the weights' 274 MB from Hugging Face and later ones
nothing from anywhere, and every machine and every release serves the same file. The default is
nomic-embed-text v1.5 at 16 bits as Nomic published it, at a fixed commit of its repository. The
server answers whatever model a request names, so `EMBEDDING_MODEL` stays the app's name for
the weights, under which the vectors are stored and priced. Its vectors are Ollama's:
llama.cpp b11706 and Ollama 0.40.5 embedded the same texts to a cosine of 0.999999 or closer,
with the same token counts, so a deployment that ran Ollama keeps its stored vectors and its
`EMBEDDING_MODEL`, and can remove what Ollama leaves behind (`docker volume rm
emudoc-deploy_models`, `docker image rm ollama/ollama:0.40.5`). Other weights are another GGUF
file named to compose beside `EMUDOC_IMAGE`, `export
EMBEDDING_WEIGHTS_URL=https://huggingface.co/<repository>/resolve/<commit>/<file>.gguf
EMBEDDING_WEIGHTS_SHA256=<the SHA-256 Hugging Face shows for the file>`, with a new
`EMBEDDING_MODEL`, its `EMBEDDING_DIM` and prefixes in the env file ("Changing the model",
below); on AWS the two are settings like the others. A file that does not match the hash stops
`up` with "The file at EMBEDDING_WEIGHTS_URL is not the one EMBEDDING_WEIGHTS_SHA256 names."

The server makes embeddings alone (no chat page, no slots endpoint) and logs warnings and
errors, never what it is sent. It holds about 500 MB with nomic-embed-text, and its image is 300
MB to download and 1.2 GB on disk. Its batches hold 8,192 tokens (`--ubatch-size`), more than
the 6,000 characters the app sends at most, so that an input past the model's context of 2,048
tokens, such as a passage of dense Chinese or a long row of a table, is refused as such (400,
`exceed_context_size_error`) rather than as a failure of the server's; the app then embeds that
input from its beginning, cut to what the context holds, as Ollama did by itself. llama.cpp has
no releases but a build a day under the `server` tag: Dependabot proposes the newest digest
every Monday, and CI's Embeddings job (`.github/scripts/check-embeddings.sh`) starts the compose
file's two services with it and fails when the vectors of `.github/scripts/embeddings.json` move
(a cosine below 0.9999), the texts' token count changes, or the refusal does. A build that fails
it is not taken; a later one that passes is. The vectors are the ones the deployments' databases
hold, so a build whose vectors moved would rank every stored passage against a query of another
model, without a word in any log; other weights chosen on purpose rewrite the file with the
script's `--write`. On the deployment's own network the server is no subprocessor and takes
plain HTTP ("Encryption"); on another machine there, such as a GPU server for a large backlog
(llama.cpp's CUDA image), it listens on a private address, and across a network the deployment
does not own it sits behind TLS, as production requires.

On a CPU, embedding is the slow part of indexing. On an 8-core laptop CPU (Ryzen 7 7435HS, in
Docker, October 2026), llama.cpp b11706 embedded a batch of 32 passages of 1,500 characters in
7 seconds and of 6,000 characters (the most the app sends) in 23, and Ollama 0.40.5 in 9 to 11
and in 23; the app waits 120 seconds for a batch. Hugging Face's text-embeddings-inference also
serves `/v1/embeddings`, from a 240 MB image, but its CPU build (1.9) could not load
nomic-embed-text v1.5 on its fast backend and ran it five times slower than Ollama, so that a
batch of long passages outlasted the 120 seconds; it suits a GPU, or a model its ONNX backend
loads.

**A hosted endpoint** is any OpenAI-compatible service with a key in `EMBEDDING_API_KEY`, such
as OpenAI's: `EMBEDDING_BASE_URL=https://api.openai.com/v1`,
`EMBEDDING_MODEL=text-embedding-3-small`, `EMBEDDING_DIM=1536`, and both prefixes set empty.
It is sent every passage and every search, so it is a company like the model providers: the
subprocessor list names it by its host, `DATA_REGION` keeps only Vertex AI's addresses and
OpenAI's European one in a region, and its terms must forbid training on what it is sent. The
privacy policy promises that for OpenAI's API, Azure OpenAI and Vertex AI (and for an endpoint
on the own network), and for any other says to ask the operator. A model of more than 2,000
dimensions does not fit the vector index (pgvector's HNSW), and the app sends no `dimensions`
to shorten one: text-embedding-3-large's 3,072 do not fit. The tokens the endpoint reports
are counted for each document's embed stage and priced like a model's, by `MODEL_PRICES` under
the name `EMBEDDING_MODEL` gives (`text-embedding-3-small=0.02/0`), so their cost shows on the
document and counts toward the day's spending; the spending limits hold back the stages that
call a model, not this one, whose cost is small beside theirs. The searches' vectors are not
counted.

**Changing the model**: a new `EMBEDDING_MODEL` queues every document to be embedded again at
the next start, and until a document's turn a search by meaning compares a vector of the new
model with its old ones, which ranks it poorly; a new `EMBEDDING_DIM` needs `python -m
app.maintenance rebuild-embeddings` with the app stopped, which drops the stored vectors. Either
replaces every row of `passages`, so rebuild the vector index and vacuum after it ("Vacuum",
above), and embedding every passage again takes hours on a CPU and costs every token again
on a hosted endpoint.

## The models

`INDEX_MODEL` reads every document, to infer its sections, write summaries, pull out facts and
transcribe scanned pages; `AGENT_MODEL` answers in the Ask tab. Each is written
`provider:model`, each may have a fallback, and every one of them is a company the documents
are sent to ("What leaves the deployment").

**Pinned.** Production refuses to start with `AGENT_MODEL` or `INDEX_MODEL` unset: the default
(`google:gemini-3.5-flash-lite` in October 2026) moves with the app's releases, and Google
showed what a provider does with a model on its own schedule when, on 18 September 2026, it
stopped serving Gemini 2.5 to accounts that had not used it. It refuses as well, for any of the
four settings, an alias its provider moves to a newer model when it likes: a name ending in
`latest` (`gemini-flash-latest`, `chatgpt-4o-latest`, a `:latest` tag) and OpenRouter's
`openrouter/auto`. Behind an alias the model changes without a release of the deployment, and
with it the price (`MODEL_PRICES` is keyed by the name), the place (`DATA_REGION`) and the
answers. Name the version the provider lists, as `google:gemini-3.5-flash-lite`. Changing a
model is then a release of its own: the setting, its key, its price in `MODEL_PRICES`, a look
at `/subprocessors`, and `python -m app.maintenance models`, below.

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
everything a primary is: the subprocessor list names its company ("when the model that usually
does cannot"), `DATA_REGION` and the privacy policy's training promise cover it,
`GOOGLE_PAID_TIER` is needed for a `google:` one, its address may not be plain HTTP off the
own network, and its key must be in the environment: a start refuses a fallback that cannot be
set up, as it would its primary. It needs its price in `MODEL_PRICES`, or what it answers
counts nothing against the spending limits: production refuses `DAILY_SPEND_CAP` without one
and warns for the plans' limits. A production start without a fallback for a kind of work
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
          2 calls, 1,278 tokens in, 2,177 out; MODEL_PRICES has no price for it, so the spending limits count none
AGENT_MODEL: ollama:qwen3.5:4b
  ok      a turn with the agent's tools: called list_documents (11.5 s)
          1 call, 1,645 tokens in, 150 out; MODEL_PRICES has no price for it, so the spending limits count none
AGENT_FALLBACK_MODEL: ollama:gemma3:4b
  FAILED  a turn with the agent's tools: The request to gemma3:4b failed (400). The provider's error code: invalid_request_error. (0.0 s)
ollama:gemma3:4b did not answer as the app asks; until it does, the work above fails or falls back. Fix the setting or the key, or name another model.
```

(Ollama refuses tools for gemma3, so that fallback would have failed every question it was
given.) It costs a few thousand tokens per model. Run it after changing a model or a key, before
launch, and monthly: beside the restore drill's timer ("The drill"), a unit that runs it and
mails on failure keeps a fallback from going stale unseen.

**Every morning, from GitHub.** The Models workflow (`.github/workflows/models.yml`) runs the
same command at 05:47 UTC, and by hand from Actions, Models, Run workflow, once the repository
names the models: the variables `AGENT_MODEL` and `INDEX_MODEL`, with `AGENT_FALLBACK_MODEL`,
`INDEX_FALLBACK_MODEL`, `DATA_REGION` and `MODEL_PRICES` as the deployment sets them, and the
keys their providers need as the secrets `GOOGLE_API_KEY`, `ANTHROPIC_API_KEY`,
`OPENROUTER_API_KEY` or `OPENAI_API_KEY` (Settings, Secrets and variables, Actions, or `gh
variable set AGENT_MODEL --body google:gemini-3.5-flash-lite` and `gh secret set
GOOGLE_API_KEY`). Without the two variables it is skipped. GitHub mails a failed scheduled run
to whoever last changed the schedule. It is the one workflow that holds a key, so it runs from
`main` alone and never on a pull request. Give it keys of its own, made for it at each provider
with a low spending limit, rather than production's: one that leaks from GitHub is then revoked
without touching the deployment, and production's stay in its secret manager. The variables
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
as `/etc/caddy/Caddyfile`:

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
    # The upload route alone takes a large body, MAX_UPLOAD_REQUEST_MB (500 by default); the
    # proxy refuses a larger one before the app has read it. Every other request is small.
    @uploads path_regexp ^/api/projects/[0-9]+/documents$
    request_body @uploads {
        max_size 500MB
    }
    @others not path_regexp ^/api/projects/[0-9]+/documents$
    request_body @others {
        max_size 1MB
    }
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
    # that route alone. The proxy refuses a larger body before the app has read it.
    client_max_body_size 1m;
    # HTTP/1.1 and no Connection header towards the app: the pool above needs both.
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    # Each request's id, in place of any the browser sent.
    proxy_set_header X-Request-ID $request_id;

    location / {
        proxy_pass http://emudoc;
    }

    location ~ ^/api/projects/[0-9]+/documents$ {
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
  authenticator secrets of two-factor sign-in, which are stored as they are. Postgres has no
  encryption of its own, so the volume under it is encrypted: LUKS on a server, or a managed
  database's encryption at rest (on RDS it is chosen when the instance is created). Its
  backups and archived WAL carry the same data and need the same.
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
  it, removing each copy when done. The temporary directory (`TMPDIR`, `/tmp` unless set) needs
  the encryption the storage has, or to be a `tmpfs`, which keeps it in memory and in the swap,
  so encrypt the swap or turn it off; the image runs with one. Python passes over a `TMPDIR` it
  cannot write in for `/tmp`, so the API and the worker say at start where the files go
  instead, and refuse to start when they can write in no temporary directory at all.
- **ClamAV** writes each file it is streamed to its own temporary directory while it scans it
  (`TemporaryDirectory` in `clamd.conf`); on a machine of its own, that disk is encrypted too.

Keys chosen per organisation are a later step; until then the bucket's default encryption and
the disks' keys are the deployment's, the same for every organisation.

## What leaves the deployment

The documents are invoices, contracts and bank statements, and three settings decide which
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
processing addendum the operator has accepted. Where the providers stood in October 2026 (read
their terms again before launch; they change):

- **The Gemini Developer API** (`google:` models, `GOOGLE_API_KEY`). Google's paid terms apply
  only while the key's Google Cloud project has an active billing account: Google then uses
  neither the prompts nor the replies to improve its products, processes them under its data
  processing addendum, and logs them for a limited time to detect abuse. A key made in AI Studio
  is on the free tier until billing is on, and on the free tier Google uses what it is sent to
  improve its products and has people read some of it. Nothing in a key or a reply tells the
  two apart, so production refuses `google:` models until `GOOGLE_PAID_TIER=true` says billing
  is on. Set it after looking at the project's billing page, not before.
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

### Keeping the data in one region

`DATA_REGION=eu` (or `us`) promises the organisations that their documents, and everything read
from them, are kept and processed in the European Union (or the United States). The European
Union means its member states: London and Zurich are in Europe but not in the Union, and the
app counts them out. It holds the deployment to the promise in three ways.

**What the app chooses itself**, on every request:

- OpenRouter (`openrouter:`) is reached at the region's own address, `eu.openrouter.ai` or
  `us.openrouter.ai`, which forwards a request only to providers in the region and fails it
  when none of them serves the model rather than send it out. Those addresses answer only
  accounts on OpenRouter's Business or Enterprise plans, so an account without in-region
  routing has every request refused there; the first answer after setting `DATA_REGION` is the
  test.
- Claude through Anthropic's own API (`anthropic:`) is told to run in the US
  (`inference_geo: "us"`) when the region is the US. Anthropic offers the US or anywhere,
  nothing else, so under the EU the app refuses a Claude model reached that way. US-only
  inference needs Claude 4.6 or later (an older model answers 400) and is priced at 1.1 times
  the standard rate, which `MODEL_PRICES` should say; Anthropic keeps what it stores at rest in
  the US whatever the setting.

**What the app reads and checks at start**: in production a piece outside the region, or one
whose place the settings cannot show, stops the API and the worker with a list of what to
change; in development each is a warning.

- Vertex AI (`google-cloud:`) runs where `GOOGLE_CLOUD_LOCATION` says, `us-central1` unless
  set: the region's multi-region (`eu`, `us`) or one of its locations, such as
  `europe-west4`. `global` runs anywhere and is refused, and so is express mode, a
  `GOOGLE_API_KEY` without `GOOGLE_APPLICATION_CREDENTIALS`, which ignores the location.
- The Gemini Developer API (`google:`) is refused under either region: Google's terms say what
  it is sent "may be stored transiently or cached in any country in which Google or its agents
  maintain facilities".
- Bedrock (`bedrock:`) keeps a model id that starts with the region's cross-region inference
  profile (`eu.anthropic.claude-…`, `us.…`) in the region's AWS regions, and runs one without a
  profile in `AWS_DEFAULT_REGION`. A `global.` profile runs anywhere and is refused.
- OpenAI (`openai:`) keeps the requests in the EU only for a project created with European
  data residency, reached at `OPENAI_BASE_URL=https://eu.api.openai.com/v1`; OpenAI decides
  which organisations may create one. The app knows no address that holds OpenAI to the US.
- A model of any other provider is refused, since nothing in the settings says where it runs,
  unless its address (`OLLAMA_BASE_URL`, `OPENAI_BASE_URL`) is on the deployment's own network.
- The bucket: an AWS bucket is asked which region it is in, so the app's credentials must
  allow `s3:GetBucketLocation`; a Hetzner bucket is placed by its address (`fsn1`, `nbg1`,
  `hel1`), and a Cloudflare R2 bucket in the EU jurisdiction by its `….eu.r2.cloudflarestorage.com`
  address. A bucket on another service cannot be placed and is refused; a directory, or a bucket
  on the deployment's own network, is where the deployment is.
- The embeddings endpoint: the compose file's, on the deployment's own network, is where the
  deployment is; a hosted endpoint is placed only at Vertex AI's addresses or OpenAI's
  European one, and any other is refused.

The places the app knows are in `backend/app/residency.py`, as the providers stood in October
2026; a location it does not know is in no region, and adding one is adding it there.

**What is the operator's word.** No setting shows where the servers and the database run.
Setting `DATA_REGION` says they are in the region, and the subprocessor list shows
`HOSTING_PROVIDER` there; a managed database or a server elsewhere breaks the promise without
the app knowing. The mail relay and the sign-in providers receive members' names and addresses,
and the alerts name organisations, projects and files, but they receive no documents and are
not held to the region: a deployment that promises one picks a relay in it (Mailgun's EU host,
an SES region in the EU) and says so in its privacy notice.

Each organisation records the region it was promised as it is created (`organizations.region`),
and one made before the deployment named a region is given it at the first start that is not
refused. A deployment that finds organisations promised another region, because `DATA_REGION`
was changed or removed under them or because it was pointed at another region's database,
refuses to start in production and says how many and which: moving an organisation to another
region is a migration of its data, never a setting. The account page shows each member the
region their organisation's data is kept in.

### The subprocessor list

The app publishes the companies it sends data to at `/subprocessors`, a page anyone can open
(the sign-up page and the user menu link to it; `GET /api/subprocessors` is the same as JSON).
It is read from the settings, so it changes with them: who hosts the deployment, the bucket
(by where its signed URLs lead), the companies behind the two models, a hosted embeddings
endpoint, the mail relay, the error tracker ("Error tracking"), and Google and Microsoft when
sign-in with them is set up. Each entry
says where the company handles what it receives when the settings show it (the bucket's region,
the Vertex AI location, "Anywhere" for the Gemini Developer API), and with `DATA_REGION` the page
opens with the region the documents are kept in. Who hosts
it is the one entry the settings cannot know: `HOSTING_PROVIDER`, free text such as
`Hetzner Online GmbH, Germany` or `Fly.io (servers) and Neon (database)`, which production
requires. A service on the deployment's own network (a private or loopback address, a compose
service's name, a name under `.internal` or `.local`) is not a subprocessor and is not listed;
a host the app does not recognise is listed by its name, so an operator's own server under a
public name appears there too. A company the app sends nothing to, such as a support desk,
belongs on the operator's own legal pages, beside a link to this one.

## Terms of service and privacy policy

The app publishes both at `/terms` and `/privacy`, pages anyone can open (`GET /api/legal` is
the same as JSON), and a new account accepts them with a ticked box at sign-up. They are
Markdown templates in `backend/app/legal/`, filled in from the settings as they are served:
who runs the deployment (`OPERATOR_NAME`, the company with its postal address, and
`OPERATOR_EMAIL`, where people write about their data), where the documents are kept
(`DATA_REGION`), how long each thing is kept on each plan, the two cookies, whether uploads are
scanned (`SCANNER_URL`), whether a model provider, the embeddings endpoint's included, may
learn from what it is sent (a `google:` model without `GOOGLE_PAID_TIER` is said to be on
Google's free tier, and a hosted embeddings endpoint is promised nothing unless it is OpenAI's,
Azure OpenAI's or Vertex AI's), and how long a deleted
account stays in the backups (`BACKUP_DAYS`, the days the deployment keeps its database and
bucket backups, 0 for none; the app makes none itself, 1.4). Production refuses to start
without the three. Where the infrastructure keeps records of connections that hold members'
addresses (a bucket's access logs, a network's flow logs), `ACCESS_LOG_DAYS` says how long, and
the policy names them with it; unset, it names none. The AWS module sets it ("The account's
records"). The texts are a starting point: have counsel read them for the operator's
country and customers before launch, and edit the templates rather than writing pages beside
them, so the facts keep coming from the settings.

Each account records the edition it accepted (`TERMS_VERSION` in `backend/app/legal/__init__.py`)
and when; the audit log records the acceptance with the client's address. A change that every
user should accept moves `TERMS_VERSION` to the day it takes effect: from the next deployment,
every account sees only a page that asks for the new edition (or offers its data and its
deletion), and the API refuses it everything else. `tests/test_legal.py` fails on any change to
the templates until that decision has been made, either way. An account made through Google or
Microsoft has accepted nothing at first and sees the same page.

The terms promise the organisations two things the app cannot do by itself: their owners are
told by email before a subprocessor is added or replaced, so mail them before changing a
setting that changes the list at `/subprocessors` (a model provider, the bucket, the mail relay,
the embeddings endpoint, `HOSTING_PROVIDER`); and a breach that affects their data is reported
to them without undue delay. The app sets only the two cookies the policy names, both needed
for it to work, and loads nothing from other sites, so it shows no cookie banner; anything that
adds an analytics or tracking cookie needs one, and a line in the policy.

## Retention and deletion

What the app removes on its own, in the worker's housekeeping round (a minute after start, then
every six hours), and so what the privacy notice can promise:

- **Chats** go once their last question is older than the organisation's plan keeps them
  (`retention_days` in `backend/app/entitlements.py`: 30 days on Free, 365 on Pro, never on
  Team and Enterprise); a chat whose answer is still being written stays until it is settled.
  Deleting a project, folder, document or chat is immediate: there is no trash yet.
- **Sessions and one-time links** go 30 days after they expired or were revoked
  (`CREDENTIAL_HISTORY` in `backend/app/auth.py`), with the address and browser a session
  recorded and the address a link went to.
- **Audit log rows** go once they are older than the organisation's plan keeps them
  (`audit_days` in `backend/app/entitlements.py`: 90 days on Free, 365 on Pro and Team, never
  on Enterprise). A row holds the client's address it was recorded from, which the privacy
  notice names with that period; an account's purge clears it from the rows of that account.
- **A deleted account** goes 14 days after its user asked (`DELETION_GRACE` in
  `backend/app/retention.py`), within six hours of the day, with every organisation it was the
  only member of: projects, documents and their files, chats, jobs and usage. In an
  organisation with other members, the user's membership, sessions and chats go and its usage
  and audit rows stay with the organisation without their name (and the audit rows without
  their addresses); the last owner of such an organisation
  cannot ask until another member is an owner, and one who became the last owner meanwhile is
  succeeded at the purge by the next member in role and seniority (logged as a warning). The
  log names accounts by id only.

Backups (and a bucket's object versions) keep a purged account until they expire, so the
privacy policy names that window, `BACKUP_DAYS`, as the outer bound of a deletion. A restore must not bring the
account back, so each purge leaves a record in the storage, `erasures/users/<id>-<created>.json`,
naming the account by its id and creation time and nothing else, and `after-restore` (or, if it
is forgotten, the first housekeeping round) purges again every account that is back (the
worker's log says "Accounts […] were purged before and are back in the database"). A restore
must leave `erasures/` as it is: restored to an older point, it forgets the purges since
("Backups", below). The records are kept for good. They say nothing about the person, and
backups may be kept for any length of time.

Logs carry no email address of the app's making: a refused sign-in names the address by its
SHA-256, as the rate limits count it, and a mail that fails is logged with its subject and the
relay's answer, every address in it replaced by `<address>`. The log mailer of development
writes every message, links included; production cannot use it, since it requires
`SMTP_URL`. The worker must run for any of this to happen: an API without one purges nothing.

## Backups

The app makes no backups itself. A deployment keeps two: the database, restorable to any moment
within the window (point-in-time recovery), and the files, through the bucket's versioning or
the disk's snapshots. Both hold every document, so both are encrypted, and both expire within
`BACKUP_DAYS`, which the privacy policy names as the latest a deleted account is gone from them.
The two must restore to the same moment: a database row whose file is gone is a document that
cannot be opened or indexed again, and a file whose row is gone is an upload nobody can reach.

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
that keeps earlier versions longer than `BACKUP_DAYS`, or for good, since the privacy policy
would then be false; a bucket without versioning while `BACKUP_DAYS` says there are backups is
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
   given a lost one's would be served its pages; ends every session and unused link, since a
   session signed out since would work again; records the restore in every organisation's audit
   log; purges again the accounts deleted since; and names the documents whose original is
   still missing, which means the storage is older than the database. Running it again is safe,
   and `after-restore --dry-run` with the same moment says first what it would do, changing
   nothing.
6. Start the app, and tell the members that what they did after that moment is undone: their
   uploads, deletions, shares, a changed password. Everyone signs in again.

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
storage, now or among the earlier versions the bucket keeps. It prints what a real
`after-restore` would do and how long each part took, and exits with 0 only when the restore is
whole:

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
  production's settings without the model keys, which the check does not use, and with the
  bucket's keys replaced by read-only ones: `s3:ListBucket`, `s3:ListBucketVersions`,
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

## Logs

The API, the worker and the release step's migrations write their log to standard error,
where Docker and systemd collect it. In production each line is one JSON object
(`LOG_FORMAT=json`, the default there; `text` gives the lines development writes):

```json
{"time": "2026-10-18T09:30:00.123Z", "level": "WARNING", "logger": "app.search",
 "message": "A search in project 7 matched keywords only: …",
 "request_id": "0f8fad5b-d9cb-469f-a165-70867728950e", "org_id": 2, "user_id": 4,
 "project_id": 7}
```

shown here on four lines. `time` is UTC, and the ids say what the line was logged about, as
far as that was known when it was written; an id not known is left out:

- **A request's** `request_id`, and once the session was read its `user_id` and `org_id`, and
  once the project was found in that organisation its `project_id` (and a document's
  `document_id`). The id is the proxy's `X-Request-ID` when it sends a well-formed one (letters,
  digits and `-_.:`, 8 to 128 of them), which both examples above do, so the proxy's access
  log and the app's lines name the same request (Caddy's `uuid` field, nginx's last); without
  one the app makes its own. Every response carries it back in `X-Request-ID`, for a member to
  quote.
- **A job's** `job_id` and `job_kind`, its `org_id` and `project_id`, and the `document_id` it
  indexes or the `message_id` of the answer it writes, with the asking member's `user_id`.
- **Neither**, for the start, the housekeeping and the alerts, which are the process's own.

uvicorn's lines go the same way, its access log included, whose lines add `method`, `path`
(the query's values hidden, below), `status` and `client`; a uvicorn `--log-config` changes
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
about as tags (`request_id`, `user_id`, `org_id`, `project_id`, `job_id`, `document_id`, …).
It holds no frame's variables (a stage's hold the document's text), no request body, cookie or
header, none of the log lines before it, and no email address; the SDK's own integrations are
off, its AI integrations above all, which would record prompts and replies, and so are its
traces, sessions and log forwarding.

An error the page renders into replaces the page with a sentence and a Reload button, and is
reported to the app (`POST /api/page-errors`, `RATE_PAGE_ERRORS` a minute per client address),
not to the tracker: the browser runs no SDK, so the page's address with the library's search
words, its requests and its console go nowhere, and the page's policy needs no other origin.
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

The tracker is a company that receives data unless it runs on the deployment's own network, so
the subprocessor list names it by its DSN's host (Sentry's EU region is a DSN under
`ingest.de.sentry.io`, in Germany; every other `sentry.io` host is its US region), and the
privacy policy says what the reports hold. `DATA_REGION` does not hold the tracker to its
region, since the reports hold no documents; under `eu`, take Sentry's EU region or a tracker of
your own, and the list says where the reports go either way. How long the reports are kept is
the tracker's setting. The DSN carries the project's key: on AWS it goes into
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
are its usage, `python -m app.maintenance usage <slug>`. A label holds the app's own words: a
model's configured name, a kind of work, a stage, a job's kind, a plan, a status, a route's
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
| `emudoc_worker_threads{lane}`, `emudoc_worker_busy{lane}` | A worker's job threads alive and running a job, by `lane` (`indexer`, `answerer`) |
| `emudoc_worker_listening` | 1 while a worker hears of queued jobs as they are queued, 0 while it polls for them: behind a pooler in transaction mode without `DATABASE_LISTEN_URL`, or while its listener's lost connection waits to be made again |
| `emudoc_upload_bytes`, `emudoc_upload_rejections_total` | The sizes of the files the API stored, and the files it refused by `reason`: `too_many` (a whole request), `plan`, `unsupported`, `empty`, `too_large`, `wrong_type`, `duplicate` |
| `emudoc_rate_limited_total` | Requests refused for a rate, by the limit's `scope` ("Rates") |
| `process_*`, `python_*` | The process's CPU, memory and open files, and Python's collector |

What the database holds, which only a worker serves, reading it when asked, at most once every
15 seconds, so that the queries grow with the workers and not with the scrapers. Every worker
serves the same figures: take one, `max without (instance)`. A worker whose reading failed
serves none of them until the next and logs why, so the series go stale rather than hold an old
value.

| Metric | What it holds |
| --- | --- |
| `emudoc_documents{status}` | Documents `queued`, `processing`, `ready`, `failed`, `quarantined` |
| `emudoc_jobs{kind,state}` | Jobs `due` (to be claimed now), `waiting` (a retry's backoff, or the next UTC day), `running`, and `failed` (kept 30 days for `failed-jobs`) |
| `emudoc_job_oldest_due_seconds{kind}` | How long the oldest due job has waited for a worker: the backlog's age |
| `emudoc_answers_in_flight` | Answers queued or being written (`MAX_ANSWERS` holds each organisation to 20) |
| `emudoc_organizations{plan}`, `emudoc_stored_bytes{plan}` | Organisations, and the bytes of the originals they keep, by plan |
| `emudoc_spent_today{currency}`, `emudoc_daily_spend_cap{currency}` | The known cost of the models' work since midnight UTC (`MODEL_PRICES`), and `DAILY_SPEND_CAP` (0 for none) |
| `emudoc_db_table_rows{table,state}` | Each of the app's tables' rows, `live` and `dead` (waiting for vacuum), as the server's statistics estimate them ("Vacuum") |
| `emudoc_db_oldest_transaction_seconds` | How long the app's role's oldest open transaction has been holding vacuum back; 0 for none |
| `emudoc_wal_archived_total`, `emudoc_wal_archive_failures_total`, `emudoc_wal_last_archived_seconds`, `emudoc_wal_segments_pending` | The server's archiving of its write-ahead log ("Backups"): the segments archived and the attempts that failed, how long ago the last went, and the finished segments waiting; none of them from a server that archives nothing (`archive_mode` off) or is in recovery |

A Prometheus on the compose file's network finds every replica by the services' names, and
keeps which kind of process each is as `process`, with `deploy/prometheus/prometheus.yml`:

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
  -v "$PWD/deploy/prometheus:/etc/prometheus:ro" prom/prometheus:v3.15.0
```

(checked with Prometheus 3.15 against the image's API and worker under those names). The file
also loads the alerting rules beside it and sends what fires to an Alertmanager: "Alerts",
below. The
counters whose labels are known in advance start at zero: every stage with every outcome, every
job's kind with every outcome, the upload refusals and the rate limits, so that `increase` sees
the first failure after a start. A model's outcomes appear with their first request, since the
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

The image runs one uvicorn process per container, which the metrics take for granted: with
uvicorn's `--workers`, each process would count apart behind one port, and the scraper would
read whichever answered.

## Alerts

Three watchers, since none of them sees everything: the worker, which sees the database and
what it reaches and mails `ALERT_EMAIL`; rules on the metrics, which see every process from
outside and need a scraper; and on AWS the host, which sees the containers.

### What the worker mails

With `ALERT_EMAIL` set and a mail relay, the worker's alerter thread reports to the address
every quarter of an hour: the jobs it gave up on ("Emudoc gave up on…"), the day's
spending at 80 % of a limit, the uploads the scan quarantined, and what its watch sees go wrong
(`backend/app/watch.py`). The watch writes once when a concern begins ("Emudoc alert: …", what
is wrong and what to do) and once when it is over ("Emudoc alert over: …", after how long),
listing what goes on meanwhile, whichever worker looked; what is raised and not over is a row of
`watch_alerts`. Every deployment has it, a scraper or not:

| Alert | When | Where to look |
| --- | --- | --- |
| Questions, or documents, wait for a worker | The oldest due question has waited 5 minutes, the oldest due document an hour | The workers' logs; more workers, or `ANSWER_CONCURRENCY`, `WORKER_CONCURRENCY` |
| Indexing fails | Of the documents whose indexing ended in the last hour, 5 and half or more have a stage that failed, by stage; one waiting for a retry is not counted until it settles, and a summary or a vector that failed counts though the document is ready | Each document's stages; the worker's log; `python -m app.maintenance models` |
| Answers fail | Of the answers written in the last hour, 5 and a quarter or more failed | The worker's log, `app.llm` and `app.answers`; `models`; a spending limit, which mails its own warning |
| `AGENT_MODEL` (or `INDEX_MODEL`) fails over to its fallback | The fallback answered a request of the role's in the last hour (the usage events name the model that answered) | The worker's `app.llm` line: "was not found, or is served no longer" is a retired model, "was rejected" a key revoked or out of credit ("The models") |
| The embeddings endpoint, the malware scanner or the storage does not answer | Asked as the start asks them, at each round | Their sentence says what failed |
| The database fails to archive its log, or its archive falls behind | The archiver's last attempt failed, or finished segments of the write-ahead log have waited past the 5-minute recovery point (`pg_stat_archiver`; a server that archives nothing has neither) | `pgbackrest --stanza=emudoc check`, the server's log ("Backups") |
| The database needs looking at | What `python -m app.maintenance database` flags, every table looked at: pgvector before 0.8, production's role without timeouts, a table autovacuum is behind on, a transaction open over an hour | "The database", "Vacuum" |

A check that cannot run (the log says "The watch could not check …") keeps the alerts it raised:
a concern that cannot be seen is not over. The worker cannot report itself gone, nor an API
that is down, nor what one process alone knows (its pool, its listener): that is the rules'
and the host's. The mails name tables, models, services and counts, never a document, a
question or an address.

### Rules on the metrics

`deploy/prometheus/alerts.yml` holds 26 Prometheus alerting rules over the metrics ("Metrics",
above), each tested against the series that should and should not fire it
(`deploy/prometheus/alerts.test.yml`, `promtool test rules`, in CI). They expect the scrape
configuration of `prometheus.yml` beside them: job `emudoc`, and `process` `api` or `worker`.
`critical` is a member waiting now or a backup losing changes, `warning` is for the working day.
The thresholds are a start; tune them, and their tests, to the deployment.

| Group | Rules |
| --- | --- |
| The processes | `EmudocProcessDown` (a target unanswered for 5 minutes), `EmudocNoWorker`, `EmudocNoApi`, `EmudocRestarting` (a process started twice again in half an hour: a worker exits when a thread ended), `EmudocWorkerNotListening` (polling for 10 minutes) |
| The work | `EmudocQuestionsWaiting` (the oldest due question over a minute, for 5), `EmudocDocumentsWaiting` (the oldest due document over half an hour, for 15), `EmudocNoDatabaseFigures` (no worker serves them: none runs, or its readings fail), `EmudocStageFailing` (a stage failed or crashed in most of its runs of the last hour, 5 at least), `EmudocStageCrashing`, `EmudocJobsGivenUp` |
| The models | `EmudocModelsDown` (no model of a role answered in 15 minutes, its fallback included), `EmudocEmbeddingsDown`, `EmudocModelRefused` (401, 402, 403 or 404), `EmudocModelFallingBack`, `EmudocModelRateLimited` (10 refusals for the rate in 15 minutes), `EmudocModelErrors` (a fifth of the requests failing on the provider's side) |
| Capacity | `EmudocPoolSaturated` (90 % of a process's connections in use for 10 minutes), `EmudocPoolTimeouts` (members answered "busy"), `EmudocStorageGrowing` (10 GiB of new originals in a day), `EmudocSpendNearCap` (80 % of `DAILY_SPEND_CAP`), `EmudocSpendCapReached` |
| The database | `EmudocWalArchiveFailing`, `EmudocWalArchiveBehind`, `EmudocVacuumBehind` (10,000 dead rows and a fifth of a table, for an hour), `EmudocLongTransaction` (over an hour) |

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
    - matchers: ['severity="critical"']
      receiver: operator
      repeat_interval: 1h
receivers:
  - name: operator
    email_configs:
      - to: ops@example.com
        from: alerts@emudoc.example.com
        smarthost: smtp.example.com:587
        auth_username: alerts@emudoc.example.com
        auth_password_file: /etc/alertmanager/smtp-password
```

```bash
docker run -d --name alertmanager --network emudoc-deploy_default \
  -v "$PWD/alertmanager.yml:/etc/alertmanager/alertmanager.yml:ro" \
  -v "$PWD/smtp-password:/etc/alertmanager/smtp-password:ro" prom/alertmanager:v0.34.1
```

(the rules checked with Prometheus 3.15 against a running API and worker: every rule evaluated
without an error, and a stopped API put `EmudocProcessDown` and `EmudocNoApi` pending; the
configuration with `amtool check-config` of Alertmanager 0.34.1). Alertmanager alone is quiet
when Prometheus is down: a dead man's switch (a rule that always fires, sent to a service that
calls when it stops hearing it) is the deployment's choice.

### The host's and the timers'

Docker starts again a container that exits, never one whose health check fails, and the
compose file's checks only mark it. On AWS the host looks every five minutes
(`emudoc-watch.timer`, `deploy/aws/host/watch`) and reports to the alerts topic a container
that is missing, stopped, restarting or unhealthy, or a one-shot that failed, once it was so at
two looks in a row, and again when every container is well; a release holds the watch off.
The drill and the credentials' renewal report their own failure (`OnFailure=`, "The drill on
AWS"). On one machine, a timer the deployment runs for its drill ("The drill") mails its
failure the same way, and a container's trouble is the rules' `EmudocProcessDown`.

## Rates

The app holds each caller to a rate of its own (the `RATE_*` settings in `.env.example`): so
many upload requests, questions and searches a minute per user, and on the sign-in routes so
many requests per email address and, with more room, per client address, the one uvicorn read
from `X-Forwarded-For`. The request past a rate is refused with 429, a sentence and a
`Retry-After` of the seconds until the minute turns, and the counts live in the database, so
every API replica counts the same. The proxy needs no rate limit of its own. One added anyway
(nginx's `limit_req`, Caddy's rate-limit plugin) sees addresses, not users, so it must allow
more than `RATE_SIGN_INS_PER_ADDRESS` a minute, or an office behind one address is held
before the app would hold it.

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
```

The directories:

- `account/`, once per AWS account: the bucket the environments' state is kept in
  (`emudoc-state-<account>`), the KMS key OpenTofu encrypts that state with before it leaves the
  operator's machine (`alias/emudoc-state`; the state holds the database's password and SES's
  SMTP key), the image repository, `emudoc` in ECR, whose tags are immutable and scanned as
  they are pushed, and GitHub's sign-in with the role CI pushes with, the one role the
  repository takes images from ("Releases from CI"), and the account's trail, CloudTrail in
  every region into a bucket of its own ("The account's records"). Its own state holds no
  secret and stays where it is applied; its tests are `tofu test` there.
- `modules/environment/`, an environment, with its tests (`tofu test` there, against a mocked
  AWS: what each resource is made with, and the inputs it refuses).
- `shared/`, the settings both environments take: the operator, the models and their prices,
  `BACKUP_DAYS` and `DATA_REGION`, and the GitHub repository CI runs in. Staging names the
  models production does, which is what pinning them is for.
- `staging/` and `production/`, the two roots. They differ in the domain, the sizes (a
  `t3a.large` and a `db.t4g.small` without a standby against a `t3a.xlarge` and a
  `db.t4g.medium` with one), deletion protection and the spending cap.
- `push-image.sh` and `release.sh`, which CI runs; `compose.yml`, `Caddyfile` and `host/`,
  which the image carries and each release runs on the instance.

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
  `BACKUP_DAYS` until it is deleted by hand: do so once it is not needed, since the privacy
  policy promises the backups end within those days. `DATABASE_URL` asks for `verify-full`
  against RDS's own authority, which no public bundle holds: each release fetches the region's
  bundle from `truststore.pki.rds.amazonaws.com` into `/etc/emudoc/rds-ca.pem`, which the
  containers mount, and keeps the copy it has when the trust store cannot be reached.
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
  downloaded again, the same file by its SHA-256.
- **Credentials**: the instance metadata service answers the host alone (IMDSv2, one hop), so
  no container holds the instance's role, which reads the settings, pulls the image and runs
  the drill. ClamAV, which opens hostile files, and llama.cpp's server hold no credentials at
  all. The API
  and the worker reach the bucket with the app role's, which allow the bucket and nothing else:
  the host renews an hour's session of it every quarter of an hour into
  `/run/emudoc/aws/app` (`emudoc-credentials.timer`), and boto3 reads the file again whenever
  the session it holds nears its end. The drill's dry run gets the storage reader's, which
  can only list the bucket.
- **Mail**, when the root names a `mail` domain: SES with the domain verified by DKIM, and SMTP
  credentials that may send from it and do nothing else, written into `SMTP_URL`. A new SES
  account sends only to verified addresses until AWS grants it production access, which is
  asked for by hand in SES's console. Without `mail`, `SMTP_URL` and `MAIL_FROM` go into the
  secrets path for a relay of the deployment's own.
- **DNS**: with `dns_zone_id`, the domain's record and the DKIM records are made in Route 53;
  otherwise `tofu output dns_records` lists them for the DNS kept elsewhere. Caddy asks Let's
  Encrypt for the certificate once the name leads to the instance.
- **Alerts**: an SNS topic the instance reports a failed drill, a failed credentials renewal
  (the containers lose the bucket within the hour), a failed start or a container in trouble
  to (`emudoc-watch.timer`, "Alerts"), naming the unit or the services and the instance, never a
  log line. `alert_email` subscribes an address, which must confirm it, and is the app's
  `ALERT_EMAIL` too, where the worker mails what it gave up on and what its watch sees, through
  the mail relay.
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
  did, and nothing of a member's, whose requests reach no API it records.
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
  `shared/`), which the module writes as `ACCESS_LOG_DAYS`, from which the privacy policy names
  them and the period. The bucket's rule removes them two days before, as the documents
  bucket's does for `BACKUP_DAYS`, and the bucket keeps no versions, so a removed log is gone.
  S3 delivers access logs within a few hours, as best it can, and charges only for their
  storage, where CloudTrail's data events for the documents bucket would cost per request,
  every page a member opens among them. No role of the app's reaches the bucket.
- **Database Insights**, in its standard mode on the RDS instance: the database's load by
  wait, statement and host over the last 7 days, without charge, in CloudWatch's Database
  Insights. A statement is shown as the app sent it, with `$1` for every value, since the app
  binds them all, and the one host is the instance. `python -m app.maintenance database`
  remains the check of the server's settings and vacuum ("The database").

Without AWS, `ACCESS_LOG_DAYS` is how long the host's own records of connections are kept (a
firewall's log, a provider's flow logs, a bucket's access logs), and unset, the privacy policy
names none.

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
places, or a value with a line break, stops it. A fresh checkout's plan refuses `OPERATOR_NAME`,
`OPERATOR_EMAIL`, `AGENT_MODEL` and `INDEX_MODEL` left empty in `shared/main.tf`, which
production cannot start without; an empty value there is a setting left unset. Keys never go
into the roots: the plan refuses a name that ends like one, and `SENTRY_DSN`, which carries the
tracker's key. The module writes `SENTRY_ENVIRONMENT`, the environment's name, itself, and
`ALERT_EMAIL` from `alert_email`. A changed setting reaches the containers with the next
release (the same tag again does).

### Setting it up

1. Once per account: `tofu -chdir=deploy/aws/account init`, then `apply`, with credentials of
   the account's administrator. Keeping staging and production in accounts of their own isolates
   them further; the module is the same, and the account root runs in each, but CI pushes to
   one repository and the second account's instance would need leave to pull from it, which
   the module does not give.
2. Fill in `shared/main.tf` and each root's domain, zone, `mail` and `alert_email`, then
   `tofu -chdir=deploy/aws/staging init -backend-config=bucket=emudoc-state-<account>` and
   `apply`.
3. Put the keys into the secrets path as above: each model's provider and each fallback's, the
   OAuth clients' secrets, and `SMTP_URL` without `mail`.
4. Make the DNS records if the zone is not in Route 53, ask SES for production access, and
   confirm the alerts' subscription.
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
timer, `emudoc-restore-drill` and its timer, and `emudoc-alert@`.

### Releases from CI

What an environment runs was built by CI from a commit on `main`, after every check passed;
no one releases from their own machine, and no AWS key is kept in GitHub.

```text
  push to main ─▶ CI ─┬─ Backend ×2, Frontend, OpenTofu, Scripts, Secrets ──────┐
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
- **Production**, and staging again (a changed setting reaches the containers with a release
  of the same tag), are the Release workflow's: Actions, Release, Run workflow, from `main`,
  with the environment and the commit, its seven-digit SHA as GitHub shows it or the whole.
  `release.sh` refuses a commit staging never ran. Going back is the same with an earlier
  commit.
- **Credentials**: each job that reaches AWS asks GitHub for a token naming the repository and
  the branch, which AWS trades for an hour of a role through GitHub's OpenID Connect provider
  (the account root makes it). The push role (`emudoc-github-push`, account root) may push to
  the repository and nothing else; each environment's release role
  (`emudoc-<environment>-github-release`, `modules/environment/release.tf`) may find its
  instance, have Systems Manager run the release there, follow it, record the tag and read
  `APP_URL`, and production's also reads staging's release history. Each trusts a job of a
  workflow on `main` of `github_repository` (`shared/`) alone: a pull request's token, or
  another branch's, is refused.
- **The repository takes images from CI alone**: its policy denies the pushes to every
  principal but the push role, the account's administrators included, so an image built on
  someone's machine cannot reach an environment. While GitHub cannot run the workflows, an
  operator names their own role in the account root's `pushers` (`tofu apply -var
  'pushers=["arn:aws:iam::<account>:role/<role>"]'`), pushes and releases with the two scripts
  from a clean checkout of the commit, and applies the root again without it afterwards.
- `tofu apply` stays the operator's: it needs rights over IAM, KMS and the state, which no
  workflow should hold while anyone who can push to `main` controls what the workflows run.

The repository variables, from the roots' outputs once they are applied (Settings, Secrets and
variables, Actions, Variables, or `gh`):

```bash
gh variable set AWS_PUSH_ROLE --body "$(tofu -chdir=deploy/aws/account output -raw push_role)"
gh variable set AWS_STAGING_ROLE --body "$(tofu -chdir=deploy/aws/staging output -raw release_role)"
gh variable set AWS_PRODUCTION_ROLE --body "$(tofu -chdir=deploy/aws/production output -raw release_role)"
gh variable set AWS_REGION --body eu-central-1      # the roots' region, which is also the default
```

Without `AWS_PUSH_ROLE` CI checks the image and pushes nothing, and without it or
`AWS_STAGING_ROLE` the Staging job is skipped, so CI runs before AWS is set up. Two things wait
for a paid GitHub plan, which a private repository needs for them: protecting `main` (no push
without a pull request whose checks passed: Backend (postgres), Backend (pgbouncer), Frontend,
Image, OpenTofu, Scripts and configuration, Secrets), and a `production` environment with
required reviewers, whose release role would then trust `environment:production` instead of
`main`. Until then, anyone who may push to `main` may release, and what reaches production is
held back by staging alone.

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
- A scraper for the metrics (the containers serve them on 9464 of the compose network) and the
  rules on them ("Alerts": the worker's mail and the host's watch cover part of them), an alarm
  on the instance's own status checks, a CDN or firewall in front, IPv6, the bucket's
  replication to a second region, and alarms on the trail's events (the root user signing in,
  a change to IAM), which would send it to CloudWatch Logs as well.

It was checked without an AWS account: the module and the account root by `tofu test` against
a mocked provider and by `validate`, the instance's part on Docker 25 (Amazon Linux's) with
Compose 5.5.1, fakes for the AWS CLI and systemd, a TLS Postgres under an RDS name and MinIO
with the module's policies as session policies, and `push-image.sh` and `release.sh` with a
fake AWS CLI, a registry of their own and a health check that answered each version. The first
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
6. Sign in with a wrong password until the form says to wait a minute: the eleventh attempt
   does (`RATE_SIGN_INS`), and the API's log line names the email address only by its hash
   (`email:` and 64 hex digits), never as it was typed.
7. The worker's log says at start which scanner it uses ("Uploads are scanned by ClamAV
   1.5.4/…"), with the signature version and date after the slashes. Upload a `.txt` file
   whose whole content is the EICAR test string (any antivirus vendor publishes it; it is
   harmless, and ClamAV flags it only as a file of its own, not inside another): within a
   moment the library shows it quarantined with the verdict `Eicar-Test-Signature`,
   "Download original file" is off, and `ALERT_EMAIL` gets "Emudoc quarantined 1 upload" at the next
   quarter of an hour. Delete it.
8. Signed out, `https://emudoc.example.com/subprocessors` lists `HOSTING_PROVIDER` first, then
   the companies behind the models and, with a bucket or a mail relay, theirs: what the
   deployment's privacy notice promises, and nothing it does not.
9. The API's and the worker's logs say at start "The bucket encrypts every file it keeps at
   rest" (with a bucket), and no line from `app.storage` warns about it. Request a password
   reset: the mail arrives, and the API's log has no line saying the relay "presented a
   certificate this machine does not trust". With a database off the own network, `psql` given
   the same URL without `+psycopg` opens with `SSL connection (protocol: TLSv1.3, …)`, and
   with `sslrootcert` pointed at an authority that did not sign the server's certificate it
   refuses to connect. `psql` has the system's libpq; that the app started at all is the check
   of its own connection, which outside the image needs `SSL_CERT_FILE` ("Encryption").
10. With `DATA_REGION` set, the API's and the worker's logs have no line from `app.residency` at
    start, `/subprocessors` opens with the region and says it under "Where" for the models and
    the bucket, and the account page says "Data kept in" it. With an `openrouter:` model, the
    first answer comes back from `eu.openrouter.ai` (or `us.`); a refusal there means the
    OpenRouter account has no in-region routing.
11. On the account page, "Download your data" saves `emudoc-account-<date>.json` with your
    account, sessions and chats and no password hash. Sign up a throwaway account, delete it
    with its password: the notice arrives by mail, the page offers only to keep it, and signing
    in elsewhere shows the same. Keep it, delete it again, and check after the 14 days (or move
    `users.deletion_due_at` into the past and wait for a housekeeping round) that signing in
    with it fails, the worker's log says "Deleted account", and the bucket or directory holds
    its record under `erasures/users/`.
12. Signed out, `/terms` and `/privacy` name `OPERATOR_NAME` and `OPERATOR_EMAIL`, and the
    privacy policy says `BACKUP_DAYS`, the region and whether uploads are scanned as the
    deployment has them. Sign up a throwaway account: the box must be ticked, and the account
    page says when the terms were accepted. Any `/api` response carries `Cache-Control:
    no-store`, and signing out answers with `Clear-Site-Data: "cache"`.
13. The API's and the worker's logs say at start how long the bucket keeps a deleted file,
    within `BACKUP_DAYS`, and `pgbackrest --stanza=emudoc info` says `status: ok` and `cipher:
    aes-256-cbc` with a full backup from the last week and WAL archived up to the last minutes.
    Before launch, drill a restore on staging as "The drill" says: a document deleted after the
    moment opens again, one uploaded after it is gone from the library, and a browser signed in
    before the restore is sent to the sign-in page. `systemctl list-timers
    emudoc-restore-drill.timer` shows the monthly drill's next run, and its first one, started
    by hand, ends with "The drill passed.", reaching within five minutes of its end while
    members work.
14. Ask a question whose answer takes a while and, while it is being written, `docker compose
    -f deploy/compose.yml restart api` (or roll the API's pods): the answer pauses and goes on
    from where it was once the API is back, the API's log says "Shutting down" and "Finished
    server process" within a second of the stop, and has no `Cancel … running task(s)` line,
    which would mean a request outlasted the 8 seconds.
15. The API's and the worker's logs say at start "The database is Postgres 17.… with pgvector
    0.8.…; the role emudoc has statement_timeout 30s and idle_in_transaction_session_timeout
    5min.", with no warning from `app.postgres`, and `python -m app.maintenance database` (from
    the image, `docker compose -f deploy/compose.yml run --rm --no-deps worker python -m
    app.maintenance database`) ends with "Nothing to look at." and exits with 0. Behind a pooler
    in transaction mode, the worker's log has no line saying the threads "find queued jobs by
    polling", and an answer starts within a second of the question. From a machine outside the
    deployment's network, `pg_isready -h` the database's host gets no answer.
16. The API's and the worker's logs say at start "Passages are embedded by" the model at the
    endpoint, with no warning from `app.startup` about it. Upload a document and search the
    library for something it says in other words than its own, such as "late fees" for a
    contract that speaks of a penalty for delay: the passage is found, the document's embed
    stage is done, and the API's log has no line saying a search "matched keywords only".
17. The API's and the worker's logs say at start "Documents are indexed by" and "Questions are
    answered by" the models, each with its fallback "when it cannot answer", and no line says a
    model "has no fallback" unless the deployment keeps to one provider on purpose. `docker
    compose -f deploy/compose.yml run --rm --no-deps worker python -m app.maintenance models`
    ends with "Every model answered as the app asks it" and exits with 0, with no line saying
    `MODEL_PRICES` has no price for a model.
18. On AWS, `release.sh` ends with "is released on staging, and … is ready". On the instance
    (`aws ssm start-session`), `sudo emudoc-compose ps` shows the API and the worker healthy
    on the tag, `systemctl list-timers 'emudoc-*'` the credentials' renewal within the quarter
    hour and the drill's next run, and `sudo docker run --rm <image> python -c 'import boto3;
    print(boto3.Session().get_credentials())'` prints `None` after a second: a container does
    not reach the instance's role. A release of a tag whose migration fails ends with "was not
    recorded", and `/api/health` answers throughout.
19. The API's and the worker's logs say at start "Metrics are served at http://0.0.0.0:9464/metrics",
    the worker's "with the database's figures". From the machine, `docker compose -f
    deploy/compose.yml exec worker python -c "import urllib.request;
    print(urllib.request.urlopen('http://127.0.0.1:9464/metrics').read().decode())" | grep
    '^emudoc_documents'` lists the documents by status; from outside the deployment's network
    the port gets no answer, and `https://emudoc.example.com/metrics` is the page, not them.
    Upload a document and ask a question: the scraper shows `emudoc_stage_runs_total` with
    `outcome="done"` for every stage and `emudoc_model_requests_total` with `outcome="ok"`, and
    no series names a member, an organisation or the question.
20. With `ALERT_EMAIL` set, the worker's log has no line saying it is not, and no line from
    `app.watch` saying it "could not check" something. Stop the scanner (`docker compose -f
    deploy/compose.yml stop clamav`): within the quarter of an hour the address gets "Emudoc
    alert: the malware scanner does not answer", and after `start clamav` and the next round
    "Emudoc alert over: …". With a scraper, its rules page lists the 26 rules of
    `deploy/prometheus/alerts.yml` without an error and none firing; with the API stopped,
    `EmudocProcessDown` fires after 5 minutes. On AWS, `systemctl list-timers emudoc-watch.timer`
    shows the next look, and stopping a container for ten minutes (`sudo emudoc-compose stop
    clamav`) reaches the alerts topic, and its start again "the containers are well again".
