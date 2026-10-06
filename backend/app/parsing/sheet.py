"""Spreadsheet parsing: each sheet becomes a heading, and its rows one or more tables."""

import csv
import datetime as dt
import io
from collections.abc import Iterable
from pathlib import Path

from app.parsing.types import ParsedBlock, ParsedDocument, clean_text, table_to_markdown

# Rows per table block. One sheet of thousands of rows becomes many blocks of a readable size;
# the structure stage splits them further into passages.
ROWS_PER_BLOCK = 200


def parse_xlsx(path: Path) -> ParsedDocument:
    from openpyxl import load_workbook

    workbook = load_workbook(path, read_only=True, data_only=True)
    blocks: list[ParsedBlock] = []
    try:
        for sheet in workbook.worksheets:
            rows = _trim(
                [_cell(value) for value in row] for row in sheet.iter_rows(values_only=True)
            )
            if not rows:
                continue
            blocks.append(ParsedBlock("heading", clean_text(sheet.title) or "Sheet", level=1))
            blocks.extend(_table_blocks(rows))
    finally:
        workbook.close()
    return ParsedDocument(blocks=blocks)


def parse_csv(path: Path) -> ParsedDocument:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    try:
        dialect: type[csv.Dialect] | csv.Dialect = csv.Sniffer().sniff(text[:4096], ",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = _trim(csv.reader(io.StringIO(text), dialect))
    return ParsedDocument(blocks=_table_blocks(rows))


def _cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else f"{value:.15g}"
    if isinstance(value, dt.datetime):
        return value.date().isoformat() if value.time() == dt.time() else value.isoformat(" ")
    if isinstance(value, dt.date | dt.time):
        return value.isoformat()
    return str(value)


def _trim(rows: Iterable[Iterable[str]]) -> list[list[str]]:
    """Drop empty rows and the empty columns at the right edge."""
    kept = [list(row) for row in rows if any(cell.strip() for cell in row)]
    width = max((_filled_width(row) for row in kept), default=0)
    return [row[:width] for row in kept]


def _filled_width(row: list[str]) -> int:
    return max((index + 1 for index, cell in enumerate(row) if cell.strip()), default=0)


def _table_blocks(rows: list[list[str]]) -> list[ParsedBlock]:
    """Tables of at most ROWS_PER_BLOCK rows, each repeating the first row as its header."""
    if not rows:
        return []
    header, body = rows[0], rows[1:]
    if not body:
        return [ParsedBlock("table", table_to_markdown([header]))]
    return [
        ParsedBlock("table", table_to_markdown([header, *body[start : start + ROWS_PER_BLOCK]]))
        for start in range(0, len(body), ROWS_PER_BLOCK)
    ]
