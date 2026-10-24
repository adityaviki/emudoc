# Emudoc

Upload documents into a project, and ask an AI agent about them. Each document is parsed once,
then indexed three ways so the agent can find anything quickly and cite where it found it.

## How it works

```text
                                 ┌──> Search index ──────┐
Upload ──> Parse ──> Structure ──┼──> Hierarchical map ──┼──> Agent tools
                                 └──> Fact table ────────┘
```

Every upload queues a job that runs it through four stages in the background. Jobs are rows in
Postgres that the worker claims with a lease, so a job whose worker died is picked up again by
the next one; and a worker that lost its lease stops at its next commit with nothing of that
stage written, so two workers never write the same document. The workers are shared between
organisations: a claim takes the job of the organisation that has had the least of them lately
(jobs running or finished in the last five minutes), so one organisation's upload of five
hundred documents costs another's single upload one slot, not five hundred. Uploads go before a
project reprocess regardless. Each stage stores its output, so any stage can be run again on
its own. A stage
that failed because the model, the embedding endpoint or the bucket was out of reach (down,
rate limiting, a dropped connection) is run again by itself after 1, 2, 4 and 8 minutes; the
document's status says when ("Retrying at 14:32", or "Paused until 02:00" for the day's
spending limit, with the stage's arc amber), the page asks about it again at that moment rather
than every second and a half, and after the last try it stays failed until "Index again". A stage that failed for a reason that will not pass on its own, such
as a rejected key or a file nothing can be read from, is not retried. A document read without
its summaries and facts or its search by meaning (enrich or embed failed, or skipped for want of
the model) is "Partly indexed", with an amber check: found by its words meanwhile, and finished
by "Index the rest" on its tab or in its menu, which runs only the stages it went without, or by
the same button in the library for every such document of the project; the button waits, saying
why, while the model or the embeddings endpoint it needs is unavailable. In development the
stage's words are the operator's (the model, the endpoint's address, the setting to change); a
deployment's members read what it means for them ("The AI model is unavailable just now.", a
document "found by its words alone") and nothing of how the deployment is set up, and so does an
answer that failed, while the operator reads the rest in the log. A job the worker gave up
on, after the last retry, after a crash, or after losing it to a dying process three times, is
kept as failed for a month: `uv run python -m app.maintenance failed-jobs` (in `backend/`)
lists them with the reason, and `--requeue` runs their documents again from the stage they
stopped at; with `ALERT_EMAIL` set, the worker mails the new ones there every quarter of an
hour as one digest. `jobs` shows the queue as it stands, each running job with its lease, and
with `--document <id>` where one document's indexing stands and what to do about it. A request
that gets no
answer is given up on and counts as the provider being out of reach: an indexing request after
five minutes, an embedding request after two, and a turn of an answer in the chat after two
minutes or a minute of silence, which the chat shows as an error. The parse stage runs the
parser in a process of its own with a time limit, a memory limit and caps on the pages of a PDF
and the rows of a sheet (`PARSE_TIMEOUT`, `PARSE_MEMORY_MB`, `PARSE_MAX_PAGES`,
`PARSE_MAX_ROWS`), so a file that would take hours or all the memory stops that process alone,
and the document says which limit it hit. Before any of that, the upload checks that a file's
bytes are what its name says (a PDF's header, an image's signature, the part a Word document or
a workbook declares, UTF-8 text with no binary in it) and rejects one that is not with the
reason, while the other files of the same upload go in. A Word document or a workbook saved with
a password, or in the format from before 2007 (.doc, .xls), is rejected there with what to do
in Office; a PDF that needs a password to open, or has no pages, goes in and fails to parse with
the reason (one whose password only restricts printing or copying is read like any other). An
empty file is rejected at the upload. The first stage then has the file
scanned for malware by the ClamAV daemon `SCANNER_URL` names, before any parser opens it and
before the file is served to anyone: a flagged file is quarantined, which the document shows
with the scanner's verdict, nothing of it is indexed, and no member can open or download it
until an editor deletes it (or has it scanned again with "Index again", once the signatures
have changed); the worker reports each quarantine to `ALERT_EMAIL`. A daemon that is down
holds the upload in the queue, retried like any passing failure. Without a scanner the stage is
skipped and every document says so, which production accepts only when `SCANNER_URL` is
set to `off` on purpose.

| Stage     | What it does                                                                      | Needs      |
| --------- | --------------------------------------------------------------------------------- | ---------- |
| scan      | Has the file scanned for malware; a flagged file is quarantined, not indexed       | ClamAV     |
| parse     | Turns the file into ordered blocks (headings, text, tables) with page and position | nothing    |
| structure | Builds the outline from the headings and cuts sections into passages               | nothing    |
| enrich    | Writes a summary per section and document, and extracts typed facts                | AI model   |
| embed     | Embeds every passage for semantic search                                           | embeddings |

The agent answers with five tools: `list_documents` and `get_outline` read the map, `search`
reads the passage index (keywords and meaning, merged), `query_facts` reads the fact table, and
`read` returns exact source text. Answers cite passages, and each citation opens the source.
Where drafts are on, a member who may change the project is offered a sixth, `create_draft`, by
which an answer offers a draft (below).

The facts are read for each document's kind, by extraction templates (`backend/app/extraction.py`;
being rolled out, behind the flag `extraction_templates`, and read as before where it is off). A
template is a kind of document and the fields read from it, each with a name, a type (text,
number, date or an amount of money), what it is, whether every document of the kind states it,
and examples as documents write them. Seven are built in (invoice, contract, purchase order,
receipt, bank statement, delivery note, price list), and a project may have twelve of its own: one
of a
built-in kind replaces the built-in one, for that project alone. The enrich stage lists the
project's templates to the model, which names the kind the document is and reads that
template's fields first, under the fields' names and of their types, then any other facts as
before; a long document's first part names its kind, and the parts after it are shown that
template alone. The document takes the template's kind, and its stage says which of the fields
every document of the kind states were not found in it. The library's "Extraction templates"
button, beside the trash, lists them with their fields; whoever may change the project makes a
template, customises a built-in one into the project's own, changes and deletes them, each a row
of the audit log. After a change they have the template's documents read again from their
summaries and facts ("Read them again", `POST /api/projects/{id}/templates/read-again`): those
read for it, those of its kind read for none, such as the ones read before it was made, and
after its kind moved or it was deleted, those read for the kind before, the question naming how
many. A project's export holds its own templates.

What a template's change did is then listed ("What changed", `backend/app/fact_changes.py`, `GET
/api/projects/{id}/fact-changes`): each time the enrich stage reads a document it read before,
it compares the facts of its template's fields with those the reading before left, a value read
alike being no change ("EUR 1,250.00" and "1,250.00 EUR" are one amount), and keeps for each
field the values found, no longer found or read otherwise, and whether the template the document
is read for changed. The view follows the documents while they are read, then lists those that
changed, first those whose new reading disagrees with a value a member checked or corrected,
whose check then waits in its cell of the facts table to be settled. A reading after the
document's outline was read again compares nothing, the facts having gone with the passages.

Each template with documents read for it opens its facts table from that list ("Facts table",
`backend/app/fact_table.py`, `GET /api/projects/{id}/fact-table`): a row for each of the
project's documents read for it and a column for each of its fields, in the library's place
beside the document and the chat. A cell shows the field's first fact in the document's order,
with how many more it holds (each line of an invoice), its quote and page on hover or focus,
and opens its facts, each to open where it was found in the document. A column's
header sorts the rows by the field's typed value (an amount by its number, a date by its date, a
text in the library's natural order), those without it last; the rows are kept by words in the
document's name or its facts, and by conditions on a field (stated, not stated, contains, at
least, at most), a row meeting one when any of the field's facts does. The columns are the
template as it is now, so a document read for an earlier version shows the fields it still
has. The table is one stop of Tab, the arrows moving between its cells.

Whoever may change the project checks a fact from its cell, or corrects it to what the document
states (`backend/app/fact_checks.py`, `PUT /api/facts/{id}/check`, `DELETE
/api/fact-checks/{id}`), each a row of the audit log naming the field and the document, never the
value. A correction replaces the model's value wherever the fact is read: the table sorts and
keeps rows by it, the agent is told a member corrected it and what the model read, and a
project's export holds it with the check. A check outlives the document's next reading: a fact
read the same again, or read as the member wrote it, takes it back, and one read otherwise leaves
it in its cell as made before, to forget. A cell marks facts all checked, and a fact whose quote
was not found in the text that nobody has checked; "Unchecked" keeps the rows with a fact nobody
checked, and a field's condition "is not checked" those with one of its own.

The table's "Download" saves every row it keeps, as it is sorted and kept, whatever page it shows
(`backend/app/fact_export.py`, `GET /api/projects/{id}/fact-table/export`): a CSV file of the table
as it shows, a cell's several values joined by "; " and a member's correction in the model's place,
with the document's review, then two more columns naming the fields of each row nobody has checked
and those whose quote was not found in the text; or an Excel workbook of the same rows, a lone number, amount or date typed so
that a spreadsheet adds and sorts it, and a second sheet of every fact with its page, its quote,
whether the quote was found, who checked it and when, and what the model read where a member
corrected it. The model gives no figure for how sure it is of a fact, and one it gave would not be
calibrated, so those two are what the files say of it. Each download is a row of the audit log
naming the kind, the file and the fields it was sorted and kept by, never a value. A program reads
the same with an API key ("Accounts"): the table as JSON, a page at a time (`GET
/api/projects/{id}/fact-table`, each fact with its typed value, quote, page and check), the files,
and what the last readings changed.

The library's "Cross-checks" button, beside the templates, checks the project's documents
against one another by rules (`backend/app/cross_checks.py`, `GET|POST
/api/projects/{id}/cross-checks`, `PUT|DELETE /api/cross-checks/{id}`): a field of one kind's
documents compared with a field of the same document (an invoice's due date at most 30 days after
its issue date), of the documents of another kind that state the same key (an invoice's quantities
with what the delivery notes of its purchase order say was delivered, added up), or of every
document of another kind (its prices with the price list's), the first value of each, or line by
line, each line paired with the other document's line that shares most of its words, figures
aside. Amounts, numbers and dates compare as equal, at most or at least, with a margin (days for
dates); texts by their words, or by the numbers they state, so that "30 days" and "within thirty
(30) days of the invoice date" agree. A few rules are suggested for the templates the project
has, added in one click. What a rule finds is worked out from the facts as they read when it is
shown, a member's corrections included, never by the model (`GET
/api/cross-checks/{id}/findings`): for each value checked, whether it differs, holds or misses
what it needs (the value, its key, a document to compare with, a line naming its item), by how
much, and the values on both sides, each opening its document where it is written. Whoever may
change the project makes, changes and deletes the rules, each a row of the audit log naming the
kinds and fields, never a value.

Under the rules, "Run the checks" works them all out now and keeps what they found
(`backend/app/check_runs.py`, `POST /api/projects/{id}/check-runs`): a run holds the rules as
they were and each finding as it was found, so it reads the same after the documents are read
again or the rules change, and lists the documents deleted since. The background worker works it
out, and with "With a discrepancy report by the model" checked, or later from the run, the agent
writes a report from its findings, the code's words handed to it with the passages each value
came from, which it cites as an answer does (`POST /api/check-runs/{id}/report`); asking for one
counts as one of the month's questions, and is held to the day's spending as a question is. The
runs are listed newest first under the rules, each opening its rules' findings, its report and
what the report used (`GET /api/projects/{id}/check-runs`, `GET /api/check-runs/{id}`, `GET
/api/check-runs/{id}/findings`). Whoever may change the project runs the checks, asks for a
report and deletes a run, each a row of the audit log naming the rules, never a value; a run is
kept as long as a chat.

Under them, "Automations" run the checks on their own (`backend/app/automations.py`, `GET|POST
/api/projects/{id}/automations`, `PUT|DELETE /api/automations/{id}`): when documents of a kind,
or any, arrive in a folder or anywhere in the project, an automation runs every cross-check or
those it names, with the discrepancy report when asked, and tells whoever made it, everyone who
sees the project, or nobody, in the app and by email as each chose for the notice, what they found
about the documents that arrived, opening the run. With "Run them again" checked, the documents
its checks compare those with arriving anywhere (a purchase order, a delivery note, a price list)
run it too. A document arrives once its upload is indexed, and the run waits until none has
arrived for two minutes (`AUTOMATION_WAIT`), at most a quarter of an hour after the first, so an
upload sent in several requests makes one run. An automation may also put in review what arrived
(below), every document of it or those its cross-checks find a difference about, assigned to a
member it names, once its run is worked out, leaving alone any a member put in review already. Its
run is listed with the others, by the automation and what arrived; its report counts as one of the month's questions, and a month or a
day with no room left runs the checks without one, saying why. Whoever may change the project
makes, changes, turns off and deletes them, each a row of the audit log naming what it watches
and runs, never a value, and the runs they make are rows of it by Emudoc.

A document may be put in review, the accounts payable's way with an invoice (`backend/app/reviews.py`,
`PUT /api/documents/{id}/review`): an automation as it arrives (above), or from its menu in the
library or its tab a member, who puts it "To review", assigns it to a member who sees the project and may change it, and approves or disputes
it; assigning one in no review puts it in review, and taking it out of review drops whom it was
assigned to. Whoever is assigned it by another, or by an automation, is told, in the app and by email as they chose for
the notice, opening the document. The library marks each row's review beside its status, and so
does the facts table beside a document's name; the library's "Reviews" button lists the project's
documents of one state, those still open oldest first and the approved ones newest first, every
member's or those assigned to the member, each moved and assigned where it is listed (`GET
/api/projects/{id}/reviews`, `GET /api/projects/{id}/reviewers`). A review is a member's word on
the document as a whole, never worked out from its facts: the facts table's "Unchecked" is the work
towards it, and the cross-checks inform it. Each move and assignment is a row of the audit log by
the document's name and the states or whom it was assigned to, never a value; a document in the
trash keeps its review, and a member who leaves the organisation is assigned nothing of it.

The agent also writes drafts for the members to edit and send (`backend/app/drafts.py`, `POST
/api/projects/{id}/drafts`): a reply email, a dispute letter, a summary memo, a comparison table or
a report. The library's "Drafts" asks for one of a kind, saying what it should say, about the
documents open beside the library or about what the agent finds; a disputed document's review
offers "Write a dispute letter" about it; and asked in a chat for something to send, the agent
offers one under its answer (`create_draft` in `backend/app/agent/tools.py`), its kind, the
documents it found and what it should say, which the member's "Write the draft" asks for (`POST
/api/chats/{id}/messages/{id}/draft`), once, counting no question beside the one asked. Nothing is
written before that press, so a document whose text tells the agent to make drafts makes none,
and a viewer, or a member of an organisation set to be deleted or past its plan, is offered none.
The background worker has the agent write it from what
the code read for it: each document with its facts beside the passages they came from, and what
the project's newest run of cross-checks found about them, which it states as found; it cites
passages as an answer does, and reads around them with its tools. A draft keeps every version: the
agent's, and each member's edit, saved as a new version from the newest (`POST
/api/drafts/{id}/versions`, refused when another was saved meanwhile), or the agent's writing it
again with what is asked now (`POST /api/drafts/{id}/rewrite`), each version with the passages it
cites as they were, so it reads the same after its documents are read again. Asking for one, or
for it again, counts as one of the month's questions and is held to the day's spending as a
question is. The drafts are the project's: everyone who sees it reads them (`GET
/api/projects/{id}/drafts`, `GET /api/drafts/{id}`, `GET /api/drafts/{id}/versions/{n}`), whoever
may change it asks for, edits and deletes them, each a row of the audit log by the draft's kind,
never what it says; a draft is kept as long as a chat, from when it was last written or saved.
Each version downloads as a Word document, a PDF or its Markdown (`GET
/api/drafts/{id}/versions/{n}/export?format=docx|pdf|md`, `backend/app/draft_export.py`), its
citations numbered notes naming the document, section and pages of each passage, and its line
breaks kept, a row of the audit log too. How a project's drafts of each kind read is its
template (`backend/app/draft_templates.py`, the drafts' "Templates", `GET
/api/projects/{id}/draft-templates`, `PUT|DELETE /api/projects/{id}/draft-templates/{kind}`): a
built-in one for each kind, or the project's own in its place, its tone, its parts in order, which
of them every draft has, and what else every draft of the kind should do. The kind's layout and
the rules every draft keeps, its citations and its placeholders, stay the code's whatever a
template says; a writing follows the template in force as it starts, so a draft written again
after a change follows the new one.

A project can hold any number of chats. Each one is saved with its answers, including the
steps the agent took and the passages it was shown, so a chat can be reopened later. An answer
is written by the background worker, not by the request that asked the question: the question
is saved with an empty answer and a job, the worker runs the agent and writes each event to a
table, and the page follows them from there. So an answer keeps streaming while you look at
another tab, survives a reload, a dropped connection and a restart of the API, and is picked
up where it was by another tab that opens the chat. While the browser is offline the page says
so (in the header, and under an answer it was following) and follows on once the connection is
back; a connection that keeps failing for 15 seconds online is given up with "Reconnect", which
reads the answer again from its start. Stop ends it with what arrived so far, whether or not a
worker is alive, even while the question is still being sent; a chat takes one question at a
time. The last answer of a member's own chat, once stopped or failed, offers "Try again", which
empties it and has the worker write it anew from the same question, counting no other question
against the plan; a run of the earlier try that is still waiting on the model writes nothing
into the new one, since each try is numbered and its job names its own.

A chat is its starter's alone until they share it with the project ("Share" in the chat's
header, "Make private" to take it back). Everyone who sees the project then finds it under
"Shared with the project" in the chat list, and a new chat offers the latest three, each with
the starter's name and the day of its last question: they read it and follow an answer as it
is written, but only the starter asks in it, renames it or deletes it, so the page offers the
others no composer, and a question of their own starts a chat of their own. A project opens
on the member's own latest chat, never on one another member shared. Sharing and taking
private are in the organisation's audit log, without the chat's title.

What an organisation uses is recorded as it happens, one row per measure in `usage_events`:
an answer's model calls and tokens, an upload's bytes and pages (once, by its first parse; a
document without pages, a Word file, a sheet or a text file, has a page for every 3,000
characters of its text begun, `text_pages` in `backend/app/entitlements.py`), the pages a scan took to read
and the model calls of the indexing stages, the texts embedded and the tokens the embeddings
endpoint says they took, and the pages rendered for the viewer, each with the organisation, project and member they belong to and the message or
document they were for. Each is counted at the end of the run that made it, so an answer that
was stopped, a scan that failed at its tenth page and a stage a worker lost are counted for
the calls they made. `uv run python -m app.maintenance usage <slug>` (in `backend/`) prints an
organisation's use per day in UTC, by kind and unit, over the last 30 days or `--days`, and
`costs` what every organisation's model use cost over the same span, costliest first, against
its plan's daily limit, with the platform's total and the cost by kind of work and model.
An answer shows what it used under its text (the model, its calls and tokens, and the
cost), and a document's header shows what bringing it in and indexing it used, with the
breakdown by stage on hover; the figures are kept on the message and the document as the
rows are recorded, summed over every run. The costs follow `MODEL_PRICES` in `.env`, what
each model costs per million tokens in and out, in `PRICE_CURRENCY`, an embedding model's
under the name `EMBEDDING_MODEL` gives; without prices, no cost is shown.

A day of model use has two limits, both measured against those costs since midnight UTC: the
organisation's, set by its plan (`backend/app/entitlements.py`: USD 1.00 a day on Free, 10 on
Pro, 50 on Team, none on Enterprise), and the platform's, `DAILY_SPEND_CAP`, the most the
operator will see on the providers' bills in one day. At the plan's limit a question is
refused with 402 and the plan's sentence, and an indexing stage that needs the model (reading
scans, writing summaries and facts) waits for midnight UTC and says so on the document, while
the outline is built without the model so that the document can be searched today; at the
platform's, every question is refused with 503 and a `Retry-After` until midnight, and the
stages wait the same way. A question that was queued before a limit was reached ends with the
sentence as its error. With `ALERT_EMAIL` set, the worker mails a warning once a day when a
day's spending reaches 80 % of a limit. The limits count nothing without `MODEL_PRICES`, and
they hold the next piece of work rather than the ones running, so a day can run past a limit
by what was in flight: a document's summaries and facts by `ENRICH_MAX_REQUESTS` requests at
most. The brake for a bill that cannot wait for midnight is a provider switched off: from the
operator's area, which every process heeds within seconds, or with `DISABLED_PROVIDERS` at the
next restart; its models are asked no more, and the app runs as it does without a key, saying
why. Set a budget at each provider as well (Google Cloud
billing budgets for the Gemini key, the Anthropic console's spend limits, OpenRouter's credit
limit): the app counts what it knows the price of, the provider counts the bill.

Every organisation is on a plan (`backend/app/entitlements.py`): Free, Pro, Team or
Enterprise, each a row of what the organisation may hold (projects, documents, bytes of
documents, members), may use in a calendar month in UTC (pages indexed, questions asked) and
may spend on models in a UTC day, plus what the later features will read from it (API access, connectors, how long data is
kept, the model tier) and how much of the workers its jobs get. A new organisation starts on
Free; the local development account has no limits. The Team plan is sold by the seat: the
seats an organisation bought are its members, in place of the plan's three. Any figure may
also be agreed with an organisation in place of its plan's, which is how an Enterprise
organisation gets its terms (`backend/app/billing.py` changes the three together, and moving
to another plan clears the seats and figures of the one before). Lowering any of them removes
nothing: an organisation that holds more than its plan allows keeps it all, and is read-only
until it is within the plan again (`read_only` in `backend/app/entitlements.py`): its members
read, search, ask about and delete what it holds, and its owners and admins remove members and
revoke invitations, but every other change of a project (a project, an upload, a folder, a
rename or move, "Index again", who sees it), an invitation and a join is refused, and every
page says so in a banner. The organisation page shows every member what the organisation may
keep and use, and what it holds past it, and its usage page (`/organization/usage`) what it
holds and has used of each figure against what it may, as the checks count it, with a warning
and the way past a month's limit from 80 % of it on (its owners and admins are mailed then, and
once it is all used), and its pages indexed and questions asked
on each of the last 30 days and in each of the last 12 months, in UTC. A question counts as it
is asked, answered or not, and deleting its chat gives it back to nobody. The plan is checked
where the thing happens: creating a project, uploading (the request is refused when the plan
has no room at all, a file that would not fit is rejected while the others go in), asking a
question, inviting, and in the parse stage once it knows how many pages a document has (those
of its text for a document without pages, which the refusal and the usage page say), which
fails the document with the reason rather than read a page past the month's allowance. A
refusal is a 402 whose body is the sentence, naming the plan and the limit, and the figure it
is held to (`{"detail": …, "limit": "pages_per_month"}`); the app shows it with the way past
it for whoever reads it (`offerFor` in `frontend/src/billing.ts`): the owner a pack for the
month, the plans or Manage billing, anyone else to ask an owner.

With `STRIPE_SECRET_KEY` set, the plans are sold through Stripe (`backend/app/billing.py`;
deploying.md, "Billing"): an owner buys Pro, or Team with a number of seats, on the
organisation page, which sends them to Stripe's Checkout page, and changes the plan or the
seats, the payment method, the billing address and VAT id, cancels, and finds the invoices
behind "Manage billing", on Stripe's customer portal. Another plan, other seats or a
cancellation can also be chosen on the organisation page, which says first what that would
leave read-only and which chats and audit log entries the lower plan keeps for less time, then
opens that one step of Stripe's portal, which shows the price and confirms it; the owners are
mailed a cancellation, and the lower plan once it comes. The subscription sets the plan: while it
is in its trial (`STRIPE_TRIAL_DAYS`, 14, for an organisation's first) or paid, and for
`BILLING_GRACE_DAYS` (14) after a payment failed, during which the owners are mailed how to pay;
once it ends, or the payment is not made, the organisation is on Free, keeping all it holds,
until it pays again. With `STRIPE_PACKS` an owner also buys packs of pages or questions, paid
once, which add to the plan's for the calendar month in UTC they are paid in. Checkout asks for
the billing address and a business's VAT id, and works out the tax with `STRIPE_TAX`; Stripe
mails the receipts and invoices itself, once its customer emails are on. An organisation on Enterprise or with agreed figures is left to
the operator. Without Stripe, or for those, `uv run python -m app.maintenance plan <slug> [plan]`
(in `backend/`) shows an organisation's plan and what it allows, or moves it to another, with
`--seats N`, `--set NAME=VALUE` (`projects=500`, `storage_bytes=1 TB`, `retention_days=none`)
and `--unset NAME` for its seats and agreed figures. The people who run the deployment read the
same in a browser at `/operator`, the operator's area: the organisations with their plans,
usage, members and costs, the users, the costs across organisations, the job queue with its
failed jobs, and how the models and services stand. `uv run python -m app.maintenance operators
add <address>` (in `backend/`) lets an account in, from an address `OPERATOR_NETWORKS` lists
(this machine alone in development) and, in production, with two-factor sign-in on; to anyone
else the area is not there. From there an operator also moves an organisation to another plan
with its seats and agreed figures, as the `plan` command does, queues failed documents again
from the stage each stopped at, switches a model provider off or on again without a
restart, and turns a feature being rolled out on or off for one organisation
(`backend/app/flags.py`: each flag is on for a share of the organisations the code names, which
grows release by release, and for those turned on here; `maintenance flags` does the same); a
plan's change, a document queued again and a flag's choice are rows of the organisation's audit
log, by Emudoc's operators. And when a member asks for help, the member lets Emudoc's support see
their account in the organisation they work in for a day or a week from their account page
(`backend/app/support.py`), and the operator opens it from the organisation's members, as the
member sees it and read only, an hour at a time, under a banner saying so: the member is mailed,
and the leave, its withdrawal and each view are rows of the organisation's audit log. A
paying plan's jobs also count for less in the claim's share of the workers, so its backlog
is indexed sooner next to a free organisation's.

## The workspace

A project opens as two panes. On the left is the **library**: the project's documents filed in
folders, with the usual file-manager moves (new folder, rename, drag to move, delete, upload by
button or by dropping files or folders, search), shown as a tree that opens folders in place, a
folder's files by name a hundred at a time, with more on request. A folder uploaded is filed as
it was, its folders made where the one it goes into holds none of their names. An upload goes in
as few requests as the deployment's limits allow, one after another, each its own upload for the
notice that follows it, and a panel shows how far it has gone as a whole and file by file, until
its Stop; a type Emudoc does not read and a file too large are left out before anything is
sent. Once sent, the same panel follows its documents as they are indexed ("12 of 40 ready; 26
being indexed; 2 could not be indexed."), lists those that could not be with why, each opening
in its tab, and stays until its cross or the next upload. An upload begun while the browser is offline waits and starts by itself once it is back;
one cut off on its way offers the files not sent again, a file that got through before the cut
coming back as already in the project. The last deletion, move or rename is
offered back at once with "Undo", until the next one or its cross. Opening a file adds a third pane in
the middle, the **document**, with a tab for each open file; the tabs stay open across reloads,
and closing the last one leaves the library and the agent side by side again.

Everything there works by the keyboard as well (WCAG 2.2 AA). The library is one stop of Tab:
the arrows move through its rows, which a screen reader reads out by name and state, Enter opens
one, F2 renames, Delete deletes, and Tab goes on to the actions of the row it is on, whose "Move
to…" asks for a folder in a dialog, the way to move without a drag. The tabs open with the arrows
and close with Delete, the text of a document scrolls once focused, and the dividers between the
panes move with the arrows. A click on a divider shows two buttons beside it that narrow or widen
the pane before it, for whoever cannot drag; a double-click puts the panes back to their starting
widths. Every menu and panel takes the keyboard as it opens, gives it back
to its button on Escape, and lets Tab go on past it.
PDFs show their rendered pages with the extracted text laid invisibly over them, so it can be
selected and searched; spreadsheets show one tab per sheet; images show the picture above the text
read from it. On the right is the **agent**. Clicking a citation in an answer opens the cited
document in the middle at that passage, with the library still beside it. The agent sees the
folders too: ask about "the invoices in Finance/2026" and `list_documents`, `search` and
`query_facts` work within that folder.

Deleting puts things in a **trash** rather than removing them (`backend/app/trash.py`): a file
or a folder with everything in it goes into its project's trash, where the library's trash
button finds it, and the library offers to undo the deletion at once; a chat goes into its
starter's trash, which the chat list's "Deleted chats" opens; a project goes, with everything
in it, into the organisation's trash under the projects. What is in the trash is out of the
library, the search, the agent's answers and the counts, and counts against nothing on the
plan, so deleting still makes room at once. A member restores it as it was (a folder with what
went with it, into the nearest folder that is still there, renamed if a name was taken
meanwhile), as the plan has room for it again, or deletes it for good with its files, once a
dialog has said what goes with it (the files a folder takes, what a project holds); after 30
days the housekeeper deletes it for good. A viewer reads the trash and restores only their own
chats. A document in the trash keeps its place: uploading the same file again is refused with
the way to restore it, which costs none of the indexing again, and its indexing waits until it
is restored.

## Run it

You need Docker, [uv](https://docs.astral.sh/uv/) and Node with pnpm.

```bash
cp .env.example .env                 # then add your GOOGLE_API_KEY
docker compose up -d                 # Postgres with pgvector on 127.0.0.1:5437
cd frontend && pnpm install && pnpm build && cd ..
cd backend && uv run alembic upgrade head   # creates or updates the database schema
uv run uvicorn app.main:app
```

Open <http://127.0.0.1:8000> and create an account. The backend serves the API and the built
frontend, and in development it runs the background worker too, so that one process indexes
what it serves. To skip signing in while developing on your own machine, put
`LOCAL_ACCOUNT=true` in `.env`: every request then acts as a local owner account.

In production the API runs no worker. Start `uv run python -m app.worker` (in `backend/`) as a
process of its own next to the API, as many as the backlog needs: it reads the same settings,
refuses the same misconfigurations, and claims jobs from the queue in Postgres. Ctrl+C or
SIGTERM stops it: the jobs it is running get a few seconds to finish (`--grace`, 8 by default,
under Docker's 10), and the rest are queued again for the next worker to take at once. With
`--alive-file PATH` it keeps a file fresh for a health check while all its threads run, and it
exits with an error should one of them end, to be started again. Without any worker, uploads
stay queued and questions wait unanswered: each worker says in the database that it runs, at
its start and every minute, so when none has for three minutes the header says "Background work
off" and a chat holds its question until one runs again, and `python -m app.maintenance jobs`
lists the workers seen. `RUN_WORKER=true` runs the worker inside the API process instead, for a
small deployment. A stopping API ends the answer
streams it serves at once, rather than wait for their answers, and the page follows each answer
again from the next one; the other requests get what `--timeout-graceful-shutdown` allows
(8 seconds in the image). Putting it on a server, with
TLS at a reverse proxy in front of uvicorn, is [`deploying.md`](deploying.md).

`docker build -t emudoc:$(git rev-parse HEAD) --build-arg VERSION=$(git rev-parse HEAD) .`
builds one image for the API, the worker, the migrations and the maintenance commands, with the
frontend built in, tagged with the commit it is built from and naming it: `/api/health`
reports that version and the user menu shows it, and a checkout says `development`. It runs as
a user of its own on a read-only filesystem and refuses development's settings.
`deploy/compose.yml` runs it on one machine, the migrations first; `deploying.md` ("The
image") says what a container needs. `deploy/aws` is the same on AWS as OpenTofu code, staging
and production alike: Postgres on RDS, the bucket, and an EC2 instance that runs the compose
file with Caddy in front. CI releases it: every commit on `main` that passes the checks is
pushed and released on staging, and production is released by hand from the Release workflow,
with a commit staging ran (`deploying.md`, "Releases from CI").

What changed, release by release, is in [`CHANGELOG.md`](CHANGELOG.md). The code is
proprietary ([`LICENSE`](LICENSE)).

The database schema is a set of migrations in `backend/migrations/`; `uv run alembic upgrade
head` applies the ones the database has not seen yet, so run it again after updating. The backend
never alters the database itself: at start it checks that the schema is current and refuses to
serve otherwise, naming that command. A database made by a version from before there were
migrations cannot be upgraded; the command says so, and `docker compose down -v` resets it.

For semantic search, run [Ollama](https://ollama.com) with `ollama pull nomic-embed-text`, or
point `EMBEDDING_BASE_URL` at any OpenAI-compatible embeddings endpoint. The backend asks it for
one vector at start and logs what it found. A deployment runs llama.cpp's server with the same
model beside the app (`deploy/compose.yml`), whose vectors agree with Ollama's.

When working on the app, run `./dev.sh` instead and open <http://localhost:5173>. It starts the
database, applies the migrations, and runs the backend (restarting when Python changes) and the
frontend dev server (updating the open page as you edit, no build needed), which proxies `/api` to
the backend. Ctrl+C stops both
servers. Port 8000 still serves the last build, so it does not show your edits.

## What works without what

| Missing          | Still works                                           | Does not                                          |
| ---------------- | ----------------------------------------------------- | ------------------------------------------------- |
| Model API key    | Parsing, outlines from headings, search, the viewer   | Summaries, facts, scans and images, the Ask tab   |
| Embeddings       | Keyword search, everything else                       | Search by meaning                                 |
| ClamAV           | Everything; each document says it was not scanned     | Malware scanning of uploads (off by choice only in production) |

The header shows which of the two is available; the backend asks the embeddings endpoint at
most once a minute for it, so an endpoint that comes back shows within the minute. In
development it names the models and says what stops one and what to change; a deployment's
members are told only what they cannot do for now and to try later, and the reason is the
operator's, in the log, the metrics and the alerts. After adding a key, restart the backend and
press "Index the rest" in the library to enrich what was indexed without it.
`DISABLED_PROVIDERS` switches a provider's models off on purpose, with the same effect and the
reason shown in the header in development, and the same button enriches what was indexed
meanwhile once it is lifted; a fallback of another provider (`AGENT_FALLBACK_MODEL`, `INDEX_FALLBACK_MODEL`, below)
does the work meanwhile instead. Running without the model is for development: with `ENVIRONMENT=production` the
backend refuses to start until the key is in the environment, unless the model was switched
off that way. The same goes for the embeddings: production refuses to start without
`EMBEDDING_BASE_URL`, or with an endpoint that turns the start's request down, and starts with a
warning when the endpoint is only out of reach (`deploying.md`, "The embeddings endpoint").

## Configuration

Settings live in `.env`; `.env.example` lists them all with their defaults. The ones that matter:

- `RUN_WORKER` is whether the process runs the background worker as well. Unset, it does in
  development and does not in production, where the worker is `uv run python -m app.worker`,
  a process of its own ("Run it" above).
- `WORKER_CONCURRENCY` and `ANSWER_CONCURRENCY` are the worker's threads: 16 documents indexed
  at once and 8 questions answered at once, in lanes of their own, so a backlog of uploads
  never holds an answer up. Both wait on the model most of the time, so a thread costs a
  database connection more than memory. `PARSE_CONCURRENCY` (2) of the indexers parse a file
  at once, each in a child process with memory of its own, and the others wait their turn.
- `DB_POOL_SIZE`, `DB_MAX_OVERFLOW` and `DB_POOL_TIMEOUT` size the database connections a
  process holds: 10 kept open, up to 20 more while all of those are in use, and 5 seconds a
  request waits for one before it is refused with 503 and asked to try again. An API replica
  needs about one per request in flight; a worker needs one per document and per answer it
  runs at once (an answer holds one only while a tool runs) plus four for its helper
  threads, and refuses to start with a pool that cannot hold them; its listener holds one more
  outside the pool. The sum over every replica must stay under the database's
  `max_connections` (100 for Postgres), or a connection pooler goes in between: one in
  transaction mode, such as pgbouncer's usual setup, needs `DATABASE_POOLER=transaction`
  (nothing is prepared then) and `DATABASE_LISTEN_URL` naming the database itself for the
  worker's listener, which otherwise polls every two seconds (`deploying.md`, "A pooler").
- `MAX_ANSWERS` is how many questions one organisation may have waiting for or receiving an
  answer at once, 20 by default. The worker answers `ANSWER_CONCURRENCY` of them at a time,
  shared between the organisations, so the cap bounds how long a question waits without
  letting one organisation keep the others waiting; the question past it is refused with 429
  and asked to wait a moment. It is the same for every organisation; the caps that differ by
  plan are in `backend/app/entitlements.py`.
- `MAX_QUESTION_CHARS` is the longest question, 20,000 characters by default. A question goes
  to the model whole with every turn of its answer, so one that is pasted rather than asked is
  refused with 413 and asked to be shortened; the documents are what to upload.
- `MAX_UPLOAD_MB`, `MAX_UPLOAD_FILES` and `MAX_UPLOAD_REQUEST_MB` bound one upload: 100 MB a
  file, 100 files a request and 500 MB a request by default. A file over its size is rejected
  on its own while the others go in; a request over the other two is refused whole with 413
  and asked to upload in smaller batches. The body has been read by then, so a proxy's limit
  on the request body is the guard against the request itself.
- `RATE_UPLOADS`, `RATE_QUESTIONS` and `RATE_SEARCHES` are how fast one user may go: 20
  upload requests, 20 questions and 120 searches (the search and facts routes) a minute by
  default. `RATE_SIGN_INS` holds the sign-in routes (sign-in, sign-up, the reset and
  verification mails, the authenticator code) to 10 requests a minute per email address, and
  `RATE_SIGN_INS_PER_ADDRESS` to 60 per client address, so that neither a password nor a code
  can be guessed at speed. The request past a rate is refused with 429 and the seconds until
  the minute turns; `0` removes a limit. The counts live in the database, so every API
  replica counts the same.
- `RATE_FAILED_SIGN_INS` is how many wrong passwords and codes an account may be tried with
  in an hour, 20 by default. Past it, sign-ins from the browsers that never signed in to the
  account pause until the hour ends, the right password included, and the owner is told by
  mail; a browser that signed in before (it keeps a cookie, `emudoc_browser`) is counted by
  itself, on this and on `RATE_SIGN_INS`, so a stranger who knows an address cannot keep its
  owner out. A password reset ends the pause.
- `PWNED_PASSWORDS_URL` is where a new password is checked against the passwords exposed in
  data breaches: Have I Been Pwned's range API by default, which is sent the first five
  characters of the password's SHA-1 alone, or a copy of its list on your own network. A
  breached password is refused at sign-up, from a reset link and on the account page; a
  service that does not answer lets it through. Empty checks nothing.
- `SCANNER_URL` names the ClamAV daemon that scans every upload before it is indexed or
  served: `clamd://host:3310`, or `clamd:///run/clamav/clamd.ctl` for one on the same
  machine. The file is streamed to it, so the daemon needs no access to the storage; its own
  limits (`StreamMaxLength`, `MaxFileSize`, `MaxScanSize` in `clamd.conf`) must allow a file
  of `MAX_UPLOAD_MB`, or it refuses the file and the document says so. Empty, nothing is
  scanned and every document says so; `off` is the same choice stated, the only form
  production accepts besides a daemon. clamd's protocol has no encryption, so production
  takes a daemon on the deployment's own network only. `docker compose --profile clamav up -d
  --wait` runs one to try this with.
- `ALERT_EMAIL` is where the worker reports the jobs it gave up on, through the same mail
  settings as the sign-in links, as one digest every quarter of an hour, where it reports
  the uploads the malware scan quarantined the same way, where it warns once a day when
  a day's spending on models reaches 80 % of a limit, the platform's or an organisation's,
  and where its watch (`backend/app/watch.py`) writes when something goes wrong and when it is
  over: questions or documents waiting long for a worker, indexing or answers failing, a model
  failing over to its fallback, the embeddings endpoint, the scanner or the storage not
  answering, the database failing to archive its log or falling behind. Empty sends nothing;
  the `failed-jobs` command lists the failures either way.
- `DAILY_SPEND_CAP` is what every organisation together may spend on models in one UTC day,
  in `PRICE_CURRENCY` by the costs `MODEL_PRICES` gives; `0`, the default, is no limit. Past
  it every question is refused with 503 until midnight UTC and every indexing stage that needs
  a model waits for it. The limit per organisation is its plan's. Production refuses the cap
  without a price in `MODEL_PRICES` for every model named, fallbacks included, since what a
  model without one answers would count nothing.
- `DISABLED_PROVIDERS` is the kill switch: the model providers switched off, by the prefix of a
  `provider:model` name, separated by commas (`google,anthropic`). A model of a provider named
  here cannot be used until the name is removed and the backend and the worker restarted;
  meanwhile the app runs as it does without a key, and says why in its log (and, in
  development, in the header). Production starts with it, unlike with a missing key. The
  operator's area switches a provider off the same way without a restart, and on again; one
  this setting names stays off whatever the area says.
- `ENVIRONMENT` is `development` (the default) or `production`. In development the backend
  copies the model keys from `.env` into its environment, where the model libraries read them.
  A production deployment injects every secret through the environment or a secret manager
  instead; the backend then copies nothing from `.env`, and a missing key is reported as one to
  set in the environment. Production also refuses to start, listing what to change, when the
  settings are development's: the `docker compose` database's password, `LOCAL_ACCOUNT=true`,
  no `STORAGE_URL`, no `APP_URL`, no `SMTP_URL`, no `SCANNER_URL`, no `EMBEDDING_BASE_URL`, no
  `HOSTING_PROVIDER`, no `OPERATOR_NAME`, `OPERATOR_EMAIL`, `BACKUP_DAYS` or `SERVER_LOG_DAYS`, a
  `google:` model without `GOOGLE_PAID_TIER`, a `DAILY_SPEND_CAP` without `MODEL_PRICES`, no
  key for the model, an embeddings endpoint that turns its requests down, with `DATA_REGION` a
  model, a bucket or an embeddings endpoint outside
  the region, or a connection that would carry the documents unencrypted off the deployment's
  own network: a database there without `sslmode=verify-full`, a scanner there, or an
  embeddings endpoint, a model's address or a bucket there over plain `http://`
  (`deploying.md`, "Encryption"). It does not serve the interactive API docs at `/docs`, and it marks
  the session cookie `Secure` and sends `Strict-Transport-Security` whatever the request's
  scheme, since a deployment is behind TLS; a request that still reaches it as plain HTTP is
  logged with what the proxy should send.
- `HOSTING_PROVIDER` names who runs the deployment's servers and database, such as
  `Hetzner Online GmbH, Germany`, for the subprocessor list (Accounts, below): the one entry
  the other settings cannot supply. Production refuses to start without it.
- `OPERATOR_NAME` and `OPERATOR_EMAIL` say who runs the deployment (the company with its postal
  address) and where people write about their data, and `BACKUP_DAYS` how long its backups of
  the database and bucket are kept (0 for none): the terms of service and the privacy policy at
  `/terms` and `/privacy` name them (Accounts, below). Production refuses to start without them,
  and with a versioned bucket that keeps a deleted file's earlier versions longer than
  `BACKUP_DAYS` (`deploying.md`, "Backups").
- `SUPPORT_EMAIL` is where members write for help, from the app's help menu (Accounts, below);
  unset, it is `OPERATOR_EMAIL`, for a deployment whose one address answers both.
  `STATUS_PAGE_URL` names the status page outside the deployment that the help menu and the
  help pages link to (`deploying.md`, "The status page").
- `ACCESS_LOG_DAYS` says how long the infrastructure keeps its records of connections, which
  hold members' addresses: a network's flow logs, and the access logs of the bucket their
  browsers fetch page images and downloads from. Set, the privacy policy names them and the
  period; unset, it names none. The AWS deployment sets it and expires the logs by it
  (`deploying.md`, "The account's records").
- `SERVER_LOG_DAYS` says how long the deployment keeps its server logs: the app's own, with
  each request's client address, the reverse proxy's access log and the database server's log.
  The privacy policy names the period, and production refuses to start without it. The app
  cannot delete what collects its log, so the deployment keeps to it (journald's
  `MaxRetentionSec`, a collector's retention); the AWS deployment does (`deploying.md`, "Logs").
- `DATA_REGION` is the region the deployment keeps its documents in: `eu` (the European Union)
  or `us` (the United States), empty for none. The backend holds to it what it can choose (an
  `openrouter:` model is reached at the region's own address, an `anthropic:` model is told to
  run in the US) and checks at start what it can read: Vertex AI's `GOOGLE_CLOUD_LOCATION`, a
  Bedrock profile or `AWS_DEFAULT_REGION`, OpenAI's European address, the bucket's region and
  the embeddings endpoint. In production it refuses to start while one is outside the region or
  cannot be placed, the Gemini Developer API (`google:`) always among them; in development it
  warns. Where the servers and the database run is the operator's word. Each organisation
  records the region as it is created, the account page shows it, and a deployment that finds
  organisations promised another region refuses to serve them. `deploying.md`, "Keeping the
  data in one region", says where each provider stands.
- `FORWARDED_ALLOW_IPS` is uvicorn's, not the app's: the addresses whose `X-Forwarded-Proto`
  and `X-Forwarded-For` headers it believes, which tell the app the request's scheme and the
  browser's address behind a reverse proxy. `127.0.0.1,::1` by default, right for a proxy on
  the same machine; a proxy elsewhere needs its address or network named, in the environment
  (`deploying.md`).
- `DATABASE_URL` names the database as a SQLAlchemy URL. There is no default: a process that
  is not told its database stops at once and says so. `.env.example` names the
  `docker compose` container, which only this machine can reach; a deployment sets the
  variable in the environment. A database off the deployment's own network is reached with
  `?sslmode=verify-full&sslrootcert=system` on the URL (or the provider's authority's file in
  place of `system`), which production requires. With `system`, `SSL_CERT_FILE` must name the
  machine's bundle of authorities, since psycopg's own OpenSSL looks for them elsewhere; the
  image sets it, and production refuses the URL without it. The database needs pgvector 0.8 or
  later (production refuses an older one, since a search would lose a project's passages to
  the other projects'), and in production its role a `statement_timeout` and an
  `idle_in_transaction_session_timeout` (a warning without them); `uv run python -m
  app.maintenance database` (in `backend/`) says what the server is, how its largest tables
  are kept by vacuum, and what to look at (`deploying.md`, "The database").
- `STORAGE_URL` is where uploaded files and rendered page images are kept: a directory as
  `file:///var/lib/emudoc`, or an S3-compatible bucket as `s3://bucket/prefix`. The default is
  `backend/data` inside the checkout; a deployment names a place of its own. A bucket is reached
  with the standard `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION` and, for
  a service other than AWS, `AWS_ENDPOINT_URL`; the backend checks at start that it can use the
  bucket, and logs whether the bucket encrypts what it keeps (its default encryption, which
  every AWS bucket has; a directory is as encrypted as its disk). Files are always served through the API after its permission checks: from a directory
  the backend sends the file, from a bucket it redirects to a URL signed for 15 minutes, so the
  bucket is never public. An original comes as a download, never shown in the browser's own
  viewer, where a PDF could run scripts and submit forms; only an image opens in a tab, and the
  files the backend sends itself carry `Content-Security-Policy: sandbox`, so a browser runs
  nothing in them. `docker compose --profile s3 up -d --wait` runs a local bucket to try
  this with; `docker-compose.yml` lists the settings for it. A checkout with uploads from before
  storage keys (October 2026) keeps them in the old layout: run
  `uv run python -m app.maintenance relocate-files` once in `backend/` with the app stopped (the
  backend logs a reminder while they are there), or `docker compose down -v` and upload again.
- `PAGE_IMAGE_DAYS` is how long a rendered page image or thumbnail is kept after it was
  rendered (30 by default). A PDF's first pages are rendered once it is parsed, the others the
  first time they are asked for, and each is kept beside the original; every few hours the
  backend removes the ones older than this, and a page asked for again is rendered again, so
  nothing is lost. `0` keeps them for good.
  `uv run python -m app.maintenance sweep-page-images` (in `backend/`) runs the sweep at once,
  say after lowering the setting. The same walk removes what a failed delete left behind: the
  files of documents and projects that no longer exist in the database (files under an hour
  old are left alone, as an upload whose row is not committed yet).
  `uv run python -m app.maintenance sweep-orphans` lists them and `--delete` removes them at
  once. Neither removes anything while the database holds no documents at all, since the
  storage is then more likely another deployment's than all orphans; the message says what to
  check.
- `PARSE_TIMEOUT`, `PARSE_MEMORY_MB`, `PARSE_MAX_PAGES`, `PARSE_MAX_ROWS` and
  `PARSE_MAX_CHARS` bound the parsing of an upload: 300 seconds, 2048 MB of address space, 500
  pages of a PDF, 100,000 filled rows on one sheet and 20,000,000 characters of text in one
  document by default; `0` removes a limit. The parser runs in a child process of the worker
  that is killed at the time limit and caps its own memory, so a file that is too big fails with
  a message naming the limit, is not retried, and leaves the worker and the other uploads
  untouched; a file with more text is refused before the worker holds any of it, since every
  later stage pays for the text (its passages, about one per 1,200 to 2,000 characters, are
  written, indexed and embedded). `PARSE_CONCURRENCY` parses run at once, each with its own
  memory on top of the worker's. An image of more than 50 megapixels is refused the same way,
  by OCR and by the thumbnail alike, from its header and before a pixel of it is decoded.
  `OCR_MAX_PAGES` (100) is how many pages of one document the model may read as scans, each a
  model call: a document with more fails before a page is read, with a message that says to
  split it; `0` removes the limit. `OCR_CONCURRENCY` (4) of a scan's pages are read at once,
  each drawn and sent to the model as an earlier one is answered. `ENRICH_MAX_REQUESTS` (50) is how many requests one
  document's summaries and facts may take, one for each 40,000 characters and one to roll them
  up: a longer document is summarised from its start as far as they reach, and its enrich
  stage says how far; the rest is searched all the same. A table of more than 100 rows, such as
  a sheet's, is shown to the model by its header, its first 20 rows and its last 5, and gives
  no facts, so that a sheet of 90,000 rows is one request rather than hundreds; deploying.md's
  "Large documents" has the whole of it.
- `RENDER_PROCESSES`, `RENDER_TIMEOUT` and `RENDER_MEMORY_MB` bound the drawing of a PDF's
  pages as images, for the document pane, the thumbnails and OCR: the API and the worker each
  draw in child processes of their own, 2 pages at once by default, each page within 30
  seconds and each process within 1024 MB, and no page larger than 2,576 pixels on its longer
  side; `0` removes the time or the memory limit. A page past a limit is not drawn and says
  why (a scanned one fails the document's indexing, naming the page), and its process is
  replaced. `PRERENDER_PAGES` (3) is how many of a PDF's first pages the worker draws once the
  PDF is parsed, so that opening it shows them at once; `0` draws none ahead. From a bucket a
  page is drawn from a copy of the original in the temporary directory, which the API keeps
  five minutes for the next pages, up to `RENDER_CACHE_MB` (256) of copies; `0` keeps none.
- `AGENT_MODEL` and `INDEX_MODEL` name the AI models as `provider:model`. Both default to
  `google:gemini-3.5-flash-lite`, Google's model for new projects (since September 2026 Google
  serves Gemini 2.5 only to accounts that used it before). Indexing is the high-volume side and
  the agent answers questions, so the two can differ, even across providers. Production refuses
  to run on the default, and on an alias its provider moves to a newer model when it likes
  (`gemini-flash-latest`, `chatgpt-4o-latest`): a deployment names the version it tried. The index model is sent the text
  and the scanned pages of every document, the agent each question and the passages it reads,
  so a deployment uses providers whose terms forbid training on them (`deploying.md` says
  where each provider stands). Production refuses `google:` models, the Gemini Developer API,
  until `GOOGLE_PAID_TIER=true` says billing is on for the key's Google Cloud project: on the
  free tier Google may use what it is sent to improve its products, and a free key looks like
  a paid one. `google-cloud:` reaches the same models through Vertex AI. A request through
  OpenRouter is forwarded only to a provider of the model that neither stores it nor trains on
  it.
- Each provider reads its own key: `GOOGLE_API_KEY` for `google:` models, `ANTHROPIC_API_KEY`
  for `anthropic:`, `OPENROUTER_API_KEY` for `openrouter:`; Vertex AI signs in as a service
  account, with `GOOGLE_APPLICATION_CREDENTIALS` naming its key file and `GOOGLE_CLOUD_PROJECT`
  its project. To switch models, change these lines and restart. [Pydantic
  AI](https://pydantic.dev/docs/ai/models/overview/) makes the requests, so any model it
  supports can be named.
- `AGENT_FALLBACK_MODEL` and `INDEX_FALLBACK_MODEL` name the model asked when the first cannot
  answer: its provider is down, overloaded or out of reach, refuses the key or the account, no
  longer serves the model, or is switched off with `DISABLED_PROVIDERS`. Not for a request the
  provider refused or a reply that took too long. The process logs each time it falls back and
  asks the fallback first for the next minute; what the fallback answered is counted under its
  own name and price. Best one of another provider, with its key: it is sent what the first is,
  so it is a subprocessor held to the same region, terms and prices, and a production start
  refuses one it cannot set up and warns about a kind of work without one.
  `uv run python -m app.maintenance models` (in `backend/`) asks every model named, fallbacks
  included, what the app asks of it (two facts of a note, the number on a drawn scan, a call
  to one of the agent's tools) and exits with 1 when one fails: the start only builds the
  models, so this is how a fallback is tried before it is needed (`deploying.md`, "The
  models", which also lists where each provider announces the models it retires).
- `AGENT_EFFORT` and `INDEX_EFFORT` set how hard a model thinks, for models that can.
- `AGENT_MAX_TURNS` is how many rounds of tool calls one answer may make, 12 by default; an
  answer that needs more stops with what it has and says so. Each round is a model request,
  so this bounds what a question can cost.
- `EMBEDDING_MODEL` and `EMBEDDING_DIM` select the embedding model. Changing the model re-embeds
  every document on the next start. Changing the size changes the type of the column the
  vectors live in, which discards them, so the backend refuses to start until
  `uv run python -m app.maintenance rebuild-embeddings` (in `backend/`, with the app stopped)
  has resized the column and queued every document to be embedded again.
- Sign-in: `LOCAL_ACCOUNT=true` lets a request without a session act as a local owner account,
  for development on one machine (off by default); `APP_URL` is the address the links in emails
  point at; `SMTP_URL` sends the emails over TLS to a relay whose certificate the machine
  trusts, or `RESEND_API_KEY` through Resend's API (without either they go to the log);
  `GOOGLE_OAUTH_CLIENT_ID` and `MICROSOFT_OAUTH_CLIENT_ID` with their secrets offer sign-in with
  those providers.

## Accounts

Signing up starts with an email address alone, which is sent a link; the page it opens takes a
name and a password and makes a user and an organisation of their own, which they own, signed in
and with the address confirmed, since whoever chose the password holds the mail. The organisation
starts with the sample project, "Sample: Acme supplier contract": a supplier's contract, three of
its invoices, the price list, the delivery log, an email and meeting notes, indexed already
(`backend/app/sample`), so that the first question gets its answer and citations without waiting
for an upload, and an empty chat in it offers three questions that read the documents against
each other. It counts against nothing on the plan and takes no uploads; its questions count, and
it is deleted like any project (`SAMPLE_PROJECT=false` turns it off; deploying.md, "The sample
project"). The projects page lists the organisation's first steps (upload its own documents, ask
about them, invite its team), each with the way to take it, read from what the organisation has
done, so a member who joins a team at work finds them taken; a member is offered uploading and
asking, an owner or admin inviting too, a viewer nothing (`backend/app/onboarding.py`). Hiding the
list hides it in every organisation of the account. The first project a member opens shows a
short tour of the workspace, card by card, which "Skip the tour" or Escape ends; the user menu
offers it again inside a project. Once an answer about the organisation's own documents is done
(not the sample's), the chat suggests inviting the team under it to an owner or admin while
nobody else is a member or invited, with a form that sends the invitation there, or on a plan
for one the way to a larger one; its cross hides it in every organisation of the account
(`frontend/src/workspace/InviteTeam.tsx`). The form answers
an address that has an account the same, and that address is told by mail instead, so the form
says nothing of who uses the deployment. An account made through Google or Microsoft whose
address the provider does not vouch for gets a verification link, and a banner asks for it until
it is used. Sign-in sets a session cookie, marked `Secure` over HTTPS and always in production. The account page (the
initials in the header) changes the name and the time zone the mail is dated in (the browser's at
sign-up; the pages date everything in the browser's own), moves the account to a new email
address through a link sent there, which the old address is told of once it is opened, changes
the password, turns on a code from an authenticator app at sign-in (shown as a QR code to scan,
with recovery codes for a lost phone; its secret is kept encrypted with `ENCRYPTION_KEYS`, which
the database never holds, so a copy of it makes no codes), lists the browsers and devices that are signed in and
signs any of them out, and chooses how each kind of notice is told, the news a member may do
without: that an upload which took five minutes or more to index is done, naming the files that
could not be indexed and why, and to an organisation's owners and admins that its pages or
questions for the month reached 80 % of what it may use, all of it, or for pages too few for a
document (`backend/app/notices.py`). Each is told under the bell in the header, the member's
notification centre of every organisation they belong to (opening one switches to its
organisation; kept 90 days), and by email, each way on or off by kind; a notice's own link and a
mail program's "Unsubscribe" turn its kind's email off, and a browser may show them as they come
as the computer's own notifications while the app is open. A forgotten password is reset
through a link sent by email, which signs the account out everywhere. The worker sends the
mail, a plain text and an HTML part each, from a queue: a relay that is down gets a message again
for half an hour, and an address the mail provider says bounced for good or was marked as spam
is sent no notices for a year (deploying.md, "Mail"). Google and Microsoft accounts sign in through
OpenID Connect and are linked to an existing account only when both sides have verified the
address. A visitor without a session is sent to `/login` and returns to the page they wanted.
A session the server stops accepting drops everything the tab had fetched, so whoever signs in
next in it starts from nothing. Every request that changes something must carry an `X-Requested-With` header, which the app's own
requests do and a page on another site cannot add, so the cookie alone changes nothing; send the
header when calling the API by hand. The app answers no request from another origin (there is
no CORS): the frontend is served from the API's own origin, and the defence rests on that. The
routes are under `/api/auth`, the pages in `frontend/src/account/`.

A user may belong to several organisations, each with its own projects, members, plan and audit
log. The organisation's name in the header opens a menu of them: switching is the session's, so
every tab of the browser works in the one chosen, and a new session starts in the one joined
first. Every request of the app names the organisation its page shows (`X-Organization`), and a
tab left behind by a switch in another is refused with 409 and starts again in the one the
session works in, so nothing it asks lands in the wrong organisation. "New organisation" starts
one the user owns, on the Free plan and in the deployment's region; one user may own
`MAX_FREE_ORGANIZATIONS` (2) on the Free plan, so that new ones are no way past its limits. The
organisation's page (`/organization`) renames it (owners and admins; the slug the operator's
commands name it by stays), keeps its settings, lists its members, hands it over to another member with the
password (they become an owner and the one handing it over an admin), and leaves it: the
member's private chats in its projects go, and the shared ones stay. Nobody leaves an
organisation that would be left without an owner or with nobody, nor their last one.
Its settings are its owners' and admins', and each change is a row of its audit log: the
language its documents are searched in (keyword search matches words by their stems in it, one
of the database's text search configurations, else the deployment's `FTS_LANGUAGE`), which the
documents indexed before move to by a background job within minutes, without asking a model
or counting a page, and answers fall back to when a question does not show a language
(`backend/app/languages.py`); how long it keeps a chat after its last question, never longer
than its plan, which deletes the older chats of every member at the next housekeeping round,
as the page says before it is saved; and how its members are told each kind of notice until
they choose for themselves on their account page. Its security settings are theirs too
(`backend/app/security.py`): requiring two-factor sign-in of everyone who works in it, which
only someone who signs in with it may turn on, and which holds a member without it, in that
organisation alone, at a page that sets up an authenticator app, offers their other
organisations and leaving this one, and signs out (an account that signs in through Google or
Microsoft sets one up too, since the provider's own second factor is not seen); and signing its
members in again sooner than the deployment's `SESSION_DAYS`, which ends a session that signed
in longer ago at its next request there. The owners and admins see which members sign in with
two-factor, and the page names whom requiring it would hold before it is turned on.
Its owners and admins also make its API keys (`backend/app/apikeys.py`), on a plan that includes
the API (every plan but Free). A program sends a key in a header, needing none of the app's:

```bash
curl -H "Authorization: Bearer emk_…" https://emudoc.example.org/api/projects
```

A key acts as the member who made it, in the organisation it was made in: it reads what they may
in the organisation's projects (their folders, documents, search, facts, extraction templates
and cross-checks, chats and trash), and with the write scope changes it as their role allows,
never more. It does less as soon as they
may, goes when they leave or are removed, and is refused whenever they would be (terms not
accepted, two-factor required and not set up, the account set to be deleted). It reaches nothing
of the accounts or of the organisation itself, so no key signs in, makes another key, invites
anyone or touches billing. A key is shown once, as it is made or rolled (only its own member
rolls it, and the secret before stops at once), runs 30, 90 or 365 days or until it is revoked,
and is held to 300 requests a minute of its own (`RATE_API_REQUESTS`) as well as its member's
rates. Each key made, rolled or revoked is a row of the audit log, and so is what a key does, as
its member's with the key named. On a plan without the API, the keys made before are kept and
refused until the organisation is on one with it again.
The roles: a viewer reads the projects and asks about them in chats of their own, a member also
changes the projects (makes, uploads, organises, deletes), an admin also renames the organisation
and manages who is in it, and an owner also manages its owners. The page offers each member only
what their role allows, and the server refuses the rest. On the organisation's page the owners
and admins change the other members' roles and remove members: an admin anyone's but an
owner's, and to any role but owner; an owner anyone's, so an owner can make another without
handing the organisation over. Nobody changes their own role there, so an organisation always
keeps an owner. A member removed loses what leaving loses, and one removed from their last
organisation gets an empty one of their own, as at sign-up. A project is seen by everyone in the
organisation until it is made private ("Who sees it" on its card, or "Private" when making it):
then only the members chosen for it see it, and the owners and admins, who see every project,
so that none is left that nobody can manage. Whoever may change the project chooses; a member
who is neither owner nor admin stays among its members, and a viewer may be chosen, to read it.
Anyone else finds no trace of it: its routes answer 404, as another organisation's do. Opening
it to everyone forgets the choice; leaving or being removed takes one's place among the
organisation's private projects, and a changed role keeps it. A member's chats in a project
they no longer see are hidden until they are chosen again, and an answer under way finishes
(`backend/app/access.py`, `GET` and `PUT /api/projects/{id}/access`). Each act
is in the organisation's audit log; a sign-in is recorded where its session starts and what the
account does after where the session works then, so one organisation does not see when, or
from where, a member signs in to another. The routes are `backend/app/api/organizations.py`, the
pages `frontend/src/organization/`.

People join an organisation by invitation. Its owners and admins invite an address with a role,
never above their own, from the organisation's page; the address is mailed a link to
`/invitation`, which shows who invited it to what. Signed in with the address, one click joins;
an address with an account signs in first and comes back; one without makes its account there,
or through Google or Microsoft from there, and that account is a member of the inviting
organisation and of none of its own. Another account cannot accept it, since the link proves
only the address it was sent to, which an account that had not confirmed it then has. An
invitation lasts a week, takes a place among the members the plan allows meanwhile (the Free
and Pro plans allow one, so only Team and Enterprise invite), and is listed for the owners and
admins, who send it again (a new link, the old one stops) or revoke it; accepting or revoking
it deletes it, and an expired one goes a month after (`backend/app/invitations.py`,
`backend/app/api/invitations.py`, the link's routes in `backend/app/api/auth.py`). An owner or
admin can also let everyone whose confirmed address is at a domain join, as a member or a
viewer: only the domain of their own confirmed address, and never a mail service's such as
gmail.com. Emudoc then offers the organisation to everyone there, on the organisation's page and
in the header's menu, and they join only if they choose, while the plan has room.

Making the account takes a ticked box, on the page the link opens, that accepts the terms of
service and the privacy policy, which anyone can read at `/terms` and `/privacy` (the sign-in
pages and the user menu link to them).
The backend fills both in from its settings and constants (`backend/app/legal/`, `GET
/api/legal`): who runs the deployment, where the documents are kept, how long each thing is
kept on each plan, the two cookies, whether a model provider may learn from what it is sent,
whether uploads are scanned, how long the infrastructure's records of connections are kept.
The account records the edition it accepted (`TERMS_VERSION`) and
when, and the audit log records the acceptance. An account made through Google or Microsoft,
or one that accepted an older edition, sees only a page that asks for the current one, with a
copy of its data and its deletion as the other ways out; the API refuses it everything else
with 403. The app sets no analytics or advertising cookies and loads nothing from other sites,
so it shows no cookie banner. The chat pane says that answers are written by an AI model and
can be wrong, and a link in an answer shows its address without leading anywhere, since a
document can steer what the model writes.

The account page also lets Emudoc's support see the account in the organisation it works in, for
a day or a week, as the member sees it and without changing anything, and withdraws that leave,
which ends a view at once; the member is mailed each time support opens it (`backend/app/support.py`,
`PUT|DELETE /api/auth/support-access`). It downloads "your data": one JSON file with everything kept about the user
as a person (the account, how it signs in, its organisations, the sessions of the last month,
the chats it started with every message, the usage counted to it, and the audit log's rows that
name it), without the password
hash, the authenticator secret or the organisation's documents (`backend/app/export.py`, `GET
/api/auth/account/export`). Deleting the account takes the password, or the address typed out
for an account that signs in only through Google or Microsoft. It signs out every other browser,
mails a notice, and leaves 14 days in which the account can do nothing but be kept, exported or
signed out of: signing in shows the day and a "Keep my account" button. Then the worker's
housekeeping purges it, with every organisation it was the only member of and everything in it,
rows and files; in an organisation with other members only the user's membership and chats go,
and the last owner of one is refused until another member is an owner (`backend/app/retention.py`).
Each purge leaves a record under `erasures/` in the storage (the account's id and creation time,
nothing else), so an account that a restored backup brings back is purged again at the next round.
A member (not a viewer) exports a whole project from its card on the projects page: the worker
writes one ZIP file with every document's original in its folders, `project.json` listing the
project, its folders, its documents and the chats the member may read (those shared with the
project and their own), and `documents/<id>.json` with each document's outline, passages (the
`[P<id>]` an answer cites) and facts, leaving out the trash, quarantined files and other
members' private chats (`backend/app/export.py`, `POST|GET /api/projects/{id}/exports`). The
member is told when it is ready, in the app and by email as they chose, downloads it from the
same panel, and it is kept 7 days for them alone, then goes from the storage.
An owner deletes the organisation as an account is deleted, at the foot of the organisation
page, confirmed by the password or the address typed out, once its subscription no longer
renews (the local organisation never). Every member is mailed the day, 14 days on, and until
then a banner under the header says it on every page: nothing can be added to its projects or
changed in them, nobody new joins it and it buys nothing, while what it holds can still be read,
asked about, downloaded, exported and deleted, and any owner keeps it from the page or the
banner, which mails every member again. Then the housekeeping deletes it with everything in it,
its audit log too, and a member it was the only organisation of is given one of their own; its
record goes under `erasures/organizations/`.
After the database is restored to an earlier point, `python -m app.maintenance after-restore
<time>` brings back from a versioned bucket the files deleted since, moves the ids past every id
the storage holds, signs everyone out, expires the open invitations and records the restore in
each organisation's audit log
(`backend/app/restore.py`); until it has run, an upload refuses a place in the storage that a
lost document's files still hold. With `--dry-run` it changes nothing and fails when the storage
lacks a document's original at that point: `deploy/restore-drill.sh` restores the newest backup
into a throwaway copy and runs it against the live storage, the monthly restore drill
(`deploying.md`, "The drill"); `deploy/managed-restore-drill.sh` does the same for a managed
database, which the provider restores to a new instance (`deploy/rds-drill.sh` for RDS).
A chat is kept for the plan's retention after its last question (30 days on Free, a year on
Pro, for good on Team and Enterprise), or for less time where the organisation chose so, which
the chat list and the account page say; after a move to a plan that keeps chats or audit log
entries for less time, those are kept as before for 30 more days (`RETENTION_HELD`), unless the
organisation chose less, which the organisation page says; sessions,
one-time links and expired invitations are removed a month after they ended.

The log holds no document text, question, answer or key (`backend/app/logs.py`): a traceback
keeps its frames and the kinds of its exceptions but not their messages unless the app wrote
them, a database error its SQLSTATE and names rather than the row it refused, a provider's error
its status and codes rather than its words (a developer sees the same, a deployment's members
neither), and the access log a query's
parameter names without their values, as does the line httpx writes for each request the app
sends, without the user and password an address may carry. A failed parse names the document
by its id. The links in emails carry their token after `#`, which
no browser sends to a server, and pages send no `Referer`. A refused request (422) says where
and why, never with the value it refused, so a short password or a link's token does not come
back in the response. `deploying.md` ("Logs") gives the
proxy a log format to match.

In production the API, the worker and the migrations write one JSON object a line to standard
error (`LOG_FORMAT`, text in development): the time in UTC, the level, the logger, the message,
and the ids of what the line was logged about, the request's id with the user, organisation
and project it acts on, or the job's id and kind with its document or answer. uvicorn's own
lines, its access log included, come out the same way, without the probes that succeeded, and
an access line says how long the request took to begin its response. Every response carries
its request's id in `X-Request-ID`, the proxy's when it sent one.

Every request starts a trace (`trace_id` on its lines), which the jobs it queues carry, so an
upload's request and its documents' indexing, or a question and its answer, are found in the
log by one id (`backend/app/tracing.py`). Each run of a job ends with one line that says where
its time went: how long it waited for a worker, each stage, each request to a model or the
embeddings endpoint with its model, outcome, first event and tokens, each tool call, the writes
of an answer's events, and when its first text was written. The spans name the app's own words,
never what was searched for or answered; there is no tracing service, on purpose. `deploying.md`
("Traces") shows a line and how to find the slowest answers.

With `SENTRY_DSN` set, the API and the worker report each error nobody planned for to Sentry,
or to GlitchTip or a Sentry of your own (`backend/app/tracking.py`), with what the log would
show of it and the ids of the request or job as tags: the exception's kind and frames, its
message only when the app wrote it, never a frame's variables, a request's body, cookies or
query values, nor an email address. An error the page renders into shows a reference and the
way on in place of the part that broke, and is reported to the app (`POST /api/page-errors`),
which logs it and passes it on; the browser runs no tracker's script. The part is as small as
it can be: the header stays while the page below it shows the error, and in a project the
library, the open document and the chat each break alone, offering to try again, and start
afresh once another folder, document or chat is opened
(`frontend/src/components/ErrorBoundary.tsx`, `frontend/src/failures.ts`). A 500 answers with
a sentence that ends with the request's id as the reference to quote. The subprocessor list
and the privacy policy name the tracker when it is a company's. `deploying.md` ("Error
tracking") has the details.

The question mark in the header is the help menu. It opens the help pages at `/help`, public
like the terms and linked under every sign-in page: a few articles, from getting started to
plans, roles and deleting, with a search over them, which the backend fills in as it serves
them (`backend/app/help/`, `GET /api/help`) from the code and the settings, so that the plans'
figures, the file types and sizes an upload takes and how long the trash keeps what was deleted
are the deployment's own, and a sentence about something it does not do (scanning, selling
plans) is left out. And it is where a member writes to support: a problem, a
question or an idea, in their own words (`frontend/src/help/`, `POST /api/feedback` in
`backend/app/api/help.py`). With `STATUS_PAGE_URL` it also leads to the status page, which the
page that cannot reach its server links to as well. It leads to what's new, `/whats-new`, also
public: what changed for members, the **Members** entries of `CHANGELOG.md` a week of days at a
time (`backend/app/changelog.py`, `GET /api/changelog`), with a dot on the question mark while a
member has entries they have not read since they last opened it, which is kept on their account
(`POST /api/auth/changelog/read`). The app mails a message to support to `SUPPORT_EMAIL` with a `Reply-To` of the
member's address and what support needs to find what they met: the account, the organisation,
the page's path with the ids of what was open, the version, the browser, and the reference of
the last request the page saw refused or failed, which the log is searched by. The member's
words go into the mail alone, never into the log, the subject or a table; the menu answers with
the message's reference. A member writes 10 messages an hour (`RATE_FEEDBACK`). `deploying.md`
("Support") says what the message holds and how to find its request.

The API and the worker serve metrics in Prometheus's format on a port of their own
(`METRICS_ADDRESS`; the image's containers on 9464, which the compose file publishes nowhere):
requests by route pattern, the database pool, each model's requests, outcomes, time to the
first event and tokens, stages, jobs, uploads and rate refusals, and from a worker the documents
and the queue by status, the backlog's age, the answers being written, the organisations and
their bytes by plan, the day's spend, and how the database server keeps up (dead rows, the
oldest transaction, the archiving of its log) (`backend/app/metrics.py`). No label names an
organisation, a user or anything a member typed. `deploying.md` ("Metrics") lists them, with a
Prometheus to scrape them; `deploy/prometheus/alerts.yml` holds alerting rules over them,
tested with promtool ("Alerts"), and a watchdog for a dead man's switch. On AWS the instance
runs Prometheus and Alertmanager with them, sending to the environment's alerts topic, and
CloudWatch reports the instance's failed status checks and the alerts falling silent.

Three service levels are measured the same way, each over 30 days: 99.5 % of the members'
requests without a server error, 95 % of answers begun within 30 seconds of the question
(`emudoc_answer_first_text_seconds`), and 95 % of uploads ready within 10 minutes
(`emudoc_document_ready_seconds`, once per document, by `documents.ready_at`), with a fourth,
the app up in 99.5 % of the minutes, for an uptime check from outside. Six of the rules alert when
an objective's error budget is spent too fast, a fast burn at once and a slow one for the working
day. `deploying.md` has them ("Service levels"), who hears which alert and how soon ("On call"),
what to do for what goes wrong most often, from a stuck upload to a provider down or a key to
rotate ("Runbooks"), and every variable the app reads with its default ("Every setting").

Each organisation keeps an audit log (`backend/app/audit.py`): its members' sign-ins (and
failed ones for their accounts), profile, address, password, two-factor and session changes,
exports and deletion requests, every upload, download and deletion of a document, folder or
project and its restore from the trash or deletion for good there, who sees a project, chats
shared, taken private, deleted or restored, a member's leave for support to see their account
and each view of it, and what the operator (a plan change, a requeue) and the app (a quarantine,
an account purge with the owner it promoted) did on their own; what was downloaded in support's
view names the operator too. A row names the member, the project and the object, the client's address, and names or
settings as they were (a file's name, a role before and after), never what a document, a
question or a chat's title says, nor what a name or an address was changed to. Its owners and
admins read it on its page (`/organization/audit`, from the organisation page's Security
section), the newest first, by what was done, by whom, in which project and on which days, and
download what they picked as a CSV file, which the log records too (`GET /api/audit`, `GET
/api/audit/export`). A row is kept 90 days on Free, a year on Pro and Team, and for good on
Enterprise. A purged account's rows stay with an organisation that keeps other members,
without their addresses, and the account export includes the rows that name its user.

`/subprocessors` lists the companies the deployment sends data to, what for and what each
receives, for anyone to read before signing up (the sign-up page and the user menu link to it).
The backend reads it from its settings (`backend/app/subprocessors.py`, `GET
/api/subprocessors`), so it changes when a provider does: `HOSTING_PROVIDER`, the bucket, the
companies behind the two models, a hosted embeddings endpoint, the mail relay, and Google and
Microsoft when sign-in with them is set up. A service on the deployment's own network is not a
subprocessor and is left out; a host the backend does not recognise is listed by its name.
Each entry says where the company handles what it receives when the settings show it
(`backend/app/residency.py`), and with `DATA_REGION` the page opens with the region the
documents are kept in.

Every response carries the headers that tell the browser what a page of the app may do
(`backend/app/headers.py`): a `Content-Security-Policy` that allows scripts, styles and requests
from the app's own origin only, and images from the storage bucket besides, so an answer cannot
load an image from an address a document planted in it; `frame-ancestors 'none'` and
`X-Frame-Options`, so no other site can show the app in a frame; `X-Content-Type-Options:
nosniff`; `Referrer-Policy: no-referrer`; and `Strict-Transport-Security`
over HTTPS and always in production. Every answer of the API, the files and page images
included, carries `Cache-Control: no-store`, and signing out sends `Clear-Site-Data: "cache"`,
so a shared computer keeps nothing of the account's chats or documents. The policy allows no inline code, which is why the one
script `index.html` runs before the app, the saved theme, is the file `frontend/public/theme.js`.
The development server on :5173 sends none of this; the build served on :8000 does.

## Tests

```bash
cd backend && uv run pytest          # needs the database container
cd frontend && pnpm lint && pnpm test && pnpm build
cd frontend && pnpm e2e              # after pnpm build; needs the database container
```

The frontend's unit tests (`pnpm test`, Vitest) sit beside the modules they test
(`src/**/*.test.ts`) and run in Node with `fetch` stubbed: the chat's events and its
reconnections, the workspace's URLs, the formatting. `pnpm e2e` (Playwright) drives the built
page in Chromium as a new member: sign up through the link the app mails, skip the tour, ask the
sample project one of its questions, make a project, upload a contract, wait for it to be indexed, search for it, ask about it and open the passage the
answer cites, then invite a colleague from under that answer, checking each kind of page, panel
and dialog against axe-core's rules for WCAG 2.2 AA in both themes along the way
(`frontend/e2e/axe.ts`), and the keyboard's focus where what held it goes. It starts the app itself (`frontend/e2e/serve.sh`, in a database of its own,
`emudoc_e2e`, made afresh each run, its log in the temporary directory, where the test finds the
mail) against a stand-in for the model and the embeddings endpoint (`frontend/e2e/standin.py`),
which answers the same way every run and needs no key. Chromium comes once with `pnpm exec
playwright install chromium`.

`cd backend && uv run python -m loadtest` is the load test: the API and a worker as production
runs them, against the same stand-in, and a member who uploads 3,000 documents, searches them,
asks 20 questions at once and uploads a 300-page scan and 50 MB workbooks. It prints what the
member waited at the 50th and 95th percentiles; the operator's guide (`deploying.md`, "Load")
has the last run's figures and what they mean for a deployment.

`tofu test` in `deploy/aws/modules/environment` and `deploy/aws/account` checks the AWS
configuration against a mocked provider; the backend suite checks that what it writes is what
production asks for.

CI (`.github/workflows/ci.yml`, GitHub Actions) runs all of it on every push to `main` and
every pull request: the backend's lint, format and suite, once against Postgres and once
through pgbouncer in transaction mode; the frontend's lint, unit tests and build; the browser
test, and the load test at a size that takes a minute; the image, built and then
started by `.github/scripts/check-image.sh` (the migrations, the API and a worker, read-only,
against the development database); the deployment's embeddings server, started from the
compose file by `.github/scripts/check-embeddings.sh` and asked for the vectors in
`.github/scripts/embeddings.json`, which are those the deployments' databases hold; the AWS
configuration's format, validation and tests;
shellcheck on every shell script; actionlint on the workflow; and trivy on the OpenTofu and the
Dockerfile, with what was looked at and accepted in `.trivyignore.yaml`, each with its reason;
that uv, pnpm, Node and Python are named at the Dockerfile's versions wherever else they
appear (`.github/scripts/check-versions.sh`); and gitleaks over every commit, for keys and for
any `.env` file whatever it holds (`.gitleaks.toml`, which says what was accepted and why).
On `main` it also scans the image it checked, pushes it, and once every check passed releases
it on staging (`deploying.md`, "Releases from CI").

A key that CI finds has been pushed, and has to be revoked. The pre-commit hook in `.githooks`
runs the same gitleaks on what is staged and refuses the commit instead; it needs Docker, and
is turned on once per clone:

```bash
git config core.hooksPath .githooks
```

The dependencies and the images are audited for known vulnerabilities every morning and on
every change to a manifest, a lock file, the Dockerfile or the deployment's compose files
(`.github/workflows/audit.yml`): every Python package `uv.lock` holds, the development tools'
included, by pip-audit against OSV, where any known vulnerability fails; the frontend's by
`pnpm audit`, where a high or critical one fails; the image, built from this commit, by trivy,
where a high or critical one fails once a fix is released, so a fixed vulnerability in a base
that has not moved is found the morning after; and the images the deployment runs beside it
(ClamAV, llama.cpp's server, Caddy) by the same scan, where what waits for their projects' next
release is accepted in `deploy/.trivyignore.yaml` until a date (`deploying.md`, "The image").
By hand:

```bash
uv --directory backend export --locked --all-groups --no-emit-project --quiet |
  uv --directory backend run --locked --only-group audit pip-audit --disable-pip --strict \
    --requirement /dev/stdin --vulnerability-service osv
pnpm --dir frontend audit --audit-level high
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock -v "$PWD:/src:ro" -w /src \
  aquasec/trivy:0.73.0 image --scanners vuln,secret --severity HIGH,CRITICAL --ignore-unfixed \
  --ignorefile .trivyignore.yaml emudoc:<tag>
```

The suites answer for the models from recorded responses, so the models themselves are asked
every morning by the Models workflow (`.github/workflows/models.yml`), once the repository
names them and holds their keys: `python -m app.maintenance models` against every model and
fallback the deployment names (`deploying.md`, "The models").

Dependabot (`.github/dependabot.yml`) proposes the updates of both lock files and of the
workflows' actions once a week, a week after their release, and those of the images, which
are pinned by digest: the image's bases in the `Dockerfile` and the compose files' images, a
newer version a week after its release and a rebuilt tag's new digest at once.

The backend tests use a separate `emudoc_test` database and make no network calls: the AI
model, the embedding model, the mailer and the identity providers are replaced by stand-ins, and
the requests to Gemini, Claude and OpenRouter are checked through their real SDKs against recorded
responses. The tests empty that database, so they refuse to run against any other, and against a
server that is not on this machine unless `TEST_DATABASE_ALLOW_REMOTE=1` is set; the server comes
from `TEST_DATABASE_SERVER` (default: the `docker compose` container, `emudoc:emudoc@127.0.0.1:5437`).

## Layout

```text
backend/app/
  parsing/      PDF, Word, Excel, CSV, text and Markdown into blocks
  indexing/     structure, enrich and the pipeline
  jobs.py       the job queue: background work as rows the worker claims with leases
  worker.py     the worker that claims and runs the jobs; its own process in production
  deadletters.py
                the jobs the worker gave up on: the operator's digest, listing and requeue
  scanning.py   the malware scan of uploads: the clamd client, the quarantine, the digest
  startup.py    the checks and the services every process starts with
  version.py    the version a process runs: the commit its image was built from
  postgres.py   what the app needs of its Postgres server: pgvector, the role's timeouts, vacuum
  search.py     hybrid search over passages
  languages.py  the language an organisation's documents are searched in, and its passages' move
  folders.py    the folder tree as paths and subtrees
  agent/        the five tools and the tool-use loop
  accounts.py   who a request acts as; signing up makes a user and their organisation
  sample/       the sample project a sign-up's organisation starts with: its files, the index
                the pipeline made of them, the copy and the build-sample command's build
  onboarding.py the organisation's first steps the projects page lists, read from what it did,
                and the account's choices about them, the tour and the invite suggestion
  invitations.py
                invitations to an organisation, and joining one by an address's domain
  auth.py       passwords, sessions, one-time links and authenticator codes
  apikeys.py    the API keys a program calls the API with, as the member who made them
  keyring.py    the keys of ENCRYPTION_KEYS and the cipher the database's secrets are kept in
  encrypted.py  the columns kept encrypted, the start's check and the encrypt-secrets command
  oidc.py       sign-in with Google and Microsoft
  mail.py       the emails the app sends, and the mailers: SMTP, Resend, the log
  postbox.py    mail posted through the queue, the worker's sending, and the notices' mail
  notices.py    the notices about members' work, told in the app and by email as each chose
  bounces.py    what the mail provider says bounced: Resend's webhook, SES's queue
  maintenance.py
                the embedding checks at start, the housekeeping and the operator commands
  restore.py    what the app sets right after its database is restored to an earlier point
  usage.py      what each organisation used, recorded as it happens, and the daily rollup
  entitlements.py
                what each plan allows, and the checks the routes and the parse stage make
  billing.py    changing a plan, and selling it and packs: Checkout, the portal, Stripe's events
  flags.py      the feature flags: what is being rolled out, to how many organisations, and to which
  extraction.py the extraction templates: the built-in ones, and those in force for a project
  fact_table.py the facts table: a kind's documents side by side, sorted, kept and paged
  fact_export.py
                the facts table as a CSV file or an Excel workbook to save
  fact_checks.py
                members' checks and corrections of facts, kept through the next reading
  fact_changes.py
                what a document's last reading changed of its template's fields
  cross_checks.py
                rules comparing one kind's facts with another's, and what each finds
  payments.py   Stripe's API, and the signature on its events
  api/          HTTP endpoints
  headers.py    the headers every response carries for the browser
  answers.py    an answer as a job: the worker writes its events, the API streams them
  llm.py        every call to the AI model, and the fallback when the first model cannot answer
  trial.py      the models command: each model asked what the app asks of it
backend/migrations/
                the database schema as Alembic migrations, one file per change
frontend/e2e/   the browser test, the app's start for it and the stand-in model it runs against
frontend/src/
  pages/        the Projects page and the Workspace (two panes)
  workspace/    the library, tabs, document viewers and the chat pane
  account/      sign-in, sign-up, the email links, and the account page
  api.ts        the typed client for the backend
  auth.ts       who is signed in, for the shell and its pages
  chat.ts       a chat's messages as the chat pane holds them, and the answers being followed
  onboarding.ts the first steps' words and ways on, and when the chat suggests inviting the
                team; tour.ts the workspace's tour, which components/Tour.tsx shows
deploy/
  compose.yml   the image on one machine: the migrations, the API, a worker and ClamAV
  restore-drill.sh
                the restore drill: the newest backup into a throwaway copy, checked against the
                live storage
  managed-restore-drill.sh
                the same drill for a managed database, with the provider's restore and delete
                as two commands; rds-drill.sh is RDS's
  aws/          staging and production on AWS in OpenTofu: account/ once per account,
                modules/environment/ an environment, shared/ the settings both take, the two
                roots, push-image.sh and release.sh, which CI runs, and what the image carries
                for the instance (compose.yml over the one above, Caddyfile,
                alertmanager.yml, host/)
  prometheus/   the scrape configuration and the alerting rules, with their promtool tests
Dockerfile      the one image for every process; .dockerignore names what goes into it
.github/        CI: the checks of every push and pull request and staging's releases
                (workflows/ci.yml), a release by hand (workflows/release.yml), the daily
                audit of the dependencies and the image (workflows/audit.yml), the daily
                question to the deployment's models (workflows/models.yml), Dependabot's
                updates (dependabot.yml), the image's smoke test (scripts/check-image.sh) and
                the tools' versions (scripts/check-versions.sh)
.githooks/      the pre-commit hook that refuses a key or a .env file (git config
                core.hooksPath .githooks)
CHANGELOG.md    what each day's commits changed for members and operators
```

## Not built yet

- The operator's area views a member's account read only, with their leave; it does not act as
  them, nor turn features on per organisation.
- Billing: the connectors and the model tier the plans declare are not read by anything yet.
- The public API: keys call today's routes, which may change; versioned routes under
  `/api/v1`, webhooks for a document ready or failed, and a hosted reference are to come.
- More file types (slides, email, legacy .doc and .xls), and reading order for multi-column PDFs.
