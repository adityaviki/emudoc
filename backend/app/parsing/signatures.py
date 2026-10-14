"""Checking that an upload's bytes are the type its extension claims, before the file is kept.

The extension decides which parser reads a document, the type it is stored and served as, and
what a browser is told it holds. A file whose bytes say otherwise is refused at the upload with
a sentence that says what to do, rather than a stage later in a parser's words, or never, for a
text file of another encoding that the parser reads with replacement characters. The check asks
whether the bytes are what the name claims, not what type they might be instead, with one
exception: Office's older binary container under a Word or Excel name. A document saved with a
password is one, and so is one in the program's format from before 2007, and Office turns
either into a file that passes, so the refusal says how. A file that passes can still be
unreadable (truncated, a PDF locked with a password), and that stays the parse stage's to
report. Like the parsers, it reads settings and database alike not at all, so the child
process that parses could run it too.
"""

import codecs
import mmap
import zipfile
import zlib
from pathlib import Path

# How much of the file the check reads. A signature sits at the very start; a text file in
# another encoding shows itself within its first lines.
HEAD = 64 * 1024
# How far into a PDF its header may sit. The specification puts it at byte 0, and readers
# tolerate a little before it, so a file that opens elsewhere opens here too.
PDF_HEADER_WINDOW = 1024
# The most `[Content_Types].xml` may hold when read out of the package: a real one is a few
# kilobytes, and the cap keeps a crafted one from costing memory before the file is refused.
CONTENT_TYPES_MAX = 1024 * 1024

PNG = b"\x89PNG\r\n\x1a\n"
JPEG = b"\xff\xd8\xff"
ZIP = b"PK\x03\x04"
# A Word document and an Excel workbook are both zips; the package's `[Content_Types].xml`
# declares what its main part is, which is how python-docx and openpyxl find it, so a package
# that declares another kind (a template, a macro-enabled file, a presentation) is not one
# they would be given.
CONTENT_TYPES = "[Content_Types].xml"
WORD_MAIN = b"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
EXCEL_MAIN = b"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
# Office's older binary container, the compound file: a .doc or an .xls is one, and so is a
# .docx or an .xlsx saved with a password, which Office encrypts whole and wraps in one. The
# streams it holds say which: the encrypted package's `EncryptionInfo`, or the older format's
# main stream (`Book` is the Excel 5 workbook's).
COMPOUND = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
ENCRYPTION_INFO = "EncryptionInfo"
OLDER_FORMATS = {
    ".docx": (
        ("WordDocument",),
        "An older Word document (.doc). Save it as .docx and upload that.",
    ),
    ".xlsx": (
        ("Workbook", "Book"),
        "An older Excel workbook (.xls). Save it as .xlsx and upload that.",
    ),
}
LOCKED = "Protected by a password. Save a copy without the password and upload that."
# The marks of the Unicode encodings the text parsers do not read.
OTHER_UNICODE_MARKS = (b"\xff\xfe", b"\xfe\xff", b"\x00\x00\xfe\xff")
# The bytes no text file holds, as file(1) draws the line: the control characters apart from
# bell to carriage return and escape. Text in another encoding has none of them, so they tell
# a binary file from text that only needs saving again as UTF-8.
NOT_TEXT = bytes([*range(0x00, 0x07), *range(0x0E, 0x1B), *range(0x1C, 0x20)])
TEXT_BYTES = bytes(byte for byte in range(256) if byte not in NOT_TEXT)
TEXT_EXTENSIONS = {".csv", ".txt", ".md"}

RENAME = "Upload the original, or rename it to its real type."
NOT_UTF8 = "Not UTF-8 text. Save it as UTF-8 and upload it again."


def wrong_type(path: Path, suffix: str) -> str | None:
    """Why the file is not what its extension says, or None when its bytes bear the name out.

    The suffix is the extension in lower case with its dot. One the parsers do not take comes
    back as a mismatch too, since nothing can be said for its bytes.
    """
    with path.open("rb") as file:
        head = file.read(HEAD)
    if suffix == ".pdf":
        return None if b"%PDF-" in head[:PDF_HEADER_WINDOW] else f"Not a PDF file. {RENAME}"
    if suffix == ".png":
        return None if head.startswith(PNG) else f"Not a PNG image. {RENAME}"
    if suffix in {".jpg", ".jpeg"}:
        return None if head.startswith(JPEG) else f"Not a JPEG image. {RENAME}"
    if suffix == ".webp":
        webp = head[:4] == b"RIFF" and head[8:12] == b"WEBP"
        return None if webp else f"Not a WebP image. {RENAME}"
    if suffix == ".docx":
        if WORD_MAIN in _content_types(path, head):
            return None
        return _compound(path, head, suffix) or f"Not a Word document. {RENAME}"
    if suffix == ".xlsx":
        if EXCEL_MAIN in _content_types(path, head):
            return None
        return _compound(path, head, suffix) or f"Not an Excel workbook. {RENAME}"
    if suffix in TEXT_EXTENSIONS:
        return _not_text(head, whole=len(head) < HEAD)
    return f"Not a supported type. {RENAME}"


def _content_types(path: Path, head: bytes) -> bytes:
    """What the package's `[Content_Types].xml` declares, or nothing for a file that is no Office
    package: not a zip, or a zip without the part or with one that cannot be read. The zip is
    opened only behind the zip signature, so a file that is nothing like one costs its first
    bytes and no more."""
    if not head.startswith(ZIP):
        return b""
    try:
        with zipfile.ZipFile(path) as package:
            if package.getinfo(CONTENT_TYPES).file_size > CONTENT_TYPES_MAX:
                return b""
            return package.read(CONTENT_TYPES)
    except (zipfile.BadZipFile, KeyError, NotImplementedError, RuntimeError, zlib.error):
        # A broken or truncated zip, no such part, a compression the module does not do, an
        # encrypted part, or compressed bytes that do not inflate: each is a package the
        # parsers could not open either.
        return b""


def _compound(path: Path, head: bytes, suffix: str) -> str | None:
    """Why a compound file under a Word or Excel name is refused: it is locked with a password,
    or it is the same program's older format. None for any other file, which gets the plain
    mismatch, as does a compound file of another kind (an older format of the other program,
    an Outlook message)."""
    if not head.startswith(COMPOUND):
        return None
    streams, older = OLDER_FORMATS[suffix]
    with path.open("rb") as file, mmap.mmap(file.fileno(), 0, access=mmap.ACCESS_READ) as data:
        if _holds_stream(data, ENCRYPTION_INFO):
            return LOCKED
        if any(_holds_stream(data, name) for name in streams):
            return older
    return None


def _holds_stream(data: mmap.mmap, name: str) -> bool:
    """Whether the compound file's directory has an entry for a stream of this name.

    An entry is the name in UTF-16 with a closing null, zeros to its 64th byte, the name's
    length in bytes and 2, the type of a stream. It is searched for across the whole file rather
    than found by following the file's sector tables: writers put the directory anywhere
    (LibreOffice puts it after the streams, so past `HEAD` in a big file), the search is one
    pass in C, and all the answer decides is which sentence refuses the file.
    """
    named = name.encode("utf-16-le") + b"\0\0"
    entry = named.ljust(64, b"\0") + len(named).to_bytes(2, "little") + b"\x02"
    return data.find(entry) != -1


def _not_text(head: bytes, whole: bool) -> str | None:
    """Why the bytes are not the UTF-8 text the parsers read, or None when they are. A control
    byte marks a binary file, unless a byte-order mark says it is text in another Unicode
    encoding, which gets the same advice as text in any other encoding: save it as UTF-8."""
    if head.startswith(OTHER_UNICODE_MARKS):
        return NOT_UTF8
    if head.translate(None, delete=TEXT_BYTES):
        return f"Not a text file. {RENAME}"
    try:
        # A sequence cut at the end of what was read is not an error unless the file ends there.
        codecs.getincrementaldecoder("utf-8")().decode(head, final=whole)
    except UnicodeDecodeError:
        return NOT_UTF8
    return None
