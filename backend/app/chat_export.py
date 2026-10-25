"""An answer with its question, or a whole chat, as a file to save (the routes are in
app/api/chats.py): its Markdown, a Word document or a PDF, laid out as a draft's version is
(app/draft_export.py), for a member to keep, send or file outside the app.

An answer's file is headed by its question, a chat's by its title with each question a heading
of its own over its answer, and an answer's own headings are set below the question's. A line
under the heading says who asked, in which project and on which day, in the time zone of the
member who downloads it, and the file ends saying that a model wrote the answers, as a shared
answer's page does, before its notes. An answer that did not end with its text says how it
ended, as the page does: stopped, cut off or failed, or still being written as the file was made.

The citations are numbered across the whole file in the order it first makes them, and listed
at its end as notes naming the document, section and pages of each passage, as an answer's
sources named it. A citation counts where its answer was shown the passage, as on the page: the
same [P…] in an answer that was never shown it stays as it is written.

The question, the title, the names and the notices are written as they read, never as Markdown:
whatever the Markdown would read in them is escaped, so a question about `**` reads `**`.

What an answer says is what documents say: the file is made in memory for the request and kept
nowhere.
"""

import datetime as dt
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from app.draft_export import (
    READER,
    Block,
    ExportFormat,
    Heading,
    Items,
    Note,
    Para,
    Quote,
    Span,
    blocks_of,
    docx_file,
    file_stem,
    footnotes,
    markdown_body,
    pdf_file,
    source_label,
    writable,
)
from app.drafts import CITATION
from app.models import ChatMessage
from app.retention import day

WRITTEN_BY_A_MODEL = (
    "Written by an AI model from the documents, which it can misread: check the passages it cites."
)
STILL_WRITTEN = "This answer was still being written when the file was made."
# What a PDF too large for its pages is refused with, of an answer or of a chat.
NOT_LAID_OUT = (
    "This {} could not be laid out as a PDF: something in it is too large for a page. Download it "
    "as a Word document instead."
)

# What the Markdown would read in the member's own words, escaped so that they read as written:
# emphasis, code, links, HTML, strikethrough, a heading's closing marks, and an entity.
_PLAIN = re.compile(r"[\\`*_\[\]<>~#|]|&(?=#?[A-Za-z0-9]+;)")


@dataclass(frozen=True)
class Exchange:
    """A question and its answer as the file shows them: the answer's text citing [P<id>], the
    passages it was shown, which its citations may name, and how it ended when it did not end
    with its text."""

    question: str
    answer: str
    sources: Sequence[Mapping[str, Any]]
    ended: str | None
    asked_at: dt.datetime


@dataclass(frozen=True)
class Asked:
    """Who asked, the chat's starter; in which project; and the zone the file's days are in,
    that of the member who downloads it."""

    by: str
    project: str
    zone: dt.tzinfo


def exchange_of(question: str, answer: ChatMessage) -> Exchange:
    """The answer and the question it answers."""
    if answer.status == "streaming":
        ended = STILL_WRITTEN
    else:
        ended = " ".join(part for part in (answer.notice, answer.error) if part) or None
    return Exchange(question, answer.content, answer.sources or [], ended, answer.created_at)


def exchanges_of(messages: Sequence[ChatMessage]) -> list[Exchange]:
    """The chat's questions, each with its answer, in the order they were asked."""
    exchanges = []
    question = ""
    for message in messages:
        if message.role == "user":
            question = message.content
        else:
            exchanges.append(exchange_of(question, message))
    return exchanges


def answer_file(written_as: ExportFormat, title: str, exchange: Exchange, asked: Asked) -> bytes:
    """The answer as the file asked for, headed by its question; `title` names it in the file's
    properties. Raises ExportRefused for a PDF that cannot be laid out."""
    byline = f"Asked by {asked.by} in {asked.project} on {day(exchange.asked_at, asked.zone)}."
    return _file(written_as, title, exchange.question, byline, [exchange], questions=False)


def chat_file(
    written_as: ExportFormat, title: str, exchanges: Sequence[Exchange], asked: Asked
) -> bytes:
    """The chat as the file asked for, headed by its title, each question a heading over its
    answer. Raises ExportRefused for a PDF that cannot be laid out."""
    first, last = (day(exchanges[at].asked_at, asked.zone) for at in (0, -1))
    days = f"on {first}" if first == last else f"from {first} to {last}"
    byline = f"Asked by {asked.by} in {asked.project} {days}."
    return _file(written_as, title, title, byline, exchanges, questions=True)


def file_name(title: str, written_as: ExportFormat, otherwise: str) -> str:
    """What the browser saves the file as: its title, or `otherwise` for one a name cannot hold."""
    return f"{file_stem(title, otherwise)}.{written_as}"


def _file(
    written_as: ExportFormat,
    title: str,
    heading: str,
    byline: str,
    exchanges: Sequence[Exchange],
    questions: bool,
) -> bytes:
    """The file: the heading, the byline, each exchange (its question a heading of its own when
    `questions`), what the model wrote, then the notes."""
    exchanges = [
        replace(
            exchange,
            question=writable(exchange.question),
            # One kind of line break, so that the reader's lines are the text's own.
            answer=writable(exchange.answer).replace("\r\n", "\n").replace("\r", "\n"),
        )
        for exchange in exchanges
    ]
    title, heading, byline = writable(title), writable(heading), writable(byline)
    notes, cited = _numbered(exchanges)
    # Below the file's heading, and below the questions' when they are headings too.
    deeper = 2 if questions else 1
    if written_as == "md":
        parts = [f"# {_line(heading)}", f"*{_plain(byline)}*"]
        for exchange, known in zip(exchanges, cited, strict=True):
            if questions:
                parts.append(f"## {_line(exchange.question)}")
            if body := markdown_body(_deeper_markdown(exchange.answer, deeper), known):
                parts.append(body)
            if exchange.ended:
                parts.append(f"*{_plain(exchange.ended)}*")
        parts.append(f"*{_plain(WRITTEN_BY_A_MODEL)}*")
        if notes:
            parts.append(footnotes(notes))
        return ("\n\n".join(parts) + "\n").encode()
    blocks: list[Block] = [Heading(1, [Span(heading)]), Para([Span(byline, italic=True)])]
    for exchange, known in zip(exchanges, cited, strict=True):
        if questions:
            blocks.append(Heading(2, [Span(exchange.question)]))
        blocks += _deeper(blocks_of(exchange.answer, known), deeper)
        if exchange.ended:
            blocks.append(Para([Span(exchange.ended, italic=True)]))
    blocks.append(Para([Span(WRITTEN_BY_A_MODEL, italic=True)]))
    if written_as == "docx":
        return docx_file(blocks, notes, title, exchanges[-1].asked_at)
    return pdf_file(blocks, notes, title)


def _numbered(exchanges: Sequence[Exchange]) -> tuple[dict[int, Note], list[dict[int, Note]]]:
    """Every passage the file cites, by id, numbered across the file in the order it is first
    cited; and each answer's notes, those of the passages it was shown."""
    notes: dict[int, Note] = {}
    cited = []
    for exchange in exchanges:
        shown = {source["id"]: source for source in exchange.sources}
        known = {}
        for passage_id in dict.fromkeys(int(match) for match in CITATION.findall(exchange.answer)):
            if passage_id not in shown:
                continue
            if passage_id not in notes:
                notes[passage_id] = Note(len(notes) + 1, source_label(shown[passage_id]))
            known[passage_id] = notes[passage_id]
        cited.append(known)
    return notes, cited


def _deeper(blocks: list[Block], by: int) -> list[Block]:
    """The blocks with their headings `by` levels deeper, six at most."""
    deeper: list[Block] = []
    for block in blocks:
        if isinstance(block, Heading):
            block = replace(block, level=min(block.level + by, 6))
        elif isinstance(block, Items):
            block = replace(block, items=[_deeper(item, by) for item in block.items])
        elif isinstance(block, Quote):
            block = replace(block, blocks=_deeper(block.blocks, by))
        deeper.append(block)
    return deeper


def _deeper_markdown(text: str, by: int) -> str:
    """The Markdown with its headings `by` levels deeper, six at most: those written with #, the
    kind a model writes; an underlined one keeps its level."""
    lines = text.split("\n")
    for token in READER.parse(text):
        if token.type == "heading_open" and token.markup.startswith("#") and token.map:
            line = lines[token.map[0]]
            # Only a quote's or a list's marks come before a heading's, and none of them is a #.
            at = line.index("#")
            marks = len(line) - at - len(line[at:].lstrip("#"))
            lines[token.map[0]] = line[:at] + "#" * min(marks + by, 6) + line[at + marks :]
    return "\n".join(lines)


def _line(text: str) -> str:
    """The member's words on one line, as a heading of Markdown holds them."""
    return _plain(" ".join(text.split()))


def _plain(text: str) -> str:
    return _PLAIN.sub(lambda match: "\\" + match.group(0), text)
