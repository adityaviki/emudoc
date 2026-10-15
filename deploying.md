# Deploying Emudoc

How to run the app on a server. The README's "Run it" is the development path, one `uvicorn`
on one machine; this is what changes for a deployment. There is no Dockerfile yet, so the
processes below run from a checkout with `uv`, as in development.

## The pieces

- **Postgres 17 with pgvector**, named in `DATABASE_URL`. The schema is applied as a release
  step, before the new version starts: `uv run alembic upgrade head` in `backend/`. A process
  that finds the database behind its migrations refuses to serve and says so. A database off
  the deployment's own network, such as a managed one, is reached with its certificate
  checked: "Encryption", below.
- **Storage** for uploads and page images, named in `STORAGE_URL`: a directory the API and
  every worker can reach, or an S3-compatible bucket with the `AWS_*` variables.
- **The API**: `uvicorn app.main:app` in `backend/`, serving `/api` and the built frontend
  (`pnpm build` in `frontend/` before it starts). It listens on the loopback address and a
  reverse proxy in front of it ends TLS, below.
- **The worker**: `uv run python -m app.worker` in `backend/`, as many as the backlog needs. It
  reads the same settings as the API and refuses the same misconfigurations.
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
- **An SMTP relay** in `SMTP_URL` for the sign-in links, and an embeddings endpoint in
  `EMBEDDING_BASE_URL` for search by meaning.
- **The model providers** named by `INDEX_MODEL` and `AGENT_MODEL`, each on terms that forbid
  training on what it is sent: "What leaves the deployment", below.
- **A region**, if the deployment promises one: `DATA_REGION=eu` or `us` holds the models, the
  bucket and the embeddings to it, and every server and database must be in it too: "Keeping
  the data in one region", below.
- **The operator's word** for the terms of service and the privacy policy: `OPERATOR_NAME`,
  `OPERATOR_EMAIL` and `BACKUP_DAYS`: "Terms of service and privacy policy", below.

The settings arrive through the process environment or a secret manager, never a `.env` file:
`ENVIRONMENT=production` makes the app copy nothing from one, and makes it refuse to start with
development's conveniences (the compose database's password, `LOCAL_ACCOUNT=true`, no
`STORAGE_URL`, `APP_URL`, `SMTP_URL`, `SCANNER_URL`, `HOSTING_PROVIDER`, `OPERATOR_NAME`,
`OPERATOR_EMAIL` or `BACKUP_DAYS`, a free-tier
Gemini key, no key for the model, with `DATA_REGION` anything outside the region, and a
connection that would carry the documents unencrypted off the deployment's own network),
naming each.
`.env.example` documents every setting; the README's "Configuration" says what the ones that
matter do.

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

A proxy that connects from another address, such as another container, needs that address
named: `FORWARDED_ALLOW_IPS=10.0.0.5`, or a network as `10.0.0.0/24`, beside the app's own
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
    reverse_proxy 127.0.0.1:8000
}
```

### nginx

nginx needs the headers set by hand, the body limit, and HTTP/1.1 with the `Connection` header
cleared towards the app: without both it opens a connection per request instead of keeping
the ones in the `upstream` block's pool. Its default `proxy_read_timeout` of a minute is
enough for an answer's stream, since the app's keep-alive comes every 15 seconds of silence.

```nginx
# The path without its query, which carries a search's words ("Logs" below), and no Referer.
log_format emudoc '$remote_addr - [$time_local] "$request_method $uri $server_protocol" '
                  '$status $body_bytes_sent $request_time';

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
  connect by the one URL. `sslrootcert=system` needs libpq 16 or later; psycopg's binary
  package brings libpq 18.
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

Inside the own network the app takes plain connections: the proxy to uvicorn on the loopback,
a compose database, Ollama, ClamAV. That network is the operator's to protect. On one machine
or one compose network nothing crosses a wire. Where it spans machines over a network the
operator does not own, such as a cloud's private network between hosts, encrypt it underneath
(WireGuard, or the provider's encrypted private networking) or encrypt those connections as
well: `sslmode=verify-full` and `https://` work inside the network as anywhere.

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
  so encrypt the swap or turn it off.
- **ClamAV** writes each file it is streamed to its own temporary directory while it scans it
  (`TemporaryDirectory` in `clamd.conf`); on a machine of its own, that disk is encrypted too.

Keys chosen per organisation are a later step; until then the bucket's default encryption and
the disks' keys are the deployment's, the same for every organisation.

## What leaves the deployment

The documents are invoices, contracts and bank statements, and three settings decide which
companies read them:

- `INDEX_MODEL` is sent the text of every document, to infer its sections, write summaries and
  pull out facts, and an image of every scanned page and uploaded image, to transcribe it.
- `AGENT_MODEL` is sent each question, the project's name and description, and every passage,
  fact, outline and list of documents the agent reads to answer it.
- `EMBEDDING_BASE_URL` is sent every passage as it is indexed and each search. The default,
  Ollama on the same machine, sends nothing out; a hosted endpoint is a company like the others.

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
- The embeddings endpoint: the default, Ollama on the deployment's own network, is where the
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
endpoint, the mail relay, and Google and Microsoft when sign-in with them is set up. Each entry
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
scanned (`SCANNER_URL`), whether a model provider may learn from what it is sent (a `google:`
model without `GOOGLE_PAID_TIER` is said to be on Google's free tier), and how long a deleted
account stays in the backups (`BACKUP_DAYS`, the days the deployment keeps its database and
bucket backups, 0 for none; the app makes none itself, 1.4). Production refuses to start
without the three. The texts are a starting point: have counsel read them for the operator's
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
naming the account by its id and creation time and nothing else, and the first housekeeping
round after a restore purges again every account that is back (the worker's log says "Accounts
[…] were purged before and are back in the database"). Restore the database on its own, and
that is all; restore the bucket too, and leave `erasures/` as it is: restored to an older
point, it forgets the purges since. The records are kept for good. They say nothing about the
person, and backups may be kept for any length of time.

Logs carry no email address of the app's making: a refused sign-in names the address by its
SHA-256, as the rate limits count it, and a mail that fails is logged with its subject and the
relay's answer, every address in it replaced by `<address>`. The log mailer of development
writes every message, links included; production cannot use it, since it requires
`SMTP_URL`. The worker must run for any of this to happen: an API without one purges nothing.

## Logs

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
front needs the same; most log the query unless told not to. The parse child prints the same
traceback as the app when a parser crashes, which the worker logs.

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

## Checking it

1. `curl -sS https://emudoc.example.com/api/health` answers `{"status":"ok"}` and the browser
   shows the certificate. Plain `http://` redirects to it.
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
   refuses to connect.
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
