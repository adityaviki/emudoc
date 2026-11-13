# Load

What one machine carries, as the load test measures it, and what the figures say about the
settings that size a deployment. The self-hosting guide's "Load" (`deploying.md`) gives the
summary; this page is the evidence, for whoever changes what the figures measure.

The load test is `python -m loadtest` in `backend/`
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

## The figures

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
  index). The worker's processes held 2.6 GB. Bounded later that day (deploying.md, "Large documents"), it
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
each for its project, as every search is now (deploying.md, "The database"), they stayed at 100 to 215 ms.

## What the figures mean

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
  an upload of about 2,800 documents is ready within the 10 minutes of the objective (deploying.md, "Service
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
  past `PARSE_MAX_CHARS` is refused in the parse (deploying.md, "Large documents"). What one within it costs
  is its passages: written, indexed and embedded, 3 to 4 ms each in the database and the
  endpoint's time on top, a minute or two for one at the limit here, and longer with an
  embeddings server on a few CPUs.
- **Pages** are drawn two at a time by each API, about 0.3 s each for a page of this scan, so
  four asked at once wait about 0.6 s. From a bucket the API copies the original, here 62 MB,
  once for the pages that follow (`RENDER_CACHE_MB`, deploying.md, "Drawing pages"), so a page waits about
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
