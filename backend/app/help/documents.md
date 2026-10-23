# Uploading and organising documents

## What you can upload

$formats. A file may be up to $max_upload_mb MB. Upload files with Upload, or a folder with the
folders in it with "Upload a folder", or drop either on the library or on a folder in it. A larger
upload is sent $max_upload_files files or $max_request_mb MB at a time, and a panel under the
toolbar shows how far it has gone, file by file under "Each file", until Stop. A file Emudoc does
not read is left out before anything is sent, and so are a folder's hidden files. A file saved
with a password cannot be read: save it again without one. A scanned page is read by the AI model
as an image$ocr_pages. $scanning

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
while the AI model or search by meaning is unavailable, the button waits and says why. An
upload that takes $notice_minutes minutes or more is followed by a notice under the bell when
it is done, naming any file that could not be read.

## Folders

Make folders with New folder, drag files and folders into them, and rename an item with F2 or
from its menu. A folder uploaded is filed as it was, inside the folder it goes into; where that
holds a folder of the same name already, its files go into that one. A move or a rename can be undone at once from the notice under the library's
toolbar. Two files of the same project cannot hold the same bytes: uploading one again says
where it is.

## Deleting and the trash

Deleting a file, a folder or a project puts it in the trash for $trash_days days, from which a
member restores it as it was, while the plan has room for it; after that it is deleted for good.
The library's Trash button opens the project's trash, and the projects page lists the projects
in the trash. Nothing in the trash is searched, asked about or counted against your plan. The
library also offers to undo a deletion at once.

## Downloading

Open a document to read its text and its pages; "Download original file", in its menu in the
library or on its tab, gives you the file as it was uploaded.
A whole project, with its documents and what was read from them, is exported from its card on
the projects page ([Your data](/help/data)).
