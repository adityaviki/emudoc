"""Files that bring other files with them, opened as the members' page uploads them
(app/uploads.py): a message saved from a mail program (.eml), whose text becomes a document,
headed by its From, To, Date and Subject, and each attachment a document beside it, as a message
sent to the project's address does (app/inbound.py).

What a file brings goes through the checks any file does, each with its own sentence, beside the
others of the upload; the file itself is not kept. Only the page's upload opens them: a program
pairs the documents an upload kept with the files it sent, one each, in their order (the upload
client, app/client/), so the versioned API, the mail and the connected folders take none, and
the extensions `GET /api/status` lists are the parsers' alone.
"""

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import BinaryIO

from app.messages import MAX_MESSAGE_BYTES, body_document, parse

# The extensions of the files opened rather than read, as the page's upload takes them.
PACKED = {".eml"}

TOO_LARGE_MESSAGE = (
    f"A message larger than {MAX_MESSAGE_BYTES // (1024 * 1024)} MB cannot be opened. Save its "
    "attachments from your mail program and upload them."
)
EMPTY_MESSAGE = "It has no text and no attachments to keep."
NESTED = (
    "A message inside another is not opened. Save it from your mail program and upload it by "
    "itself."
)


class Refused(Exception):
    """The file cannot be opened; the message is the sentence its uploader reads."""


@dataclass(frozen=True)
class Inside:
    """A file another brought: its name, the folders it sits in within the other, below the
    folder the other went into, and its bytes."""

    name: str
    folders: list[str]
    file: BinaryIO


def unpacked(path: Path, name: str) -> list[Inside]:
    """What the file at `path`, uploaded as `name`, brings; raises `Refused` with why it cannot
    be opened."""
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
