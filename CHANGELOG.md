# Changelog

What changed for the people who use Emudoc (**Members**) and for those who run it
(**Operators**), newest first, by the day the change reached `main`. A release is a commit: its
full SHA is the image's tag, the version `/api/health` reports and the user menu shows, so
`git log <released>..<to release>` lists the commits a release adds, and this file says what
they change. A commit that changes what a member or an operator sees adds its line here, under
its day, in the same commit.

## 2026-10-20

Members:

- In a large project, search finds more of the passages closest in meaning to what you typed:
  in one of 30,000 documents, 93 % of the ten closest where it found 83 %. It also stays as
  quick when many other projects share the deployment, where a large project's searches could
  slow to a third of their speed.

Operators:

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
