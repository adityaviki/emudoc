# Paperless

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

When working on the frontend, run `pnpm dev` in `frontend/` and open <http://localhost:5173>;
it reloads on change and proxies `/api` to the backend.

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

## Tests

```bash
cd backend && uv run pytest          # needs the database container
cd frontend && pnpm lint && pnpm build
```

The backend tests use a separate `paperless_test` database and make no network calls: the AI
model and the embedding model are replaced by stand-ins, and the requests to Gemini, Claude and
OpenRouter are checked through their real SDKs against recorded responses.

## Layout

```text
backend/app/
  parsing/      PDF, Word, text and Markdown into blocks
  indexing/     structure, enrich, the pipeline and its background worker
  search.py     hybrid search over passages
  agent/        the five tools and the tool-use loop
  api/          HTTP endpoints
  llm.py        every call to the AI model
frontend/src/
  pages/        Projects, Documents, Search, Ask, Facts and the document viewer
  api.ts        the typed client for the backend
  chat.ts       a chat's messages as the Ask tab holds them, and the answers being streamed
```

## Not built yet

- Users and permissions. The app is single-user and has no login.
- Schema migrations. Tables are created on start; after changing a table, reset the database
  with `docker compose down -v`.
- More file types (spreadsheets, slides, email), and reading order for multi-column PDFs.
