# Changelog

What changed for the people who use Emudoc (**Members**) and for those who run it
(**Operators**), newest first, by the day the change reached `main`. A release is a commit: its
full SHA is the image's tag, the version `/api/health` reports and the user menu shows, so
`git log <released>..<to release>` lists the commits a release adds, and this file says what
they change. A commit that changes what a member or an operator sees adds its line here, under
its day, in the same commit.

## 2026-10-17

Members:

- The user menu shows the version of Emudoc that runs.
- When Gemini cannot be reached, questions and documents go to the second model, as they did
  when Gemini was down or refused them.
- A file that needs more memory to parse than allowed says so and suggests splitting it, where
  it sometimes said that it could not be parsed.

Operators:

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
