# Uploading and organising documents

## What you can upload

$formats. A file may be up to $max_upload_mb MB$max_pages. Upload files with Upload, or a folder with the
folders in it with "Upload a folder", or drop either on the library or on a folder in it. On a
phone or a tablet, the camera button beside them takes a photo of a page and uploads it. A larger
upload is sent $max_upload_files files or $max_request_mb MB at a time, and a panel under the
toolbar shows how far it has gone, file by file under "Each file", until Stop. A file Emudoc does
not read is left out before anything is sent, and so are a folder's hidden files. A file saved
with a password cannot be read: save it again without one. A scanned page, and each page of a
TIFF, is read by the AI model as an image$ocr_pages. It is read the way its text runs, even
photographed sideways, handwriting included: a word the model cannot read is written
[illegible], a signature [signature], a ticked box [x]. A PDF set in columns is read a column at
a time, and a page printed sideways the way its text runs. An email saved from your mail program
(.eml) is opened: its text becomes a document headed by who sent it to whom and when, and each
file attached to it a document beside it. A ZIP archive is unpacked as your computer would,
into a folder of its name unless it holds one folder already, its folders made as an uploaded
folder's are; the archives of one upload may bring $max_upload_files files and $max_request_mb MB
unpacked between them. $scanning

## How a document is read

An upload is read in the background, in steps that its status ring fills one by one: the file
is checked, its text is read, its sections are found, each section is summarised and its facts
(dates, amounts, parties) are noted, and its passages are indexed for search by meaning. Hover
over the ring to see where it stands; once the document is indexed, a green dot takes its
place. A document can be searched and asked about once it is read; the summaries and the search
by meaning follow. If a step fails, the ring says why;
Emudoc tries again by itself when the cause may pass, and says when (its step turns amber in the
ring until then), and "Index again" in the file's menu
(right-click it in the library) starts it again at once. A document read without its summaries
and facts, or without its search by meaning, is marked "Partly indexed" with an amber dot and
says what it lacks; it is found by its words meanwhile. "Index the rest", on its tab or in its
menu, adds only what it lacks, and the library offers the same for all such documents at once;
while the AI model or search by meaning is unavailable, the button waits and says why. Once an
upload is sent, its panel follows it as it is read: how many of its files are ready, and those
that could not be read, each with why, which open in their tab; close it with its cross. An
upload that takes $notice_minutes minutes or more is followed by a notice under the bell when
it is done, naming any file that could not be read.

## Folders

Make a folder with New folder, in the menu you get by right-clicking the library's empty space,
drag files and folders into it, and rename an item with F2 or from its menu. A folder uploaded is filed as it was, inside the folder it goes into; where that
holds a folder of the same name already, its files go into that one. A move or a rename can be undone at once from the notice under the library's
toolbar. Two files of the same project cannot hold the same bytes: uploading one again says
where it is.

## Several files at once

Choose several files with the box at the left of each one, which shows as you point at a file
and on every file once two are chosen, and always on a touch screen. Ctrl (⌘ on a Mac) or Shift
and a click do the same, and Ctrl+A chooses every file shown. From the keyboard, Shift and the
arrows choose the files in a row, and Ctrl and the arrows move without choosing, Space then
adding the file reached or taking it out. The bar under the files says how many are chosen; its
Actions offers what the menu of one of them does, and Clear selection or Esc lets them go; a click
on the empty space beside them keeps them chosen. Besides
asking about them together, moving and deleting them,
a member who may change the project does two things to as many as $chosen of them at once:

- **Index … documents again** reads each of them again from the start, and **Index the rest**
  indexes the partly indexed among them in full. One being indexed already is left to finish.
- **Export … documents** makes a ZIP file of them alone, as a project's export makes of the whole
  project, without its chats ([Your data](/help/data)).

## Favourites and recent documents

The library can show a project's documents other ways than by their folders. Choose what it shows
with the icons above the files:

- **All files** shows the folders and the files in them.
- **Favourites** are the documents you starred with "Add to favourites" in their menu, and
  **Recent** the last $recent you opened in the project. Both are yours alone.
- **Trash** shows what was deleted from the project, to take back or delete for good.

Beside them, the order of the files (by name, date added, the document's own date or size, either
way) and whether they show as a list or as a grid of pictures of their first pages. Your browser
remembers both. Under them, **Filter by name** finds files by their names.

## A document's details and its note

Each document is read for its title, its type and its date, which the library's date order, its
search's filter and the agent use, and for the language it is written in, which the search
matches its words in, so that "Lieferung" finds "Lieferungen" in a German contract. Where the
reading got one wrong, set it yourself with "Edit details" on the document's tab: what you set is
kept whenever the document is read again or given a new version, and the dialog shows what the
reading found beside it, with "Use the reading" to take that again. A type is kept in lower case,
as the reading writes it. A document whose words are too few to tell its language, such as a log
of dates and amounts, is searched in the language your organisation chose; so is one whose
language you set to the organisation's.

The language also says how a document's dates and amounts are read into its facts: in a German
invoice "03.04.2026" is the 3rd of April and "1.250" is one thousand two hundred and fifty, where
in English "1.250" is one and a quarter. English writes the day first in some countries and the
month first in others, so a date such as "03/04/2026" in an English document is read by what the
document shows of its country or its other dates, and left without a date where nothing does. A
document whose language could not be told is read by what its figures show alone. After you set
a document's language, use "Index again" for its facts to be read in it.

A document's note holds what its file does not say: where the signed copy is, whom to ask, why it
was paid late. Write it with "Add a note" on the tab, if you may change the project; everyone who
reads the project reads it there, with who changed it last. The agent does not read notes, and a
search does not find them.

## Copies in other projects

A project holds a file once, but another project may hold it too. A document's tab says where else
it is, among the projects you see, each place a link to the copy, and when there are any, the
projects page leads to "See the duplicates", every file held in more than one project with how
much the copies take. Only the same bytes count: a file saved again, or a scan of a printed one,
is not a copy.

## New versions

When a file changes, a contract coming back signed or an invoice sent again corrected, give its
document the new file: "Upload a new version" in its menu in the library, or under Versions on
its tab. The document keeps its place, its chats, its review, its share links and the checks of
its facts, and its new file is read in place of the old one: from then on it is searched, asked
about and read for facts as the new file alone. A review that approved or disputed the old file
is opened again, to review, a check of a fact goes back on a fact the new file reads the same,
and a title, type or date you set stays. A file of another type takes its type's extension, the
name otherwise kept. The earlier files stay under Versions on its tab, each to download or to read its text, for as long as the
document is kept, and the plan counts them with it; the new file's pages count again as it is
read. A file of a connected folder that changes there becomes its document's new version the
same way. A document takes no new version while it is being read, nor in the sample project.

## Many files, or a scanner's folder

The upload client sends a folder from your computer into a project, its folders as they are, or
goes on sending each file a scanner saves to a folder. It is one file,
[emudoc_upload.py]($upload_client), which Python $oldest_python or later runs: a Mac's own
`python3` will do, and on Windows install Python and type `py` in place of `python3`. It needs an
API key that reads and writes, which an owner or admin makes on the organisation page
([Organisations, members and roles](/help/organisations)), in the variable `$key_variable`
rather than on the command line (`export $key_variable=emk_…` on a Mac or Linux, `set
$key_variable=emk_…` on Windows). Then:

    python3 emudoc_upload.py send --server $app_url --project "Invoices" ~/Invoices
    python3 emudoc_upload.py watch --server $app_url --project "Invoices" --into Scans ~/Scans

`send` files the folder inside the project, or inside the folder `--into` names, says what it
left out or Emudoc refused and why, then follows the files until they are read. `watch` looks at
the folder every $watch_seconds seconds and sends a file once it has stayed the same between two
looks, so that a scan goes once the scanner has written all of it, into the project as it is
below the folder, until Ctrl+C. Both leave out what Emudoc does not read and hidden files, send
the rest within the limits above, and never delete, move or change a file. `watch` notes what it
sent in `$state_file` in the folder, so that each file goes once however it is renamed, and
Emudoc never takes the same file twice into a project.

## Deleting and the trash

Deleting a file, a folder or a project puts it in the trash for $trash_days days, from which a
member restores it as it was, while the plan has room for it; after that it is deleted for good.
The trash icon above the library's files, with All files, Favourites and Recent, opens the project's
trash, and the projects page lists the projects in the trash. Nothing in the trash is searched, asked about or counted against your plan. The
library also offers to undo a deletion at once.

## Downloading and printing

Open a document to read its text and its pages; "Download original file", in its menu in the
library or on its tab, gives you the file as it was uploaded.
"Print", on its tab, or your browser's own printing, prints the document alone, as it reads: a
PDF its pages, a sheet each, anything else its title and text, without the library, the chat or
the buttons around it. A passage opened from a citation keeps its highlight; clear it first for
a clean copy. To keep a copy as a file, choose "Save as PDF" where the browser asks for a printer.
A whole project, with its documents and what was read from them, is exported from its card on
the projects page ([Your data](/help/data)).
