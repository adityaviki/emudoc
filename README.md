# Emudoc

Emudoc keeps your team's documents in projects, with an AI agent that reads them for you. Put
contracts, invoices, delivery notes and price lists into a project and ask in your own words:
"Does invoice 0503 apply the volume discount the contract sets?" The agent searches and reads
the documents, compares the facts read from each of them, and answers with every claim cited: a
citation opens the document at the passage it came from. Ask for the letter that follows, and it
writes it as a Word document into your own files.

It is free software (AGPL-3.0) that your team runs on its own server, with the AI models of the
provider you choose.

![A project in Emudoc: the library on the left, a contract open in the middle, the chat on the right](docs/screenshot.png)

## What it does

- **Ask** about a whole project, a folder or a few chosen documents. The agent lists the
  documents, reads their outlines, searches the passages by their words and their meaning, reads
  what it found, and cites what it used. Chats stay private until you share them with the
  project; an answer or a whole chat downloads as Word, PDF or Markdown.
- **Read almost anything**: PDF, Word, Excel, PowerPoint, RTF, HTML, CSV, text and Markdown,
  photos and scans, emails with their attachments, and ZIP archives.
- **Compare**: as each document is indexed, its parties, identifiers, dates, amounts and terms
  are read, each with the words it came from. Ask "which invoices are over 5,000 EUR?", or to
  check an invoice against its purchase order, the contract and the price list: the agent
  queries the facts across the documents and cites where each figure is written.
- **Create**: ask in a chat for a letter, a reply, a memo or a report, to whatever outline you
  give, and the agent writes it as a Word document into your My files, which only you see; it
  makes and edits Excel workbooks there too. Every change it makes is kept as a version.
- **Work together**: one workspace with roles (viewer, member, admin, owner), private projects,
  notices, and links that show a document or an answer to someone outside.
- **Bring documents in** by upload, with the upload client that sends a folder from a computer
  or watches one, by mail to a project's own address, from Google Drive, Dropbox or OneDrive
  folders, or through the API.
- **Run it yourself**: one image and one compose file, a setup page on the first visit, backups
  in one command, an Administration page for the jobs, the services and what the models cost, a
  daily spending cap, an audit log, a versioned API with keys, and webhooks.

A new workspace starts with a sample project, a supplier's contract with three of its invoices,
the price list, the delivery log, an email and meeting notes, so the first question can be asked
before anything is uploaded.

## How it works

Every upload is indexed in the background by a worker, in five stages, each stored so that it
can be run again on its own:

| Stage | What it makes | Needs |
| --- | --- | --- |
| scan | a malware check; a flagged file is quarantined | ClamAV |
| parse | the file as headings, text and tables, with their pages | nothing |
| structure | the outline, and its sections cut into passages | nothing |
| enrich | a summary of each section, and the document's facts | the indexing model |
| embed | a vector for each passage, for search by meaning | the embeddings server |

Parse and structure are all a document needs to be found by its words. Without the model or the
embeddings server it is indexed without enrich or embed, and "Index the rest" finishes it once
they are back; without ClamAV it says it was not scanned
([What works without what](#what-works-without-what)).

The stages build three indexes in Postgres: the passages, searched by their words and their
meaning at once (full-text search and pgvector, merged); each document's outline with its
summaries; and the facts. The agent answers with five read-only tools over them,
`list_documents`, `get_outline`, `search`, `query_facts` and `read`, each held to the chat's
project by the server, never by the model. What a tool returns reaches the model as data, so a
document that tells the agent what to do is reported, not obeyed.
[`docs/architecture.md`](docs/architecture.md) says how the rest works and where the code is.

## Quick start

You need a Linux server (amd64 or arm64) with Docker and Compose, a name in DNS that leads to
it, and ports 80 and 443 open to the people who will use it. 8 GB of memory is comfortable, and
two cores serve a small team.

1. Fetch the compose file and its settings into a folder of their own, and make the database's
   password and the key the database's secrets are encrypted with:

   ```bash
   mkdir emudoc && cd emudoc
   curl -fsSLO https://raw.githubusercontent.com/adityaviki/emudoc/v0.2.0/deploy/compose.yml
   curl -fsSL -o .env https://raw.githubusercontent.com/adityaviki/emudoc/v0.2.0/deploy/.env.example
   chmod 600 .env && sed -i "s|^POSTGRES_PASSWORD=$|&$(openssl rand -hex 24)|; s|^ENCRYPTION_KEYS=$|&$(openssl rand -base64 32)|" .env
   ```

   Keep a copy of `.env` away from the server and its backups: they cannot be read without its
   `ENCRYPTION_KEYS`.
2. In `.env`, set:
   - `APP_URL`, the address people will open, such as `https://emudoc.example.com`;
   - a model provider's key: `GOOGLE_API_KEY` for the default Gemini models, with
     `GOOGLE_PAID_TIER=true` once billing is on for the key's project, or another provider's
     ([Models](#models));
   - `COMPOSE_PROFILES`, the services to run beside the app: `tls` for Caddy to answer on 80
     and 443 with a certificate from Let's Encrypt (left out behind a proxy of your own), `scan`
     for ClamAV to scan every upload, and `office` for Word, PowerPoint and Excel files shown as
     they look, such as `COMPOSE_PROFILES=tls,scan,office`, each with the line its comment in
     `.env` names.
3. Start it:

   ```bash
   docker compose up -d --wait
   ```

   This starts Postgres with pgvector, applies the migrations, and starts the API, a worker and
   the embeddings server, which downloads its model (334 MB) the first time.
4. Open `APP_URL`. A new install opens its setup page, which makes the first admin and the
   workspace: set it up before the address is public. Then invite the team from the workspace's
   page.
5. Back it up every night: `docker compose run --rm backup`, run from a timer, writes the
   database and the files into a new folder of the backups volume. Copy the backups off the
   server, and try a restore once.

`docker compose logs -f api worker` shows what they do; every start logs what the install runs
without and refuses a setting that would not work, naming it. The image is
`ghcr.io/adityaviki/emudoc`, published for each release; to run one you built from a checkout
([`docs/development.md`](docs/development.md), "The image"), name it in `.env` as
`EMUDOC_IMAGE=emudoc:<commit>`.

The self-hosting guide, [`deploying.md`](deploying.md), takes it from there: the models, a
proxy of your own, sign-in with Google or Microsoft, mail, connected folders, backups and
restores, upgrades, monitoring, the runbooks, and every setting the app reads.

## On your own machine

To look around before setting up a server, or to work on Emudoc, run it from a checkout. You
need Docker, [uv](https://docs.astral.sh/uv/) and [pnpm](https://pnpm.io) with Node 24:

```bash
git clone https://github.com/adityaviki/emudoc.git && cd emudoc
docker compose up -d --wait   # Postgres with pgvector, on 127.0.0.1:5437
cp .env.example .env          # then add a model's key, GOOGLE_API_KEY by default
./dev.sh                      # the backend on :8000 and the frontend on :5173
```

Open <http://localhost:5173>, where the setup page makes your account and the workspace.
[`docs/development.md`](docs/development.md) says the rest: search by meaning on your machine,
the tests and CI.

## Models

Emudoc uses two models through their providers' APIs: the indexing model reads each document
once, for its summaries, its facts and the text of scanned pages, and the agent model answers.
Each is named as `provider:model`, with a fallback of another provider for when the first cannot
answer:

- `google:` for the Gemini API (`google:gemini-3.5-flash-lite` is the default for both), or
  `google-cloud:` for Vertex AI;
- `anthropic:`, `openai:` or `openrouter:`;
- `ollama:`, or any OpenAI-compatible server through `openai:` and `OPENAI_BASE_URL`.

Your team pays its provider directly. Emudoc counts the tokens and what they cost, shows them to
the workspace's admins, and stops for the day at a spending cap if you set one.

Search by meaning uses EmbeddingGemma, run beside the app by llama.cpp's server from the compose
file, so passages and questions are embedded on your own server.

### What leaves your server

- The indexing model's provider is sent the text of every document, and an image of every
  scanned page and photo.
- The agent model's provider is sent each question and what the agent reads to answer it.
- The rest receive less, and only once you set them up: a bucket the files are kept in, a mail
  relay (the mail the app writes), the provider of a project's own address (the mail sent to
  it), Google or Microsoft for sign-in (who signs in), and an error tracker (what the log shows
  of an error, never a document's text).
- Have I Been Pwned is sent the first five characters of the SHA-1 of each new password, never
  the password; `PWNED_PASSWORDS_URL` left empty turns that off.

Choose model providers whose terms forbid training on what they are sent:
[`deploying.md`](deploying.md), "What leaves the deployment", says where each stood. The app sets
only the two cookies it needs and loads nothing from other sites.

## What works without what

| Missing | Still works | Does not |
| --- | --- | --- |
| A model's key | Parsing, outlines from headings, search, the viewer | Summaries, facts, scans and photos, the Ask tab |
| The embeddings server | Keyword search, everything else | Search by meaning |
| ClamAV (`scan`) | Everything; each document says it was not scanned | Malware scanning of uploads |
| The converter (`office`) | Everything; Office files are shown by their text | Word, PowerPoint and Excel files shown as they look |
| A mail relay | Everything; admins hand invitation and password links on, notices stay in the app | Mail |

The Ask tab says when the model is out of reach, and a search when it matched keywords only.
Members are told only what they cannot do for now; the reason is in the log, the metrics and the
alerts. After adding a key, restart and press "Index the rest" in the library to finish what was
indexed without it. A production start refuses to run without a model or the embeddings server,
and warns about the rest.

## Documentation

- [`deploying.md`](deploying.md): the self-hosting guide, from the install to the runbooks, and
  every setting the app reads.
- The help pages, in the app's help menu: how members use Emudoc, filled in for your install.
- [`docs/architecture.md`](docs/architecture.md): how it works and where the code is.
- [`docs/development.md`](docs/development.md): running it from a checkout, the tests and CI.
- [`CHANGELOG.md`](CHANGELOG.md): what each release changes, for users and for admins.
- The versioned API's contract, at `/api/v1/openapi.json` on any install.
- [`CONTRIBUTING.md`](CONTRIBUTING.md): how to contribute; [`SECURITY.md`](SECURITY.md): how to
  report a vulnerability; [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md).

## Licence

Emudoc is free software under the GNU Affero General Public License, version 3 only
([`LICENSE`](LICENSE)); [`NOTICE`](NOTICE) names the other projects' software it uses. The help
menu's "Source code" offers the running version's source to whoever uses an install, as the
licence asks. Contributors sign the contributor licence agreement ([`CLA.md`](CLA.md)), which
`CONTRIBUTING.md` explains. The name and the logo are not covered by the licence:
[`TRADEMARKS.md`](TRADEMARKS.md) says how they may be used.
