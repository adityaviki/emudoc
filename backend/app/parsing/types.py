import re
from dataclasses import dataclass, field

BBox = tuple[float, float, float, float]  # x0, top, x1, bottom


class UnsupportedFile(Exception):
    pass


class FileTooLarge(Exception):
    """The file has more pages, rows or pixels than the parsers take; the message says the cap
    and what to do."""


def too_much_text(max_chars: int) -> str:
    """Why a file over PARSE_MAX_CHARS is refused, in the words its uploader reads."""
    return (
        f"This file holds more than {max_chars:,} characters of text, the most one document "
        "can have. Split it and upload the parts."
    )


class UnreadableFile(Exception):
    """The file is the type its name says but holds nothing the parsers can open: it is locked
    with a password, or has no pages; the message says which and what to do."""


@dataclass
class ParsedBlock:
    kind: str  # heading | paragraph | list_item | table
    text: str
    level: int | None = None  # headings only, 1 is the top level
    page: int | None = None  # 1-based; None for formats without pages
    bbox: BBox | None = None


@dataclass
class ParsedDocument:
    blocks: list[ParsedBlock]
    page_count: int | None = None
    page_sizes: list[tuple[float, float]] = field(default_factory=list)
    title: str | None = None
    # Pages with no text layer (scans, images); the pipeline sends these to OCR.
    ocr_pages: list[int] = field(default_factory=list)


_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_SPACES = re.compile(r"[ \t ]+")


def clean_text(text: str) -> str:
    """Drop control characters (Postgres rejects NUL) and collapse runs of spaces."""
    return _SPACES.sub(" ", _CONTROL.sub("", text)).strip()


def table_to_markdown(rows: list[list[str | None]]) -> str:
    """Render extracted table rows as a pipe table; the first row is the header."""

    def cell(value: str | None) -> str:
        return clean_text((value or "").replace("\n", " ")).replace("|", "\\|")

    width = max(len(row) for row in rows)
    lines = []
    for index, row in enumerate(rows):
        cells = [cell(value) for value in row] + [""] * (width - len(row))
        lines.append("| " + " | ".join(cells) + " |")
        if index == 0:
            lines.append("|" + " --- |" * width)
    return "\n".join(lines)
