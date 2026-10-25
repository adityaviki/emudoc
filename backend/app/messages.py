"""Reading an email message: its one sender, its subject, its text (the plain body, or the HTML
body's words) and the files attached, and the message as a document, its headers above its text
as an email reads, for mail sent to a project's address (app/inbound.py) and a message uploaded
as a file (app/unpacking.py). A message's text is a document's: none of it goes into a log line
or an error."""

import mimetypes
import re
from dataclasses import dataclass, field
from email import policy
from email.message import EmailMessage, Message
from email.parser import BytesParser
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path

# The largest message read, attachments and all: SES's own limit for the mail it hands over,
# and so the most a message uploaded as a file may be (app/unpacking.py).
MAX_MESSAGE_BYTES = 40 * 1024 * 1024
# A message's subject as its document's name keeps it, and an attachment's name.
SUBJECT_CHARS = 120
FILE_NAME_CHARS = 200


@dataclass
class Parsed:
    """What a message is made of, for its project: the sender, the subject, the body's text and
    the files attached."""

    sender: str | None
    subject: str
    text: str
    headers: list[tuple[str, str]]
    attachments: list[tuple[str, bytes]] = field(default_factory=list)


def parse(raw: bytes) -> Parsed:
    """The message's sender (its one From address, or None), subject, text and attachments."""
    message = BytesParser(policy=policy.default).parse(BytesIO(raw))
    assert isinstance(message, EmailMessage)
    sender = sender_of(message)
    subject = " ".join(str(message.get("Subject", "")).split())
    headers = [
        (name, " ".join(str(message[name]).split()))
        for name in ("From", "To", "Cc", "Date", "Subject")
        if message.get(name)
    ]
    body = message.get_body(preferencelist=("plain", "html"))
    text = ""
    if body is not None:
        content = _content(body)
        text = text_of_html(content) if body.get_content_subtype() == "html" else content
    parsed = Parsed(sender, subject, text.strip(), headers)
    for part in message.walk():
        if part is body or part.is_multipart():
            continue
        name = _attachment_name(part)
        if name is None:
            continue
        payload = part.get_payload(decode=True)
        if isinstance(payload, bytes):
            parsed.attachments.append((name, payload))
    return parsed


def sender_of(message: Message) -> str | None:
    """A message's one From address, lower-cased; None for none, several, or one unreadable."""
    addresses = getattr(message.get("From"), "addresses", ()) if "From" in message else ()
    senders = [a.addr_spec.lower() for a in addresses if "@" in (a.addr_spec or "")]
    return senders[0] if len(senders) == 1 else None


def _content(part: EmailMessage) -> str:
    try:
        content = part.get_content()
    except (LookupError, ValueError):
        # A charset Python does not know, or a part that says one thing and holds another.
        content = (part.get_payload(decode=True) or b"").decode("utf-8", "replace")
    return content if isinstance(content, str) else ""


def _attachment_name(part: EmailMessage) -> str | None:
    """The name a part is kept under, or None for a part that is the body's: an attachment by
    its disposition or its name, but not an image the HTML body shows by its Content-ID."""
    disposition = part.get_content_disposition()
    name = part.get_filename()
    if disposition != "attachment":
        if not name or part.get("Content-ID"):
            return None
    if not name:
        extension = mimetypes.guess_extension(part.get_content_type()) or ""
        name = f"attachment{extension}"
    # A name from another system may carry its folders, with either slash, and a name past
    # FILE_NAME_CHARS keeps its end, the extension the file is read by.
    name = Path(name.replace("\\", "/")).name or "attachment"
    return name if len(name) <= FILE_NAME_CHARS else name[-FILE_NAME_CHARS:]


class _Text(HTMLParser):
    """An HTML body's words, a line for each block, without its scripts and styles."""

    BLOCKS = {
        "p",
        "div",
        "br",
        "tr",
        "li",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "table",
        "blockquote",
    }
    HIDDEN = {"script", "style", "head", "title"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._hidden = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in self.HIDDEN:
            self._hidden += 1
        elif tag in self.BLOCKS:
            self.parts.append("\n")
        elif tag in ("td", "th"):
            self.parts.append("\t")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.HIDDEN:
            self._hidden = max(0, self._hidden - 1)
        elif tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._hidden:
            self.parts.append(data)


def text_of_html(html: str) -> str:
    reader = _Text()
    reader.feed(html)
    reader.close()
    lines = (" ".join(line.split()) for line in "".join(reader.parts).splitlines())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def body_document(parsed: Parsed, attached: list[str]) -> tuple[str, bytes] | None:
    """The message's text as a document, its headers above as an email reads, and the files
    it brought named; None when it has no text."""
    if not parsed.text:
        return None
    lines = [f"{name}: {value}" for name, value in parsed.headers]
    if attached:
        lines.append(f"Attached: {', '.join(attached)}")
    content = "\n".join([*lines, "", parsed.text, ""])
    return f"{_name_of(parsed)}.txt", content.encode()


def _name_of(parsed: Parsed) -> str:
    """A file name from the subject, or the sender for a message without one."""
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", parsed.subject)
    name = " ".join(name.split()).strip(". ")[:SUBJECT_CHARS].strip()
    return name or f"Message from {parsed.sender or 'an unknown sender'}"
