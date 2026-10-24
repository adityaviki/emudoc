"""The facts table as a file to save (app/fact_table.py; the route is in app/api/templates.py):
every row the page's sort, conditions, words and "Unchecked" keep, as a CSV file or an Excel
workbook.

The CSV is the table as the page shows it: a row a document, its name first, then a column a
field of the template in its order, each cell the values of the field's facts as written, or as
a member corrected them, in the document's order and joined by "; ". Then the document's review
(app/reviews.py), approved or disputed, which a payment run filters by, and two more columns say
what the cells' marks say: the fields with a fact no member has checked, and those of them whose
quote was not found in the document's text. The workbook's first sheet is the same table with each
value typed, a lone number or amount a number and a lone date a date, so that a spreadsheet adds
and sorts them (an amount shown in its currency); its second sheet is every fact of those cells
on a row of its own, with where it was found, whether its quote is in the text, who checked it,
and what the model read where a member wrote another value.

How sure a fact is, is what the file says beside it: the model gives no figure for its own
reading, and a figure it gave would not be calibrated (indexing-approach.md, "Extraction
templates"). What is known is whether the quote it gave was found in the text, and whether a
member checked it.

A heading is the field's name, which is lowercase (`normalize_fact_name`), so the document's and
the checks' columns, capitalised, never take a field's. The rows are read BATCH at a time, each
batch in a session of its own and into plain values before it closes, as the audit log's file is
(app/api/audit.py): the CSV is sent as it is read, its cells a spreadsheet would run as a formula
quoted; the workbook is written in openpyxl's write-only mode to a temporary file and sent once
it is whole, every text in it a text, never a formula.
"""

import csv
import datetime as dt
import io
import tempfile
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Literal

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE, Cell
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from openpyxl.worksheet._write_only import WriteOnlyWorksheet

from app.db import SessionLocal
from app.extraction import Template
from app.fact_table import Ask, Row, read_table
from app.models import Fact
from app.reviews import STATE_WORDS

ExportFormat = Literal["csv", "xlsx"]
MEDIA_TYPES: dict[str, str] = {
    "csv": "text/csv; charset=utf-8",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
# The rows read at a time.
BATCH = 500
# What a spreadsheet starts a formula with (OWASP's "CSV injection"), as the audit log's file.
FORMULA = ("=", "+", "-", "@", "\t", "\r")
# How a cell's several values are joined.
JOINED = "; "
# A sheet's rows, and a cell's characters, as Excel holds them.
SHEET_ROWS = 1_048_576
CELL_CHARS = 32_767

DOCUMENT = "Document"
REVIEW = "Review"
UNCHECKED = "Unchecked"
NOT_FOUND = "Quote not found"
FACT_COLUMNS = (
    (DOCUMENT, 40),
    ("Field", 22),
    ("Value", 18),
    ("Currency", 10),
    ("As written", 30),
    ("Page", 7),
    ("Quote", 50),
    ("Quote found", 12),
    ("Checked by", 22),
    ("Checked at (UTC)", 20),
    ("Model read", 30),
)
LEFT_OUT = "Excel holds {rows:,} rows a sheet: the rest are left out. The CSV file holds them."


@dataclass(frozen=True)
class Value:
    """A fact as the file writes it, read into plain values before its session closes."""

    field: str
    type: str
    value: str
    number: float | None
    date: dt.date | None
    currency: str | None
    quote: str | None
    page: int | None
    verified: bool
    checked: bool
    # Who checked it, as their name is now; None for a fact not checked, or a member gone.
    checked_by: str | None
    checked_at: dt.datetime | None
    # What the model read, where a member wrote another value.
    read_as: str | None


@dataclass(frozen=True)
class Line:
    """A row of the table: the document's name, its facts of each field and its review in words."""

    document: str
    cells: dict[str, list[Value]]
    review: str

    def unchecked(self) -> list[str]:
        """The fields with a fact no member has checked."""
        return [name for name, facts in self.cells.items() if any(not f.checked for f in facts)]

    def not_found(self) -> list[str]:
        """The fields with a fact no member has checked whose quote is not in the text."""
        return [
            name
            for name, facts in self.cells.items()
            if any(not f.checked and not f.verified for f in facts)
        ]


def lines(project_id: int, template: Template, ask: Ask) -> Iterator[list[Line]]:
    """The table's rows a batch at a time, every page of them."""
    after = None
    while True:
        with SessionLocal() as session:
            table = read_table(
                session, project_id, template, ask, after=after, limit=BATCH, counted=False
            )
            batch = [_line(row, template) for row in table.rows]
        yield batch
        if table.next_after is None:
            return
        after = table.next_after


def _line(row: Row, template: Template) -> Line:
    # The fields in the template's order, which is the columns'.
    return Line(
        row.document.display_title,
        {
            field.name: [_value(fact) for fact in row.cells[field.name]]
            for field in template.fields
            if field.name in row.cells
        },
        STATE_WORDS.get(row.document.review, ""),
    )


def _value(fact: Fact) -> Value:
    check = fact.check
    return Value(
        field=fact.name,
        type=fact.value_type,
        value=fact.value,
        number=fact.number,
        date=fact.date,
        currency=fact.currency,
        quote=fact.quote,
        page=fact.page,
        verified=fact.verified,
        checked=check is not None,
        checked_by=check.checker if check is not None else None,
        checked_at=check.checked_at if check is not None else None,
        read_as=check.read_value if check is not None and check.value is not None else None,
    )


def headings(template: Template) -> list[str]:
    return [DOCUMENT, *(field.name for field in template.fields), REVIEW, UNCHECKED, NOT_FOUND]


def csv_file(project_id: int, template: Template, ask: Ask) -> Iterator[str]:
    """The table as a CSV file, a batch of rows at a time, with a byte order mark first, without
    which Excel reads its names as another encoding."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(headings(template))
    yield "﻿" + _taken(buffer)
    for batch in lines(project_id, template, ask):
        for line in batch:
            writer.writerow(
                [
                    _quoted(line.document),
                    *(
                        _quoted(JOINED.join(fact.value for fact in line.cells.get(field.name, [])))
                        for field in template.fields
                    ),
                    line.review,
                    JOINED.join(line.unchecked()),
                    JOINED.join(line.not_found()),
                ]
            )
        yield _taken(buffer)


def _quoted(text: str) -> str:
    """Text a spreadsheet would run as a formula, quoted, as OWASP advises."""
    return f"'{text}" if text.startswith(FORMULA) else text


def _taken(buffer: io.StringIO) -> str:
    written = buffer.getvalue()
    buffer.seek(0)
    buffer.truncate()
    return written


def workbook_file(project_id: int, template: Template, ask: Ask) -> BinaryIO:
    """The table as an Excel workbook in a temporary file, at its start. The file has no name in
    the directory, so it goes when it is closed, whether its download ended or was dropped."""
    file = tempfile.TemporaryFile(prefix="emudoc-facts-", suffix=".xlsx")
    try:
        write_workbook(file, project_id, template, ask)
    except BaseException:
        file.close()
        raise
    file.seek(0)
    return file


def read_out(file: BinaryIO, size: int = 64 * 1024) -> Iterator[bytes]:
    """The file a piece at a time, closed once read, or once its reader stops."""
    with file:
        while piece := file.read(size):
            yield piece


def write_workbook(file: BinaryIO | Path, project_id: int, template: Template, ask: Ask) -> None:
    """The table as an Excel workbook: its sheet of documents, and one of their facts."""
    book = Workbook(write_only=True)
    table = book.create_sheet("Documents")
    facts = book.create_sheet("Facts")
    names = headings(template)
    _start(table, [(name, 40 if name == DOCUMENT else 18) for name in names])
    _start(facts, FACT_COLUMNS)
    rows = {"table": 1, "facts": 1}

    def add(sheet: WriteOnlyWorksheet, key: str, cells: list[Cell | None]) -> None:
        # The last row a sheet holds says that the rest are left out.
        if rows[key] < SHEET_ROWS - 1:
            sheet.append(cells)
        elif rows[key] == SHEET_ROWS - 1:
            sheet.append([_text(sheet, LEFT_OUT.format(rows=SHEET_ROWS))])
        rows[key] += 1

    for batch in lines(project_id, template, ask):
        for line in batch:
            cells = [line.cells.get(field.name, []) for field in template.fields]
            add(
                table,
                "table",
                [
                    _text(table, line.document),
                    *(_cell(table, values) for values in cells),
                    _text(table, line.review),
                    _text(table, JOINED.join(line.unchecked())),
                    _text(table, JOINED.join(line.not_found())),
                ],
            )
            for values in cells:
                for value in values:
                    add(facts, "facts", _fact_row(facts, line.document, value))
    book.save(file)


def _start(sheet: WriteOnlyWorksheet, columns: Sequence[tuple[str, int]]) -> None:
    """The sheet's widths and its heading, kept in view as the rows scroll."""
    for index, (_, width) in enumerate(columns, 1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    sheet.freeze_panes = "B2"
    bold = Font(bold=True)
    heading = []
    for name, _ in columns:
        cell = _text(sheet, name)
        cell.font = bold
        heading.append(cell)
    sheet.append(heading)


def _cell(sheet: WriteOnlyWorksheet, values: list[Value]) -> Cell | None:
    """A field's cell: a lone value typed, several joined as a text, none empty."""
    if not values:
        return None
    if len(values) == 1:
        return _typed(sheet, values[0])
    return _text(sheet, JOINED.join(value.value for value in values))


def _typed(sheet: WriteOnlyWorksheet, value: Value) -> Cell:
    """A value as a number, an amount in its currency's format or a date, where its type has one;
    else its words."""
    if value.type in ("number", "money") and value.number is not None:
        cell = WriteOnlyCell(sheet, value=value.number)
        if value.type == "money":
            code = (value.currency or "").replace('"', "").replace("\\", "")
            cell.number_format = f'#,##0.00 "{code}"' if code else "#,##0.00"
        return cell
    if value.type == "date" and value.date is not None:
        cell = WriteOnlyCell(sheet, value=value.date)
        cell.number_format = "yyyy-mm-dd"
        return cell
    return _text(sheet, value.value)


def _fact_row(sheet: WriteOnlyWorksheet, document: str, value: Value) -> list:
    checked_at = None
    if value.checked_at is not None:
        # Excel's times have no zone: the heading says it is UTC's.
        checked_at = WriteOnlyCell(
            sheet, value=value.checked_at.astimezone(dt.UTC).replace(tzinfo=None, microsecond=0)
        )
        checked_at.number_format = "yyyy-mm-dd hh:mm"
    return [
        _text(sheet, document),
        _text(sheet, value.field),
        _typed(sheet, value),
        _text(sheet, value.currency or ""),
        _text(sheet, value.value),
        value.page,
        _text(sheet, value.quote or ""),
        value.verified,
        _text(sheet, (value.checked_by or "A former member") if value.checked else ""),
        checked_at,
        _text(sheet, value.read_as or ""),
    ]


def _text(sheet: WriteOnlyWorksheet, text: str) -> Cell | None:
    """A text cell, kept a text whatever it starts with (openpyxl takes a string starting with
    "=" for a formula), without the control characters a workbook cannot hold and within a
    cell's length."""
    if not text:
        return None
    cell = WriteOnlyCell(sheet, value=ILLEGAL_CHARACTERS_RE.sub("", text)[:CELL_CHARS])
    cell.data_type = "s"
    return cell
