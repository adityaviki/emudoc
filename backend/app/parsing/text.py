"""Plain text and Markdown parsing."""

import re

from app.parsing.types import ParsedBlock, ParsedDocument, clean_text

_MD_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*$")
_LIST_ITEM = re.compile(r"^\s*(?:[-*+•]|\d{1,3}[.)])\s+\S")
_UNDERLINE = re.compile(r"^\s*(=+|-+)\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")


def parse_text(source: str, markdown: bool = False) -> ParsedDocument:
    blocks: list[ParsedBlock] = []
    paragraph: list[str] = []
    table: list[str] = []
    code: list[str] | None = None

    def flush_paragraph() -> None:
        if paragraph:
            text = clean_text(" ".join(paragraph))
            if text:
                blocks.append(_plain_block(text, len(paragraph)))
            paragraph.clear()

    def flush_table() -> None:
        if table:
            blocks.append(ParsedBlock("table", "\n".join(table)))
            table.clear()

    for raw in source.splitlines():
        line = raw.rstrip()

        if markdown and _FENCE.match(line):
            if code is None:
                flush_paragraph()
                code = []
            else:
                if code:
                    blocks.append(ParsedBlock("paragraph", "\n".join(code)))
                code = None
            continue
        if code is not None:
            code.append(line.replace("\x00", ""))
            continue

        if markdown and line.lstrip().startswith("|"):
            flush_paragraph()
            table.append(line.strip())
            continue
        flush_table()

        if not line.strip():
            flush_paragraph()
            continue

        heading = _MD_HEADING.match(line) if markdown else None
        if heading:
            flush_paragraph()
            blocks.append(
                ParsedBlock("heading", clean_text(heading.group(2)), level=len(heading.group(1)))
            )
            continue

        underline = _UNDERLINE.match(line)
        if underline:
            # Setext heading: one text line underlined with === or ---. Otherwise a rule.
            if len(paragraph) == 1:
                level = 1 if underline.group(1)[0] == "=" else 2
                blocks.append(ParsedBlock("heading", clean_text(paragraph.pop()), level=level))
            else:
                flush_paragraph()
            continue

        if _LIST_ITEM.match(line):
            flush_paragraph()
            blocks.append(ParsedBlock("list_item", clean_text(line)))
            continue

        paragraph.append(line.strip())

    flush_paragraph()
    flush_table()
    if code:
        blocks.append(ParsedBlock("paragraph", "\n".join(code)))
    return ParsedDocument(blocks=blocks)


def _plain_block(text: str, line_count: int) -> ParsedBlock:
    # A short all-caps line standing alone reads as a heading in plain text.
    letters = [char for char in text if char.isalpha()]
    if (
        line_count == 1
        and len(text) <= 80
        and len(letters) >= 3
        and all(c.isupper() for c in letters)
    ):
        return ParsedBlock("heading", text, level=1)
    return ParsedBlock("paragraph", text)
