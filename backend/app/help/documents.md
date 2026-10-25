# Uploading and organising documents

## What you can upload

$formats. A file may be up to $max_upload_mb MB. Upload files with Upload, or a folder with the
folders in it with "Upload a folder", or drop either on the library or on a folder in it. A larger
upload is sent $max_upload_files files or $max_request_mb MB at a time, and a panel under the
toolbar shows how far it has gone, file by file under "Each file", until Stop. A file Emudoc does
not read is left out before anything is sent, and so are a folder's hidden files. A file saved
with a password cannot be read: save it again without one. A scanned page, and each page of a
TIFF, is read by the AI model as an image$ocr_pages. An email saved from your mail program
(.eml) is opened: its text becomes a document headed by who sent it to whom and when, and each
file attached to it a document beside it. A ZIP archive is unpacked as your computer would,
into a folder of its name unless it holds one folder already, its folders made as an uploaded
folder's are; the archives of one upload may bring $max_upload_files files and $max_request_mb MB
unpacked between them. $scanning

## How a document is read

An upload is read in the background, in steps that its status ring fills one by one: the file
is checked, its text is read, its sections are found, each section is summarised and its facts
(dates, amounts, parties) are noted, and its passages are indexed for search by meaning. Hover
over the ring to see where it stands. A document can be searched and asked about once it is
read; the summaries and the search by meaning follow. If a step fails, the ring says why;
Emudoc tries again by itself when the cause may pass, and says when (its step turns amber in the
ring until then), and "Index again" in the file's menu
(right-click it in the library) starts it again at once. A document read without its summaries
and facts, or without its search by meaning, is marked "Partly indexed" with an amber check and
says what it lacks; it is found by its words meanwhile. "Index the rest", on its tab or in its
menu, adds only what it lacks, and the library offers the same for all such documents at once;
while the AI model or search by meaning is unavailable, the button waits and says why. Once an
upload is sent, its panel follows it as it is read: how many of its files are ready, and those
that could not be read, each with why, which open in their tab; close it with its cross. An
upload that takes $notice_minutes minutes or more is followed by a notice under the bell when
it is done, naming any file that could not be read.

## Folders

Make folders with New folder, drag files and folders into them, and rename an item with F2 or
from its menu. A folder uploaded is filed as it was, inside the folder it goes into; where that
holds a folder of the same name already, its files go into that one. A move or a rename can be undone at once from the notice under the library's
toolbar. Two files of the same project cannot hold the same bytes: uploading one again says
where it is.

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
The library's Trash button opens the project's trash, and the projects page lists the projects
in the trash. Nothing in the trash is searched, asked about or counted against your plan. The
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
