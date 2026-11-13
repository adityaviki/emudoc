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
the model) is "Partly indexed", with an amber dot: found by its words meanwhile, and finished
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
or a TIFF, the slides of a presentation and the rows of a sheet (`PARSE_TIMEOUT`,
`PARSE_MEMORY_MB`, `PARSE_MAX_PAGES`, `PARSE_MAX_ROWS`), so a file that would take hours or all
the memory stops that process alone, and the document says which limit it hit. A long PDF is
read `PARSE_PART_PAGES` pages a process, each within those limits, and its document made of the
parts by one more, its outline over the whole file as if it had been read at once. Before any of
that, the upload checks that a file's bytes are what its name says (a PDF's header, an image's
signature, the part a Word document, a workbook or a presentation declares, an RTF document's
opening, UTF-8 text with no binary in it, an HTML page with none in any encoding) and rejects one that is not with the reason, while the other files of the same
upload go in. An Office file saved with a password, or in the format from before 2007 (.doc,
.xls, .ppt), is rejected there with what to do in Office; a PDF that needs a password to open, or has no pages, goes in and fails to parse with
the reason (one whose password only restricts printing or copying is read like any other). An
empty file is rejected at the upload. The first stage then has the file
scanned for malware by the ClamAV daemon `SCANNER_URL` names, before any parser opens it and
before the file is served to anyone: a flagged file is quarantined, which the document shows
with the scanner's verdict, nothing of it is indexed, and no member can open or download it
until an editor deletes it (or has it scanned again with "Index again", once the signatures
have changed); the worker reports each quarantine to `ALERT_EMAIL`. A daemon that is down
holds the upload in the queue, retried like any passing failure. Without a scanner the stage is
skipped and every document says so: a team's choice, since the uploads are its own, which
every production start warns about.

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
which an answer offers a draft (below). What the tools return reaches the model inside tags its
prompt names as data, never instructions, and so do the documents' summaries and facts a draft
or a report is written from; the indexing model is told the same of the document it reads. So a
document that tells the assistant what to do (to say an invoice is approved, to cite a passage, to
offer a draft, to describe other projects) is reported, not obeyed, and a document that writes
one of those tags writes text, not a tag.

The facts are read for each document's kind, by extraction templates (`backend/app/extraction.py`). A
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
button, above the project's search box, lists them with their fields; whoever may change the project makes a
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
counts as one of the workspace's questions, and is held to the day's spending as a question is. The
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
run is listed with the others, by the automation and what arrived; its report counts as one of the workspace's questions, and a day past
the spending cap runs the checks without one, saying why. Whoever may change the project
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

Everyone who sees a project comments on its documents, viewers too, since a comment changes no
document (`backend/app/comments.py`, `GET|POST /api/documents/{id}/threads`, `POST
/api/threads/{id}/comments`, `PATCH /api/threads/{id}`, `PATCH|DELETE /api/comments/{id}`). The
"Comments" button on a document's tab opens its threads beside its text, the open ones counted on
it; "New comment" starts a thread about the words chosen in the text, the passage a citation or
the outline opened ("Comment on this" beside "Showing: …"), or the document as a whole. A thread
keeps the words it quotes, their page and the version they were in: a document's passages are
written anew at every reading, so its link to the passage goes with a new version while the quote
stays. Its writer changes and deletes a comment, an owner or admin deletes any, a thread goes with
its last comment, and anyone resolves a thread or opens it again. After "@" the box offers the
members who see the project (`GET /api/projects/{id}/mentionable`); whoever a comment mentions is
told, and the earlier writers of a thread are told of a reply, in the app and by email as each
chose, by who wrote and on which document, never the words, the notice opening the thread. A
document's comments go into the trash with it, travel in the project's export and a member's own
in their account's export, and stay without their writer's name once the account is deleted;
neither the agent nor the search reads them. The library's "Activity" lists what was done in the
project lately, newest first, for everyone who sees it (`backend/app/activity.py`, `GET
/api/projects/{id}/activity`): documents uploaded, given versions, deleted and restored, reviews
moved and assigned, comments and threads resolved, drafts, runs of the cross-checks and the
question sets, chats shared, each by names alone and leading to what it was about while that is
there; it reads the project's rows of the audit log, which record each comment added and deleted
and each thread resolved by the document's name, so it is kept as long as the audit log is.

The agent also writes drafts for the members to edit and send (`backend/app/drafts.py`, `POST
/api/projects/{id}/drafts`): a reply email, a dispute letter, a summary memo, a comparison table or
a report. The library's "Drafts" asks for one of a kind, saying what it should say, about the
documents open beside the library or about what the agent finds; a disputed document's review
offers "Write a dispute letter" about it; and asked in a chat for something to send, the agent
offers one under its answer (`create_draft` in `backend/app/agent/tools.py`), its kind, the
documents it found and what it should say, which the member's "Write the draft" asks for (`POST
/api/chats/{id}/messages/{id}/draft`), once, counting no question beside the one asked. Nothing is
written before that press, so a document whose text tells the agent to make drafts makes none,
and a viewer is offered none.
The background worker has the agent write it from what
the code read for it: each document with its facts beside the passages they came from, and what
the project's newest run of cross-checks found about them, which it states as found; it cites
passages as an answer does, and reads around them with its tools. A draft keeps every version: the
agent's, and each member's edit, saved as a new version from the newest (`POST
/api/drafts/{id}/versions`, refused when another was saved meanwhile), or the agent's writing it
again with what is asked now (`POST /api/drafts/{id}/rewrite`), each version with the passages it
cites as they were, so it reads the same after its documents are read again. Asking for one, or
for it again, counts as one of the workspace's questions and is held to the day's spending as a
question is. The drafts are the project's: everyone who sees it reads them (`GET
/api/projects/{id}/drafts`, `GET /api/drafts/{id}`, `GET /api/drafts/{id}/versions/{n}`), whoever
may change it asks for, edits and deletes them, each a row of the audit log by the draft's kind,
never what it says; a draft is kept as long as a chat, from when it was last written or saved.

Each member also keeps files of their own in a project, their My files (`backend/app/my_files.py`,
`GET|POST /api/projects/{id}/my-files`): Word documents and Excel workbooks that only they see,
never the project's other members, its search, facts, share links, the versioned API, webhooks or
export, until they copy one into the project's library (`POST /api/my-files/{id}/copy`), where it is
an upload like any, as a new document in a folder or, for a copy of a library's document, as that
document's next version. A file keeps every version (`POST /api/my-files/{id}/versions`, refused
when another was added meanwhile); each version a member uploads is scanned for malware before it
is served and is shown as it looks by the converter's PDF, both by the worker, and each can be
downloaded. A viewer keeps files too, since they change nothing the members share; copying one in
needs the role that uploads. The bytes count toward the workspace's storage, a member keeps
200 files in a project, and the files go when the member leaves the organisation, their account
is deleted or the project goes; deleting one is for good. The library's "My files" lists them and
opens each with its pages and versions.

Asked in a chat, the agent writes and edits Word documents in the asker's My files
(`backend/app/agent/writing.py`): it writes a new one from what it found, its citations numbered
notes naming the passages, reads one of theirs as numbered paragraphs with their styles and
tables, changes it by those numbers (a paragraph replaced, paragraphs inserted or deleted, words
found and replaced) keeping the formatting of everything it does not touch, and copies a Word
document of the project's library into My files to change it there, the library's document
staying as it was until the member copies the file back as its next version. Each write is a new
version, which the answer's step names and opens; an answer written again writes the same file
again rather than a second one. A document's text cannot have the agent write into the project:
at most into the asker's own files, every version kept. Excel workbooks come in a later change.

A project's question sets ask its documents the same questions on their own
(`backend/app/question_sets.py`, `GET|POST /api/projects/{id}/question-sets`, `PUT|DELETE
/api/question-sets/{id}`): every day or one day a week at an hour of its maker's zone, once new
documents arrive in the project (waiting, as an automation does, until none has arrived for two
minutes, at most a quarter of an hour after the first), or both, and whoever may change the
project runs one there and then (`POST /api/question-sets/{id}/run`). Each run is a report
(`backend/app/question_reports.py`, `GET /api/projects/{id}/reports`, `GET|DELETE
/api/reports/{id}`), which the background worker answers one question after another as a chat
would, citing passages, the agent told which documents arrived when they made it run; each
question counts as one of the workspace's questions and is held to the day's spending as a member's
is, and the questions a day past the spending cap has no room for say so in the report. Its report is
told to the set's maker, everyone who sees the project or nobody (a member's own run, to them),
in the app and by email as each chose for the notice, by the set's name and how many questions
were answered, never an answer; programs hear of it by the `report.completed` webhook and read
it from the API (`GET /api/v1/reports/{id}`). The library's "Question sets" lists the sets with
when each runs next and the reports, newest first, each opening its answers. Making, changing,
running and deleting a question set, or deleting a report, is a row of the audit log
naming the set and counting its questions, never one of them, and a report is kept as long as a
chat.
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

A project can take its documents by email too, where the deployment receives mail
(`backend/app/inbound.py`, `GET|PUT|DELETE /api/projects/{id}/mailbox`): the
library's "Email in" gives it an address of its own, `<token>@INBOUND_DOMAIN`, and whoever may
change the project chooses whom it takes mail from, besides themselves from their own confirmed
addresses: addresses and domains, but never a mail service's whole domain. A message from one
of them that the provider proved its sender's (DMARC, or DKIM of the From domain) becomes
documents in the address's folder: its text as a `.txt` file named by its subject, with its
From, To, Date and Subject above it, and each attachment as an uploaded file, through the
checks an upload's files go through. It is an upload of the member who sent it, told of as
their uploads are, or of nobody for a sender outside the organisation, and the project's
automations see it arrive as any upload. Anything else is turned away with nothing sent back; the page lists
the last messages, the documents kept from each and the files that were not, or why it was
turned away, kept 30 days. A new address replaces one given out too widely (`POST
/api/projects/{id}/mailbox/address`), and the old one then reaches nothing. The provider that
receives the domain's mail hands each message over as it was sent, with its verdicts on the
sender (`backend/app/receiving.py`): Resend through its webhook and API, Amazon SES through the
SQS queue its notices reach, which the worker waits on (deploying.md, "Mail in").

A project can also keep in step with a folder of a member's Google Drive, Dropbox, OneDrive or
SharePoint, where the deployment has an OAuth client for the service (`backend/app/connectors/`,
`GET|POST
/api/projects/{id}/connectors`): the library's "Connected folders" sends the browser to the
service for the member's consent to read their files, keeps the grant it gives encrypted, and
lets the member browse their folders and choose one, and where its files go in the project. The
worker syncs it every 15 minutes and at "Sync now": Google's change log or Dropbox's cursor says
whether anything changed, and if so the folder is read whole and compared with what the last
sync found, so that a new file becomes a document through an upload's checks, in the folders of
its path, a changed one becomes its document's new version (below, "The workspace"), and the
document of a file taken out of the folder goes to the trash; Google's own documents are exported as
Word, Excel or PDF files, and Dropbox's Paper documents as Markdown. A file is read once per
version, so a document deleted in the project stays deleted, and nothing goes to the trash
unless the folder was read whole. The documents a sync brings are its member's upload, told of
and seen by the automations as any. A connector pauses when the service no longer takes its
grant or its member no longer may change the project, and goes with its member's membership;
disconnecting it gives the grant back and keeps its documents (deploying.md, "Connected
folders").

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
empties it and has the worker write it anew from the same question, counting no other question;
a run of the earlier try that is still waiting on the model writes nothing
into the new one, since each try is numbered and its job names its own. A finished last answer
offers "Regenerate", and the chat's last question "Edit the question": either has the answer
written again in its place, from the question as it is or as edited (a chat named by its first
question takes the edit as its name), and counts as a question asked, since the member asked for
another answer; nothing else in the chat changes, and the answer's share links show it as it is
written again.

A chat is about the whole project, about one of its folders with the subfolders, or about
documents chosen from it, which "About: …" above the box says and changes: all the documents,
any folder, or a document open in a tab. The library starts such chats from a row's menu: "Ask
about this folder", "Summarise this folder" (which asks for the summary at once), "Ask about
this document", and for files chosen together "Ask about these n documents" and, for two,
"Compare the two documents". Each answer reads only what its chat is about as it starts, held by
the server as the project is (`backend/app/scopes.py`), whatever the model asks for; a chat whose
folder or documents were deleted, or are in the trash, reads nothing and its answer says so,
rather than reading the whole project.

A chat is its starter's alone until they share it with the project ("Share" in the chat's
header, "Make private" to take it back). Everyone who sees the project then finds it under
"Shared with the project" in the chat list, and a new chat offers the latest three, each with
the starter's name and the day of its last question: they read it and follow an answer as it
is written, but only the starter asks in it, renames it or deletes it, so the page offers the
others no composer, and a question of their own starts a chat of their own. A project opens
on the member's own latest chat, never on one another member shared. Sharing and taking
private are in the organisation's audit log, without the chat's title.

Each reader pins the chats they read to the top of their own list ("Pin chat" in the chat list,
`PUT|DELETE /api/chats/{id}/pin`, a row of `chat_pins` per chat and member): "Pinned" lists them,
the newest pin first, before the member's other chats and those shared with the project. A pin
waits with its chat in the trash and while it is private again, and goes with the chat or the
account; it is no audit action. The workspace's keyboard shortcuts (`frontend/src/shortcuts.ts`):
Ctrl+Shift+O (⌘ on a Mac) a new chat, Shift+Esc the box a question is asked in, Esc there Stop,
Ctrl+/ their list. None is a single key, and none acts while a dialog, a menu or the tour is open.

Whoever reads a chat downloads an answer of it ("Download the answer" under it, `GET
/api/chats/{id}/messages/{id}/export?format=docx|pdf|md`), or the whole chat (the download button
in the chat's header, `GET /api/chats/{id}/export`), as a Word document, a PDF or its Markdown
(`backend/app/chat_export.py`, laid out as a draft's version is). An answer's file is headed by its
question, a chat's by its title with each question a heading over its answer; a line says who asked,
in which project and on which day, in the downloading member's time zone, and the file ends saying
that a model wrote it. The citations are numbered across the file and listed at its end as notes
naming the document, section and pages of each passage; an answer stopped, failed or still being
written says so. Each download is a row of the audit log by the chat's id and the format, never
what was asked or answered.

What an organisation uses is recorded as it happens, one row per measure in `usage_events`:
an answer's model calls and tokens, an upload's bytes and pages (once, by its first parse; a
document without pages, a Word file, a sheet or a text file, has a page for every 3,000
characters of its text begun, `text_pages` in `backend/app/usage.py`), the pages a scan took to read
and the model calls of the indexing stages, the texts embedded and the tokens the embeddings
endpoint says they took, and the pages rendered for the viewer, each with the organisation, project and member they belong to and the message or
document they were for. Each is counted at the end of the run that made it, so an answer that
was stopped, a scan that failed at its tenth page and a stage a worker lost are counted for
the calls they made. `uv run python -m app.maintenance usage <slug>` (in `backend/`) prints an
organisation's use per day in UTC, by kind and unit, over the last 30 days or `--days`, and
`costs` what the models cost over the same span, against `DAILY_SPEND_CAP`, by organisation
(one, an install holding one workspace) and by kind of work and model, then by kind and version
of its prompts: each kind's prompts have a version, a hash of what the model is
told for it that any edit changes (`backend/app/prompts.py`), which the rows of its model calls
carry.
An answer shows what it used under its text (the model, its calls and tokens, and, to the
organisation's owners and admins alone, the cost, which the server sends no member or viewer:
`backend/app/costs.py`); a document's tab shows nothing of what bringing it in and indexing it
used, which the API still gives as its `usage`. The figures are kept on the message and the document as the
rows are recorded, summed over every run. The costs follow `MODEL_PRICES` in `.env`, what
each model costs per million tokens in and out, in `PRICE_CURRENCY`, an embedding model's
under the name `EMBEDDING_MODEL` gives; without prices, no cost is shown.

A day of model use has one limit, `DAILY_SPEND_CAP`, measured against those costs since
midnight UTC (`backend/app/spending.py`): the most the operator will see on the providers'
bills in one day. At it every question is refused with 503 and a `Retry-After` until midnight,
and an indexing stage that needs the model (reading scans, writing summaries and facts) waits
for midnight UTC and says so on the document, while the outline is built without the model so
that the document can be searched today. A question that was queued before the cap was
reached ends with the sentence as its error. With `ALERT_EMAIL` set, the worker mails a
warning once a day when a day's spending reaches 80 % of the cap. The cap counts nothing
without `MODEL_PRICES`, and it holds the next piece of work rather than the ones running, so a
day can run past it by what was in flight: a document's summaries and facts by `ENRICH_MAX_REQUESTS` requests at
most. The brake for a bill that cannot wait for midnight is a provider switched off: from the
Administration page, which every process heeds within seconds, or with `DISABLED_PROVIDERS` at
the next restart; its models are asked no more, and the app runs as it does without a key, saying
why. Set a budget at each provider as well (Google Cloud
billing budgets for the Gemini key, the Anthropic console's spend limits, OpenRouter's credit
limit): the app counts what it knows the price of, the provider counts the bill.

The workspace's usage page (`/organization/usage`, from its page) shows every member what it
holds now (its projects, its documents, the bytes of their originals with the earlier versions
and the members' own files, its members with the open invitations) and its pages indexed and
questions asked on each of the last 30 days and in each of the last 12 months, in UTC, and its
owners and admins what the models cost beside them (`backend/app/api/usage.py`). A question
counts as it is asked, answered or not. Nothing is held to a plan and nothing is sold: what
bounds the work is the server's own ceilings (a file's size and an upload's files, the rates a
minute, the answers written at once, a document's requests to the model), set in `.env`
(deploying.md, "Every setting"), and the day's spending cap above.

The workspace's owners and admins read what the maintenance commands print in a browser on its
Administration page (`/admin`, from the account's menu or the workspace page): the job queue
with its failed jobs and the workers, how the models and services stand, and what the models
cost by kind of work and model against the daily cap (`backend/app/api/admin.py`). From there
they also queue failed documents again from the stage each stopped at, and switch a model
provider off or on again for every process without a restart; each is a row of the audit log,
theirs. The workspace page's members list tells them when each member was last seen. Until
2026-11-13 this was the operator's area, a page of its own at `/operator` for the accounts a
command named.

## The workspace

A project opens as two panes. On the left is the **library**: the project's documents filed in
folders, with the usual file-manager moves (new folder, rename, drag to move, delete, upload by
button or by dropping files or folders, search, and on a phone or a tablet a photo of a page
taken with the camera, `capture="environment"`, named by when it was taken), shown as a tree that opens folders in place, a
folder's files in the order chosen (by name unless another is) a hundred at a time, with more on request. A folder uploaded is filed as
it was, its folders made where the one it goes into holds none of their names. An email saved
from a mail program (.eml) is opened as it is uploaded (`app/unpacking.py`): its text becomes a
document headed by its From, To, Date and Subject, as mail sent to the project's address does,
each attachment a document beside it, checked as any file is, and a ZIP archive is unpacked
into the folders its paths name (into a folder of its own name unless it holds one folder
already), the archives of one upload bringing `MAX_UPLOAD_FILES` files and
`MAX_UPLOAD_REQUEST_MB` unpacked between them, read from their directories before a byte is
inflated, so a zip bomb is refused unopened; a program's upload, which pairs the documents
kept with the files it sent, is refused either. An upload goes in
as few requests as the deployment's limits allow, one after another, each its own upload for the
notice that follows it, and a panel shows how far it has gone as a whole and file by file, until
its Stop; a type Emudoc does not read and a file too large are left out before anything is
sent. Once sent, the same panel follows its documents as they are indexed ("12 of 40 ready; 26
being indexed; 2 could not be indexed."), lists those that could not be with why, each opening
in its tab, and stays until its cross or the next upload. An upload begun while the browser is offline waits and starts by itself once it is back;
one cut off on its way offers the files not sent again, a file that got through before the cut
coming back as already in the project. The last deletion, move or rename is
offered back at once with "Undo", until the next one or its cross. Opening a file adds a third pane in
the middle, the **document**, with a tab for each open file; the tabs stay open across reloads, in
the order they are dragged into, and closing the last one leaves
the library and the agent side by side again. A tab pinned from its menu stays before the others,
shows a pin in place of its cross, and stays open through "Close others" and "Close all". As more
files open the tabs narrow together, each name and then its status dot giving way, down to the
file's icon and its cross; past that the row of tabs scrolls. A tab closed with the mouse leaves
the others as wide as they were until the mouse leaves the row, so the next tab's cross comes
under it.

Everything there works by the keyboard as well (WCAG 2.2 AA). The library is one stop of Tab:
the arrows move through its rows, which a screen reader reads out by name and state (its star and,
in a place across the folders, where it is), or in the grid by a tile and by a row, Enter opens
one, F2 renames, Delete deletes, and Tab goes on to the actions of the row it is on, whose "Move
to…" asks for a folder in a dialog, the way to move without a drag. The tabs open with the arrows,
close with Delete and move with Ctrl+Shift and an arrow (⌘+Shift on a Mac), or "Move left" and
"Move right" in a tab's menu, the text of a document scrolls once focused, and the dividers between the
panes move with the arrows. A click on a divider shows two buttons beside it that narrow or widen
the pane before it, for whoever cannot drag; a double-click puts the panes back to their starting
widths. Every menu and panel takes the keyboard as it opens, gives it back
to its button on Escape, and lets Tab go on past it.
PDFs show their rendered pages with the extracted text laid invisibly over them, so it can be
selected and searched, and so do a TIFF's pages and a HEIC photo, which a browser does not show
itself. A PDF page whose text runs down, up or upside down as it is shown (a landscape table
drawn sideways on a portrait page, a page turned by a rotation it did not need) is read the way
its text runs, and its text and a cited passage's mark lie where the page shows them; a line
turned against the rest of its page, such as a reference up the margin, is read after the
page's own text and heads no section. A page set in columns is read a column at a time, its
headings, paragraphs and tables in the column they stand in, and a heading, a table or
whitespace across the page starts the columns below it afresh; spreadsheets show one tab per
sheet; images show the picture above the text read from it. Where the deployment runs the
converter (`CONVERTER_URL`, below), a Word file, a presentation, a spreadsheet, an RTF or an HTML
file opens as it looks, page by page: the worker sends the file, once parsed, to LibreOffice in a
container of its own and keeps the PDF it makes beside the original (`backend/app/conversion.py`,
its view), whose pages are drawn as a PDF's are. "Original" and "Text" above the tab switch to its
text with the outline, where a cited passage is marked, so a citation opens there; a file
uploaded before, or while the converter was away, has its view made when it is first opened. A scanned page or a photo is read by
the model as it stands, turned sideways or upside down, in columns or in handwriting: a word it
cannot read is written [illegible], a signature [signature] with the name where it can be read,
a box [x] or [ ], and a table, ruled or not, comes out a table whose rows give facts like any
other's. Printed, from the tab's "Print" or the browser's own, a document is itself alone
(`data-print` and the print rules in `frontend/src/index.css`): a PDF its pages, a sheet each and
as large as the sheet holds, anything else its title and text over as many sheets as it takes,
in the light theme, under the document's name, which a PDF saved from the print takes; a share
link's page prints the same way. On the right is the **agent**. Clicking a citation in an answer opens the cited
document in the middle at that passage, with the library still beside it. The agent sees the
folders too: ask about "the invoices in Finance/2026" and `list_documents`, `search` and
`query_facts` work within that folder.

A document is given a **new version** from its menu in the library or its tab's "Versions"
(`backend/app/versions.py`, `POST /api/documents/{id}/versions`): a file of any type the upload
takes, through the same checks, which the document reads in its earlier file's place, keeping its
id and so its chats' scope, its review (an approved or disputed one opened again, to review), its
share links and its members' checks of facts that read the same. Only the current file is
searched, asked about and read for facts; each earlier one is kept, its file under the document's
prefix in the storage and its text with its row (`document_versions`), for the tab's "Versions" to
download it and show its text, the project's export to hold it, and the usage page's storage to count
it. A new version's pages count again as it is read, and it is an upload of its own, its notice,
automations and `document.ready` following its reading.

The library reads a project more ways than by its folders. Above the files, a row of icons shows
all the files in their folders, or instead, across all of them, the member's own **favourites**
(starred from a file's menu) and **recent** documents (the last 20 of the project they opened in
the middle pane, `backend/app/recents.py`), the icon of the place shown pressed, and in the same
group the way to the project's trash. The path above them is always there, "All files" alone at
the top, so that choosing a place moves nothing. The box under them **searches the project** in
the library's place: first the **files whose names hold the words** (`name` on the listing: the
letters anywhere in a file's name, in any case, from every folder of the project, each file shown
with its folder, or narrowing the favourites or the recent ones), then below them the
**passages** the words find, by their exact words and by their meaning, each opening its
document at the passage in a tab of the middle pane while the search stays in the library beside
it. The passages are narrowed by the documents' types, a span of the documents' own dates, review states and a folder with its
subfolders (their "Filter" dialog, each part a chip that takes it off; the search route takes them
as query parameters, `SearchIn`, and `backend/app/document_filter.py` resolves the documents' ids
before the ranking); "Search within these" makes the words searched one of those parts, so that
the next words search only the documents with a passage holding every one of them. The projects
page's box searches **every project** the member sees at
once (`/search`, `backend/app/api/search.py`, one ranking over all their passages, each hit
naming its project and opening its document at the passage), narrowed to some of those projects,
to types and to a span of dates; a private project is searched by those who see it alone, and
the agent and the versioned API still search one project. Any place is listed by name, as added, by the documents' own dates (the
undated after the dated) or by size, either way, each order a keyset of its own on the
server, and shown as the tree or as a **grid** of tiles, each file's the thumbnail the server
draws of a PDF's first page, a scan or an image, the browser remembering both choices.

Files **chosen together** in the library (the box at each one's left, Ctrl or Shift and a click,
Ctrl+A, Space and Shift with the arrows, never a plain click; the bar under the files counts them
and offers their menu), up to 100 at a
time (`backend/app/selection.py`), are moved and deleted together, and by whoever may change the
project indexed again ("Index n documents again", or "Index the rest" of the partly indexed among them; one being
indexed already is left to its run and counted), and exported alone ("Export n documents": the
project export's ZIP file of those documents, without the chats). Each change is one request
that checks every chosen document is the project's and out of the trash, and is refused whole
when one is not (`POST /api/projects/{id}/documents/reprocess`).

A document's **details**, its title, type, date and language, are what its reading found until a
member who may change the project sets one of them in their place from its tab's "Edit details"
(`backend/app/details.py`, `PATCH /api/documents/{id}/details`): the member's value is the one
the library, its date order, the search's filter, search hits and the agent read,
and it holds through "Index again" and a new version, while what each reading finds is kept
beside it for the dialog to show and "Use the reading" to take again; a title set starts the
outline's paths, which the passages are searched with. Its language is the one its words are searched in:
the parse stage tells it from the document's first 20,000 characters of words (py3langid's
model, on the worker, which knows 97 languages; the database stems 28 of them, and a document in
another, Polish say, is searched by its words as written), and a document whose words are too
few or too mixed to tell, a delivery log of dates and amounts, follows its organisation's
language (`backend/app/languages.py`). The agent is told a document's language where it is not
the organisation's, so that it searches the document by its own words. A language set moves the
document's passages by the same background job a change of the organisation's language queues.
The language also says how the document's dates and amounts are written (`backend/app/locales.py`):
the model reading its facts is told that German puts the day first and a comma before the
decimals, and a fact's value that is a figure alone, "1.250,00 €" or "03.04.2026", is read again
from its marks, the language settling only what they leave open ("1.250" is 1250 in German and
1.25 in English), which replaces the model's number or date where it is certain. English puts the
day first in Britain and the month first in America, so an English "03/04/2026" is left to the
model to read by the document's country or its other dates, or left out. A document without a
language of its own is read by what its values settle alone, the model told the language its
organisation chose, if it chose one, as a likelihood. The facts of documents read before change
when they are read again ("Index again").
Search by meaning needs no language: the default embedding model, EmbeddingGemma, puts a
question and a passage that says the same near each other whichever languages they are written
in, so an English question finds the German clause that answers it, whose words it does not
share (`deploying.md`, "The embeddings endpoint"). A document has a **note**, one text its project's
members share (`backend/app/notes.py`): whoever may change the project writes it on the tab,
everyone who reads the document reads it with who changed it last, and it travels with the
document into the trash and the project's export; neither the agent nor the search reads it.
And a file held in more than one of the projects a member sees has its **copies** named on its
tab, each a link to it there, and listed on the projects page's "See the duplicates"
(`backend/app/duplicates.py`, `/duplicates`), by the same bytes alone.

Deleting puts things in a **trash** rather than removing them (`backend/app/trash.py`): a file
or a folder with everything in it goes into its project's trash, which the trash icon above
the library's files opens, and the library offers to undo the deletion at once; a chat goes into its
starter's trash, which the chat list's "Deleted chats" opens; a project goes, with everything
in it, into the organisation's trash under the projects. What is in the trash is out of the
library, the search, the agent's answers and the counts, the usage page's among them. A member restores it as it was (a folder with what
went with it, into the nearest folder that is still there, renamed if a name was taken
meanwhile), or deletes it for good with its files, once a
dialog has said what goes with it (the files a folder takes, what a project holds); after 30
days the housekeeper deletes it for good. A viewer reads the trash and restores only their own
chats. A document in the trash keeps its place: uploading the same file again is refused with
the way to restore it, which costs none of the indexing again, and its indexing waits until it
is restored.

## Quick start

On a server with Docker, a name that leads to it and ports 80 and 443 open, the image from GHCR
(amd64 or arm64) with everything it needs beside it:

```bash
mkdir emudoc && cd emudoc
curl -fsSLO https://raw.githubusercontent.com/emudocai/emudoc/main/deploy/compose.yml
curl -fsSL -o .env https://raw.githubusercontent.com/emudocai/emudoc/main/deploy/.env.example
chmod 600 .env && sed -i "s|^POSTGRES_PASSWORD=$|&$(openssl rand -hex 24)|; s|^ENCRYPTION_KEYS=$|&$(openssl rand -base64 32)|" .env
docker compose up -d --wait          # after setting APP_URL, a model's key and COMPOSE_PROFILES in .env
```

The fourth line makes the database's password and the key the database's secrets are encrypted
with; keep a copy of `.env` apart from the backups. In `.env`, set `APP_URL` to the address
people will open, the models and their provider's key (Gemini's by default), and
`COMPOSE_PROFILES=tls` for Caddy to answer on 80 and 443 with a certificate from Let's Encrypt,
adding `scan` for ClamAV and `office` for Word, PowerPoint and Excel files shown as they look,
each with the line beside it. Then open `APP_URL`: a new install opens its setup page, which
makes the first account and the workspace. `docker compose up -d --wait` starts Postgres with
pgvector, applies the migrations, and starts the API, a worker and the embeddings server.
`docker compose run --rm backup` writes a backup of the database and the files, to run every
night from a timer and copy off the server; `deploying.md` says how ("Backups"), how to update
and go back ("Upgrades"), and the rest. The image is published from the
first release, v0.1.0; until then, build it from a checkout as below and name it in `.env` as
`EMUDOC_IMAGE=emudoc:<commit>`.

## Run it

For development: you need Docker, [uv](https://docs.astral.sh/uv/) and Node with pnpm.

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
a user of its own on a read-only filesystem and refuses development's settings. A version tag
(`v0.1.0`) publishes it for amd64 and arm64 as `ghcr.io/emudocai/emudoc`, tagged `0.1.0`,
`0.1`, `latest` and the commit, with that version in the menu (`.github/workflows/publish.yml`).
`deploy/compose.yml` runs it on one machine with Postgres, the migrations first, as the quick
start above does; `deploying.md` ("The image") says what a container needs.

What changed, release by release, is in [`CHANGELOG.md`](CHANGELOG.md). Emudoc is free software
under the GNU Affero General Public License, version 3 only ([`LICENSE`](LICENSE), and
[`NOTICE`](NOTICE) for the other projects' software it uses); the help menu's "Source code"
offers the running version's source to whoever uses a deployment, as the licence asks
(`deploying.md`, "The source code"). [`CONTRIBUTING.md`](CONTRIBUTING.md) says how to
contribute.

The database schema is a set of migrations in `backend/migrations/`; `uv run alembic upgrade
head` applies the ones the database has not seen yet, so run it again after updating. The backend
never alters the database itself: at start it checks that the schema is current and refuses to
serve otherwise, naming that command. A database made by a version from before there were
migrations cannot be upgraded; the command says so, and `docker compose down -v` resets it.

For semantic search, run [Ollama](https://ollama.com) with `ollama pull embeddinggemma`
(Google's EmbeddingGemma, under the Gemma Terms of Use), or point `EMBEDDING_BASE_URL` at any
OpenAI-compatible embeddings endpoint. The backend asks it for one vector at start and logs what
it found. A deployment runs llama.cpp's server with the same model beside the app
(`deploy/compose.yml`), whose vectors agree with Ollama's.

When working on the app, run `./dev.sh` instead and open <http://localhost:5173>. It starts the
database, installs what the frontend's lock file asks for, applies the migrations, and runs the
backend (restarting when Python changes) and the frontend dev server (updating the open page as
you edit, no build needed), which proxies `/api` to the backend. Ctrl+C stops both servers. Port
8000 still serves the last build, so it does not show your edits. `dev.sh`, the browser test and
the load test use fixed ports, so they run in one checkout at a time.
## Tests

```bash
cd backend && uv run pytest          # needs the database container
cd backend && uv run pytest --shard 1/3   # a third of the suite, as each of CI's backend jobs runs
cd frontend && pnpm lint && pnpm test && pnpm build
cd frontend && pnpm e2e              # after pnpm build; needs the database container
```

The backend's suite holds every parser to a corpus of files (`backend/tests/corpus`: the sample
project's, and at least one of each kind the app reads, a scan, a page set in columns and one
turned sideways among them), each beside a snapshot of the blocks, the language and the outline
the parse and structure stages make of it, so that a change to a parser or an update of a
library it reads with that reads a file differently fails the suite. A change meant to do that
writes the snapshots again with `cd backend && uv run python -m tests.corpus`, and the diff
shows what each file now reads as.

The frontend's unit tests (`pnpm test`, Vitest) sit beside the modules they test
(`src/**/*.test.ts`) and run in Node with `fetch` stubbed: the chat's events and its
reconnections, the workspace's URLs, the formatting. `pnpm e2e` (Playwright) drives the built
page in Chromium as a new member: set the install up on its setup page, skip the tour, ask the
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

CI (`.github/workflows/ci.yml`, GitHub Actions) holds all of it, on every pull request and every
commit on `main`, on GitHub's runners, with no secret, so that a pull request from a fork runs
it whole; `main` takes a commit only through a pull request whose checks passed
(`.github/rulesets/main.json`; `CONTRIBUTING.md`, "Checks"). The checks: the backend's
lint, format and suite against Postgres, in three jobs of a third of the tests (the Pooler
workflow runs the suite through pgbouncer in transaction mode on a change to how the app talks
to the database); the frontend's lint, unit tests and build; the browser
test, and the load test at a size that takes a minute; the image, built and then
started by `.github/scripts/check-image.sh` (`deploy/compose.yml` run from it with the quick
start's `.env`, in production: Postgres, the migrations, the API, a worker, the embeddings server
and the converter); the deployment's embeddings server, started from the
compose file by `.github/scripts/check-embeddings.sh` and asked for the vectors in
`.github/scripts/embeddings.json`, which are those the deployments' databases hold (its own
workflow, `embeddings.yml`, on a change to them); shellcheck on every shell script; actionlint
on the workflows; promtool on the example alerting rules (`deploy/prometheus`); and trivy on the
Dockerfile and the compose files, with what was looked at and accepted in `.trivyignore.yaml`, each with its reason;
that uv, pnpm, Node and Python are named at the Dockerfile's versions wherever else they
appear (`.github/scripts/check-versions.sh`); and gitleaks over every commit, for keys and for
any `.env` file whatever it holds (`.gitleaks.toml`, which says what was accepted and why).

A key that CI finds has been pushed, and has to be revoked. The pre-commit hook in `.githooks`
runs the same gitleaks on what is staged and refuses the commit instead; it needs Docker, and
is turned on once per clone:

```bash
git config core.hooksPath .githooks
```

The dependencies and the images are audited for known vulnerabilities every Monday and once a
change reaches `main` to a manifest, a lock file, the Dockerfile or the deployment's compose files
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

The suites answer for the models from recorded responses, so the models themselves are asked by
hand, with the deployment's keys: `python -m app.maintenance models` against every model and
fallback the deployment names, and `python -m app.maintenance golden`, thirteen questions about
the sample project whose answers are known, asked of the agent and graded on what each answer
says and cites, then judged by the indexing model, which says whether each answer is right and
each cited passage says what its sentence does (`backend/app/golden.py`); both after a change to
the prompts, the agent, the sample or the models (`deploying.md`, "The models"). `python -m
app.maintenance fact-accuracy`, run by hand too, indexes the sample with extraction templates on and scores the facts
the indexing model reads from its contract, invoices and price list against values labelled by
hand (`backend/app/fact_accuracy.py`), each field's recall and precision. `python -m
app.maintenance injection`, run by hand too, indexes a supplier's documents that each hold an
instruction planted for whoever reads them and asks the agent about each, failing when the
indexing model wrote what an instruction dictated or an answer did what one said
(`backend/app/injection/`). The reports name the versions of the prompts they ran, so two runs
with the same versions differ by the model alone.

Dependabot (`.github/dependabot.yml`) proposes the updates of both lock files and of the
workflows' actions once a week, a week after their release, and those of the images, which
are pinned by digest: the image's bases in the `Dockerfile` and the compose files' images, a
newer version a week after its release and a rebuilt tag's new digest at once.

The backend tests use a separate `emudoc_test` database (`emudoc_test_gw0` and on for each of
`-n`'s workers) and make no network calls: the AI
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
  search.py     hybrid search over passages, of one project or of every one a member sees
  languages.py  the language a document is searched in, detected or its organisation's, and the move
  locales.py    how a document's language writes dates and amounts, which its facts are read by
  folders.py    the folder tree as paths and subtrees
  agent/        the five tools and the tool-use loop
  accounts.py   who a request acts as; signing up makes a user and their organisation
  sample/       the sample project the setup's workspace starts with: its files, the index
                the pipeline made of them, the copy and the build-sample command's build
  onboarding.py the organisation's first steps, read from what it did, which the invite
                suggestion reads, and the account's choices about the tour and the suggestion
  invitations.py
                invitations to the workspace
  auth.py       passwords, sessions, one-time links and authenticator codes
  apikeys.py    the API keys a program calls the API with, as the member who made them
  share_links.py
                links that show a document, or an answer, to whoever holds them, with no account
  idempotency.py
                a program's Idempotency-Key: a request sent again is given the first answer
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
  spending.py   the daily spending cap, its checks and the warning as a day nears it
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
  client/       the upload client members download and run, a script of the standard library
  api/          HTTP endpoints
  api/v1/       the versioned API programs build on, its models, and its published contract
  headers.py    the headers every response carries for the browser
  answers.py    an answer as a job: the worker writes its events, the API streams them
  scopes.py     what a chat is about, a folder or chosen documents, which its answers read alone
  llm.py        every call to the AI model, and the fallback when the first model cannot answer
  trial.py      the models command: each model asked what the app asks of it
backend/migrations/
                the database schema as Alembic migrations, one file per change
frontend/e2e/   the browser test, the app's start for it and the stand-in model it runs against
frontend/src/
  pages/        the Projects page, the search of every project and the Workspace (two panes)
  workspace/    the library, tabs, document viewers and the chat pane
  account/      sign-in, sign-up, the email links, and the account page
  api.ts        the typed client for the backend
  auth.ts       who is signed in, for the shell and its pages
  chat.ts       a chat's messages as the chat pane holds them, and the answers being followed
  scopes.ts     what a chat is about in words, and the questions a chat about part of it asks
  onboarding.ts when the chat suggests inviting the team; tour.ts the workspace's tour, which
                components/Tour.tsx shows
deploy/
  compose.yml   the image on a team's server: Postgres, the migrations, the API, a worker and
                the embeddings server, and behind profiles ClamAV, the converter and Caddy;
                .env.example beside it is the .env it reads
  restore-drill.sh
                the restore drill: the newest backup into a throwaway copy, checked against the
                live storage
  managed-restore-drill.sh
                the same drill for a managed database, with the provider's restore and delete
                as two commands; rds-drill.sh is RDS's
  prometheus/   an example scrape configuration and the alerting rules, with their promtool
                tests
Dockerfile      the one image for every process; .dockerignore names what goes into it
.github/        CI: the checks of every push and pull request (workflows/ci.yml), the image
                published to GHCR on a version tag (workflows/publish.yml), the rules
                main is held to (rulesets/main.json), the weekly audit of the
                dependencies and the image (workflows/audit.yml), Dependabot's updates
                (dependabot.yml), the image's smoke test (scripts/check-image.sh) and the
                tools' versions (scripts/check-versions.sh)
.githooks/      the pre-commit hook that refuses a key or a .env file (git config
                core.hooksPath .githooks)
CHANGELOG.md    what each day's commits changed for members and operators
LICENSE         the GNU AGPL-3.0; NOTICE the copyright and the other projects' licences
CONTRIBUTING.md how to contribute, the checks and the contributor licence agreement (CLA.md)
```

## Not built yet

- The public API: SDKs (Python, TypeScript) generated from its contract are to come.
- Office files in the format from before 2007 (.doc, .xls, .ppt), which the upload turns away
  with how to save them as today's.
- A document's earlier version made the current one again: it is downloaded and uploaded as a new
  version; an upload of a file of the same name as one in its folder is a new document, never a
  version, until asked.
- Copies found by their text rather than their bytes (a file saved again, a scan of a printed one),
  and copies within one project, which takes a file's bytes once.
