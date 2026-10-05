"""Word parsing with python-docx: headings come from paragraph styles."""

import re
from pathlib import Path

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.parsing.types import ParsedBlock, ParsedDocument, clean_text, table_to_markdown

_HEADING_STYLE = re.compile(r"heading\s*(\d)", re.IGNORECASE)


def parse_docx(path: Path) -> ParsedDocument:
    document = Document(str(path))
    blocks: list[ParsedBlock] = []
    for item in document.iter_inner_content():
        if isinstance(item, Table):
            rows = [[cell.text for cell in row.cells] for row in item.rows]
            if rows and any(any(cell.strip() for cell in row) for row in rows):
                blocks.append(ParsedBlock("table", table_to_markdown(rows)))
        elif isinstance(item, Paragraph):
            text = clean_text(item.text)
            if text:
                blocks.append(_paragraph_block(item, text))

    title = clean_text(document.core_properties.title or "") or None
    return ParsedDocument(blocks=blocks, title=title)


def _paragraph_block(paragraph: Paragraph, text: str) -> ParsedBlock:
    style = (paragraph.style.name if paragraph.style is not None else "") or ""
    match = _HEADING_STYLE.search(style)
    if match:
        return ParsedBlock("heading", text, level=int(match.group(1)))
    if style.lower() == "title":
        return ParsedBlock("heading", text, level=1)
    if style.lower().startswith("list") or _is_numbered(paragraph):
        return ParsedBlock("list_item", text)
    if _all_bold(paragraph) and len(text) <= 120 and text[-1] not in ".;,":
        # Unstyled documents mark headings with bold alone; treat them as one flat level.
        return ParsedBlock("heading", text, level=2)
    return ParsedBlock("paragraph", text)


def _is_numbered(paragraph: Paragraph) -> bool:
    properties = paragraph._p.pPr
    return properties is not None and properties.numPr is not None


def _all_bold(paragraph: Paragraph) -> bool:
    runs = [run for run in paragraph.runs if run.text.strip()]
    return bool(runs) and all(run.bold for run in runs)
