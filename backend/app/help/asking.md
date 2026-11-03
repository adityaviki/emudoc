# Searching and asking

## Searching

The search box above the library finds passages by their words and by their meaning together,
so "late delivery penalty" also finds a clause about liquidated damages. It searches the
project, or the folder you are in, or a tag's documents while the library shows them. Words
are matched in each document's language, so that "invoices" also finds "invoice": the one the document was read to be written in, or the one your organisation chose
on its page ([A document's details](/help/documents)). The passages that hold every word you typed come first, then
those that hold all but one of them, then those that hold any.

- **Filter**, above the results, narrows a search to the documents of some types, dated within
  a span, carrying tags, in review or in a folder. Each part shows beside it, and its cross
  takes it off.
- **Search within these** keeps the words you searched for and empties the box: the next words
  search only the documents with a passage that holds every one of them. Do it again to narrow
  further, up to $within times.

To search every project you see at once, type in the box above the projects on the projects
page. Its page lists the best passages first wherever they are, each with its project's name;
click one to open its document at that passage. **Filter** there narrows the search to some of
the projects, to documents of some types or to those dated within a span. A private project is
searched only by those who see it: its chosen members, and the organisation's owners and admins.

## Asking

The chat beside the library answers questions about the project's documents. Emudoc's assistant
looks for what it needs itself: it lists the documents, reads their outlines and summaries,
searches them and reads the passages it found, and it can compare the facts read from several
documents, such as an invoice's prices against the price list. Every claim in an answer cites
its passage with a numbered chip: click it to open the document at that passage and check it.
Answers are written by an AI model and can be wrong; the citations are there so that you can
tell. Stop ends an answer early, and an answer goes on while you look elsewhere, or after you
reload the page. Try again, under the last answer of your chat when it was stopped or failed,
writes it anew in its place. Regenerate, under the last answer once it is finished, writes it
again from the same question, and Edit the question, beside your last question, lets you change
it and have it answered again in its place; each counts as a question.

A question can be up to $question_chars characters; for more, upload the text as a document and
ask about it. New chat starts afresh, and the empty chat suggests a few questions, which the AI
model writes from the project's documents once they are indexed. Under the last answer of your
chat, "Ask next" suggests two or three questions that take it further: click one to ask it, as if
you had typed it. A suggestion is only asked when you click it, and counts as a question.

The chat's title, at the top of the chat, opens the list of your chats. Pin chat, beside a chat
there, keeps it at the top under "Pinned", one you started or one shared with the project; your
pins are yours alone, and Unpin chat puts it back among the others. Under an answer, a line says
which AI model wrote it and how many calls and tokens it took, and to the organisation's owners
and admins what it cost; a document's tab says the same of reading it.

## Keyboard shortcuts

In a project, Ctrl+Shift+O (⌘+Shift+O on a Mac) starts a new chat, Shift+Esc goes to the box you
ask in, and Esc there stops the answer being written. Ctrl+/ (⌘+/) lists them. They work while
you type in a field too, and wait while a dialog or a menu is open.

## Asking about part of the project

About, above the box you ask in, says what the chat is about and changes it: all the documents,
a folder with its subfolders, or a document open in a tab. The assistant then reads only those,
whatever the question, and tells you when you ask about anything else. The library starts such a
chat from a folder's or a document's menu: Ask about this folder; Summarise this folder, which
asks for its summary at once; and Ask about this document. Choose several files with Ctrl (or ⌘)
and a click for Ask about these documents, or two of them for Compare the two documents. A chat
whose folder or documents were deleted reads nothing until you choose again what it is about.

## Who sees a chat

A chat is yours alone until you share it with the project: Share in the chat's header lets
everyone who sees the project read it, under "Shared with the project", and only you ask in it.
Make private takes it back. A chat is kept after its last question as long as the
organisation's plan keeps chats ($chat_retention), or less where its owners or admins chose so;
a deleted chat goes into your own trash, under "Deleted chats".

Whoever reads a chat can keep it outside Emudoc: Download the answer, under an answer, saves it
with its question as a Word document, a PDF or Markdown, and the download button in the chat's
header saves every question and answer of the chat in one file. Each citation becomes a numbered
note naming the document, section and pages of its passage.

## When the assistant cannot answer

The header says when the AI model or the search by meaning is out of reach: search then matches
words alone, and asking is off until the model is back. A question past the
organisation's questions for the month, or its spending for the day, is refused with what
lifts the limit ([Plans and usage](/help/plans)).
