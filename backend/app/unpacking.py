"""Files that bring other files with them, opened as the members' page uploads them
(app/uploads.py): a message saved from a mail program (.eml), whose text becomes a document,
headed by its From, To, Date and Subject, and each attachment a document beside it, as a message
sent to the project's address does (app/inbound.py); and a ZIP archive, each file in it a
document in the folder its path names, as an uploaded folder's are.

What a file brings goes through the checks any file does, each with its own sentence, beside the
others of the upload; the file itself is not kept. Only the page's upload opens them: a program
pairs the documents an upload kept with the files it sent, one each, in their order (the upload
client, app/client/), so the versioned API, the mail and the connected folders take none, and
the extensions `GET /api/status` lists are the parsers' alone.

An archive is read from its directory before a byte of it is inflated: the files it holds and
their sizes unpacked count against what one upload may bring (`Room`), the archives of an
upload between them, so a small archive that would unpack to gigabytes is refused unopened, and
`zipfile` inflates no entry past the size its header declares.
"""

import lzma
import zipfile
import zlib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import BinaryIO

from app.folders import PATH_SEPARATOR, folder_names
from app.messages import MAX_MESSAGE_BYTES, body_document, parse

# The extensions of the files opened rather than read, as the page's upload takes them.
PACKED = {".eml", ".zip"}
# What an archive holds that is no one's document: the folders and files a computer adds as it
# packs one (macOS's resource forks, a folder's view settings and thumbnails), as an uploaded
# folder leaves them out (frontend/src/workspace/uploads.ts).
SYSTEM_FILES = {"thumbs.db", "ehthumbs.db", "desktop.ini"}
SYSTEM_FOLDERS = {"__MACOSX"}

TOO_LARGE_MESSAGE = (
    f"A message larger than {MAX_MESSAGE_BYTES // (1024 * 1024)} MB cannot be opened. Save its "
    "attachments from your mail program and upload them."
)
EMPTY_MESSAGE = "It has no text and no attachments to keep."
EMPTY_ARCHIVE = "It holds no files."
DAMAGED_ARCHIVE = (
    "It cannot be opened: the archive is damaged. Check that it opens on your computer, then "
    "upload it again."
)
LOCKED_ARCHIVE = "Protected by a password. Unpack it on your computer and upload its folder."
UNKNOWN_PACKING = (
    "It is packed in a way that cannot be unpacked here (such as Deflate64). Unpack it on your "
    "computer and upload its folder, or pack it again as a plain ZIP file."
)
DAMAGED_ENTRY = (
    "It could not be unpacked: the archive is damaged there. Unpack the archive on your "
    "computer and upload the file from it."
)
# The ways an archive's files are packed that `zipfile` unpacks.
UNPACKED_KINDS = {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED, zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA}
NESTED = (
    "A message or an archive inside another is not opened. Save it apart and upload it by itself."
)


class Refused(Exception):
    """The file cannot be opened; the message is the sentence its uploader reads."""


class Damaged(Exception):
    """A file of an archive did not unpack as it is read (DAMAGED_ENTRY): its bytes do not
    inflate, or not to what its header says."""


@dataclass(frozen=True)
class Inside:
    """A file another brought: its name, the folders it sits in within the other, below the
    folder the other went into (None when they cannot be made, which rejects it), and its
    bytes."""

    name: str
    folders: list[str] | None
    file: BinaryIO


@dataclass
class Room:
    """What the archives of one upload may still bring between them: files, and bytes
    unpacked, from MAX_UPLOAD_FILES and MAX_UPLOAD_REQUEST_MB."""

    files: int
    bytes: int


@contextmanager
def unpacked(path: Path, name: str, room: Room) -> Iterator[list[Inside]]:
    """What the file at `path`, uploaded as `name`, brings, to be read while the context
    lasts; raises `Refused` with why it cannot be opened, before anything is read."""
    if Path(name).suffix.lower() == ".zip":
        with _archive(path, name, room) as inside:
            yield inside
    else:
        yield _message(path, name)


def _message(path: Path, name: str) -> list[Inside]:
    if path.stat().st_size > MAX_MESSAGE_BYTES:
        raise Refused(TOO_LARGE_MESSAGE)
    message = parse(path.read_bytes())
    attached = [attachment for attachment, _ in message.attachments]
    files = []
    if body := body_document(message, attached):
        # Named as the member saved it rather than by its subject, as mail in names it.
        files.append(Inside(f"{Path(name).stem}.txt", [], BytesIO(body[1])))
    files += [Inside(file, [], BytesIO(data)) for file, data in message.attachments]
    if not files:
        raise Refused(EMPTY_MESSAGE)
    return files


@contextmanager
def _archive(path: Path, name: str, room: Room) -> Iterator[list[Inside]]:
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as error:
        raise Refused(DAMAGED_ARCHIVE) from error
    with archive:
        entries = [entry for entry in archive.infolist() if not _skipped(entry)]
        if not entries:
            raise Refused(EMPTY_ARCHIVE)
        if any(entry.flag_bits & 0x1 for entry in entries):
            raise Refused(LOCKED_ARCHIVE)
        if any(entry.compress_type not in UNPACKED_KINDS for entry in entries):
            raise Refused(UNKNOWN_PACKING)
        size = sum(entry.file_size for entry in entries)
        if len(entries) > room.files:
            raise Refused(
                f"It holds {len(entries):,} files, and the archives of one upload may bring "
                f"{room.files:,} more at most. Split it, or unpack it and upload its folder."
            )
        if size > room.bytes:
            raise Refused(
                f"Unpacked, its files come to {_megabytes(size)}, and the archives of one "
                f"upload may bring {_megabytes(room.bytes)} more at most. Split it, or unpack "
                "it and upload its folder."
            )
        room.files -= len(entries)
        room.bytes -= size
        paths = [_parts(entry.filename) for entry in entries]
        # Unpacked as a computer unpacks it: into a folder of the archive's name, unless it
        # holds one file or everything in it is in one folder already.
        tops = {parts[0] if len(parts) > 1 else None for parts in paths}
        alone = len(entries) == 1 or (len(tops) == 1 and None not in tops)
        within = [] if alone else [Path(name).stem]
        yield [
            Inside(parts[-1], _folders([*within, *parts[:-1]]), _Entry(archive, entry))
            for entry, parts in zip(entries, paths, strict=True)
        ]


def _skipped(entry: zipfile.ZipInfo) -> bool:
    """Whether the entry is a folder, or what a computer adds as it packs one: a name or a
    folder starting with a dot is hidden, ".." among them, so no path leads out of the folder
    the archive goes into."""
    if entry.is_dir():
        return True
    parts = _parts(entry.filename)
    return (
        not parts
        or any(part.startswith(".") or part in SYSTEM_FOLDERS for part in parts)
        or parts[-1].lower() in SYSTEM_FILES
    )


def _parts(filename: str) -> list[str]:
    """An entry's path as its folders and name, either slash a separator, as some packers on
    Windows write them."""
    return [part for part in filename.replace("\\", "/").split("/") if part]


def _folders(names: list[str]) -> list[str] | None:
    """The folders an entry goes into, tidied as an uploaded folder's are, or None for a path
    that names one that cannot be made (too long a name, too deep), as an uploaded folder's
    rules have it."""
    return folder_names(PATH_SEPARATOR.join(names))


def _megabytes(size: int) -> str:
    return f"{size / (1024 * 1024):,.0f} MB"


class _Entry:
    """An archive's file, opened when it is first read, so that a hundred of them hold no more
    than the one being read; read to its end, it closes. One that does not unpack raises
    `Damaged`, which rejects that file alone."""

    def __init__(self, archive: zipfile.ZipFile, entry: zipfile.ZipInfo):
        self._archive = archive
        self._entry = entry
        self._opened: BinaryIO | None = None

    def read(self, size: int = -1) -> bytes:
        try:
            if self._opened is None:
                self._opened = self._archive.open(self._entry)
            chunk = self._opened.read(size)
        except (zipfile.BadZipFile, zlib.error, lzma.LZMAError, EOFError, OSError) as error:
            # A bad header or checksum, bytes that do not inflate, or fewer than it says.
            self.close()
            raise Damaged(DAMAGED_ENTRY) from error
        if not chunk:
            self.close()
        return chunk

    def close(self) -> None:
        if self._opened is not None:
            self._opened.close()
