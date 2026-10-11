# Emudoc

Upload documents into a project, and ask an AI agent about them. Each document is parsed once,
then indexed three ways so the agent can find anything quickly and cite where it found it.

## How it works

```text
                                 ┌──> Search index ──────┐
Upload ──> Parse ──> Structure ──┼──> Hierarchical map ──┼──> Agent tools
                                 └──> Fact table ────────┘
```

Every upload runs through four stages in the background. Each stage stores its output, so any
stage can be run again on its own.

| Stage     | What it does                                                                      | Needs      |
| --------- | --------------------------------------------------------------------------------- | ---------- |
| parse     | Turns the file into ordered blocks (headings, text, tables) with page and position | nothing    |
| structure | Builds the outline from the headings and cuts sections into passages               | nothing    |
| enrich    | Writes a summary per section and document, and extracts typed facts                | AI model   |
| embed     | Embeds every passage for semantic search                                           | embeddings |

The agent answers with five tools: `list_documents` and `get_outline` read the map, `search`
reads the passage index (keywords and meaning, merged), `query_facts` reads the fact table, and
`read` returns exact source text. Answers cite passages, and each citation opens the source.

A project can hold any number of chats. Each one is saved with its answers, including the
steps the agent took and the passages it was shown, so a chat can be reopened later and an
answer keeps streaming while you look at another tab.

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
cd backend && uv run uvicorn app.main:app
```

Open <http://127.0.0.1:8000>. The backend serves the API and the built frontend.

For semantic search, run [Ollama](https://ollama.com) with `ollama pull nomic-embed-text`, or
point `EMBEDDING_BASE_URL` at any OpenAI-compatible embeddings endpoint.

When working on the app, run `./dev.sh` instead and open <http://localhost:5173>. It starts the
database, the backend (restarting when Python changes) and the frontend dev server (updating the
open page as you edit, no build needed), which proxies `/api` to the backend. Ctrl+C stops both
servers. Port 8000 still serves the last build, so it does not show your edits.

## What works without what

| Missing          | Still works                                           | Does not                                          |
| ---------------- | ----------------------------------------------------- | ------------------------------------------------- |
| Model API key    | Parsing, outlines from headings, search, the viewer   | Summaries, facts, scans and images, the Ask tab   |
| Embeddings       | Keyword search, everything else                       | Search by meaning                                 |

The header shows which of the two is available. After adding a key, restart the backend and
press "Run the model on them" in the Documents tab to enrich what was indexed without it.

## Configuration

Settings live in `.env`; `.env.example` lists them all with their defaults. The ones that matter:

- `AGENT_MODEL` and `INDEX_MODEL` name the AI models as `provider:model`. Both default to
  `google:gemini-2.5-flash-lite`. Indexing is the high-volume side and the agent answers
  questions, so the two can differ, even across providers.
- Each provider reads its own key: `GOOGLE_API_KEY` for `google:` models, `ANTHROPIC_API_KEY`
  for `anthropic:`, `OPENROUTER_API_KEY` for `openrouter:`. To switch models, change these
  lines and restart. [Pydantic AI](https://pydantic.dev/docs/ai/models/overview/) makes the
  requests, so any model it supports can be named.
- `AGENT_EFFORT` and `INDEX_EFFORT` set how hard a model thinks, for models that can.
- `EMBEDDING_MODEL` and `EMBEDDING_DIM` select the embedding model. Changing either re-embeds
  every document on the next start.
- Sign-in: `LOCAL_ACCOUNT` (true by default) lets a request without a session act as a local
  owner account, for development on one machine; `SMTP_URL` sends the verification and reset
  emails (without it they go to the log); `GOOGLE_OAUTH_CLIENT_ID` and
  `MICROSOFT_OAUTH_CLIENT_ID` with their secrets offer sign-in with those providers.

## Accounts

Signing up with an email and a password makes a user and an organisation of their own, which
they own; a verification link goes out by email. Sign-in sets a session cookie; the API lists a
user's sessions and revokes any of them, resets a forgotten password by email, and can require a
code from an authenticator app at sign-in (with recovery codes for a lost phone). Google and
Microsoft accounts sign in through OpenID Connect and are linked to an existing account only when
both sides have verified the address. The routes are under `/api/auth`; the pages that use them
are not built yet, so by default a request without a session still acts as the local owner.

## Tests

```bash
cd backend && uv run pytest          # needs the database container
cd frontend && pnpm lint && pnpm build
```

The backend tests use a separate `emudoc_test` database and make no network calls: the AI
model, the embedding model, the mailer and the identity providers are replaced by stand-ins, and
the requests to Gemini, Claude and OpenRouter are checked through their real SDKs against recorded
responses.

## Layout

```text
backend/app/
  parsing/      PDF, Word, Excel, CSV, text and Markdown into blocks
  indexing/     structure, enrich, the pipeline and its background worker
  search.py     hybrid search over passages
  folders.py    the folder tree as paths and subtrees
  agent/        the five tools and the tool-use loop
  accounts.py   who a request acts as; signing up makes a user and their organisation
  auth.py       passwords, sessions, one-time links and authenticator codes
  oidc.py       sign-in with Google and Microsoft
  mail.py       the emails the app sends
  api/          HTTP endpoints
  llm.py        every call to the AI model
frontend/src/
  pages/        the Projects page and the Workspace (two panes)
  workspace/    the library, tabs, document viewers and the chat pane
  api.ts        the typed client for the backend
  chat.ts       a chat's messages as the chat pane holds them, and the answers being streamed
```

## Not built yet

- The sign-in pages. The API signs users in (see Accounts) and scopes every route to the
  caller's organisation and role (viewers read, members change, a chat is private until
  shared), but the frontend has no login, sign-up or verification pages yet, so it runs as the
  local owner account.
- Schema migrations. Tables are created on start; after changing a table, reset the database
  with `docker compose down -v`.
- More file types (slides, email, legacy .doc and .xls), and reading order for multi-column PDFs.
