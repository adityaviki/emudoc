# Facts and checks

Everyone who sees a project reads what this page describes; whoever may change the project
(its members, admins and owners, not its viewers) makes and changes it. The buttons named here
are among the library's, above its files.

## Extraction templates

A template names a kind of document and the fields its facts are read for: an invoice's number,
date, supplier, lines and total, say. Emudoc has $built_in built in, and every new document's
facts are read for its kind's. **Extraction templates** lists them: "Customise" makes a built-in
template the project's own, to change, and "New template" makes one for a kind of its own, up to
$max_templates a project. Each field has a name, a type, what it is, a few examples, and whether
every document of the kind states it. Once a template is saved, a notice offers to read the
documents it changes again, and "What changed" lists, document by document, the values the new
reading changed.

## The facts table

A template's "Facts table" shows the facts of every document of its kind, a row a document, a
column a field: sort it by a column, find words in it, keep the rows that meet a condition (a
field stated or not, at least or at most an amount, on or after a date), or only those with a
fact nobody has checked ("Unchecked"). Click a cell to see where its value is written, with its
quote, and "Open in the document" there. "Check" marks a value as checked, under your name, and
"Correct…" sets the right one, which a later reading keeps. "Download" gives the table as a CSV
file, or as an Excel workbook with each fact's source.

## Cross-checks

A cross-check compares a fact of one kind of document with another's: an invoice's prices with
the price list, its total with its purchase order, its payment terms with the contract.
**Cross-checks** offers some suggested for the project's templates, each added with "Add", and
"New cross-check" makes one of your own, up to $max_cross_checks a project: what it checks, how it
compares (an amount or a date equal to the other, at most or at least it, within a margin if you
like; a text the same words or numbers), what with, and which documents are paired.
"What it finds" lists, document by document, where the facts differ, where one is missing and
where they hold, worked out afresh each time it is opened, without the AI model.

"Run the checks" keeps what every cross-check finds at that moment as a run, which stays as it
was while the documents change; "With a discrepancy report by the model" adds a report that the
AI model writes of what differs, citing where each figure is written, as an answer does.

## Automations

An automation runs the cross-checks for you as documents arrive: under Cross-checks, "New
automation" says what it waits for (a kind of document, in a folder or anywhere in the project),
which cross-checks it runs, with a report or not, and who is told what it found. It runs once
uploads have stopped for
$automation_wait, so that a batch is checked once. A project has up to $max_automations, each
turned off and on again with a press.

## Question sets

A question set asks the questions you ask again and again for you: **Question sets**, "New
question set", up to $max_set_questions questions, one a line, asked every day, one day a week at
an hour of your time zone, once new documents arrive, or both. Each round of answers is a
report, citing the passages as a chat does, and its reports are told to whoever made it,
everyone who sees the project, or nobody. "Run now" asks them at once, and that report is told to
you alone. A project has up to $max_question_sets sets.

"What arrived this week", a notice you turn on from your [account page](/account), names on
$digest_day morning, after $digest_hour in your time zone, the documents that arrived over the week
in each project you see.

## Who is told

A run an automation made is told when it finds something about what arrived, and a question
set's report as its set says: $told. A notice says what happened and where, never an answer.
Choose how each kind reaches you on your [account page](/account)
([Your account and sign-in](/help/account)).
