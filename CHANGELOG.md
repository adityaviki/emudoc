# Changelog

What changed for the people who use Emudoc (**Members**) and for those who run it
(**Operators**), newest first, by the day the change reached `main`. A release is a commit: its
full SHA is the image's tag, the version `/api/health` reports and the user menu shows, so
`git log <released>..<to release>` lists the commits a release adds, and this file says what
they change. A commit that changes what a member or an operator sees adds its line here, under
its day, in the same commit.

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
