"""Excel workbooks as the agent writes, reads and edits them in a member's My files
(app/agent/writing.py; my-files.md), with openpyxl, as app/word_files.py does Word documents.

A workbook is written from sheets of rows of cells, each typed as one types into Excel: a number,
a percentage, a date, TRUE or FALSE, a formula after "=", and text, an apostrophe first forcing
it. It is read back cell by cell with its address, value, formula and number format, and edited by
cells set, sheets added, renamed and deleted, and rows and columns inserted and deleted, every
formula that names what moved moved with it as Excel moves it. An edit's changes apply in order,
each to the workbook the ones before it left, since a row inserted has no address in the reading
to name it by; the edit is made whole or not at all.

openpyxl loses what it cannot read back, so a workbook holding any of that (charts, pictures,
pivot tables, macros, …) is read but never edited. It also writes formulas without their values,
which LibreOffice works out (`Converter.calculate` in app/conversion.py) and `with_values` writes
into openpyxl's own file, so that the view, `read_file`, a copy in the library and any program
reading the file see them. So that working them out fetches nothing, a formula may not reach
outside its workbook, and a workbook whose formulas do is not edited.
"""

import io
import json
import re
import warnings
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any, Literal

from lxml import etree
from openpyxl import Workbook, load_workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE, MergedCell
from openpyxl.formula.tokenizer import Token, Tokenizer, TokenizerError
from openpyxl.styles import Font
from openpyxl.utils import FORMULAE, get_column_letter
from openpyxl.utils.cell import column_index_from_string, range_boundaries
from openpyxl.worksheet.formula import ArrayFormula

# Characters of a workbook one reading shows before it asks the agent to go on, as word_files'.
READ_BUDGET = 12_000
CELL_CHARS = 300
# The most of a workbook's parts, unpacked, these tools read, and the most they open whole to
# edit: openpyxl holds a workbook it edits in memory, many times its size.
MAX_READ = 200 * 1024 * 1024
MAX_EDITED = 30 * 1024 * 1024
# Excel's own limits.
SHEET_CHARS = 31
TEXT_CHARS = 32_767
LAST_ROW = 1_048_576
LAST_COLUMN = 16_384
FORMAT_CHARS = 100
# Rows and columns one change may insert or delete.
MAX_MOVED = 10_000
# Column widths a new sheet's columns are fitted between, in characters.
NARROWEST, WIDEST = 8, 60

Operation = Literal[
    "set",
    "add_sheet",
    "rename_sheet",
    "delete_sheet",
    "insert_rows",
    "delete_rows",
    "insert_columns",
    "delete_columns",
]

# What a workbook may hold that openpyxl would lose by saving it, by the parts that hold it.
HELD = (
    (
        ("xl/drawings/drawing", "xl/charts/", "xl/chartsheets/", "xl/media/"),
        "charts, pictures or shapes",
    ),
    (("xl/pivotTables/", "xl/pivotCache/"), "pivot tables"),
    (("xl/vbaProject.bin",), "macros"),
    (("xl/slicers/", "xl/slicerCaches/", "xl/timelines/", "xl/timelineCaches/"), "slicers"),
    (("xl/threadedComments/",), "threaded comments"),
    (("xl/ctrlProps/", "xl/activeX/", "xl/embeddings/"), "form controls or embedded objects"),
    (("xl/connections.xml", "xl/queryTables/", "xl/model/"), "data connections or queries"),
    (("xl/metadata.xml", "xl/richData/"), "dynamic arrays or linked data types"),
    (("xl/externalLinks/",), "links to other workbooks"),
)
EDIT_IN_EXCEL = "The member may change it in Excel and upload it as its next version."

# Functions that fetch from outside the workbook, which working its formulas out would run.
FETCHING = frozenset(
    {"WEBSERVICE", "RTD", "CALL", "REGISTER", "REGISTER.ID", "IMAGE", "STOCKHISTORY"}
)
# Functions whose result spills into the cells beside, which openpyxl cannot write as such.
SPILLING = frozenset(
    {
        "FILTER",
        "SORT",
        "SORTBY",
        "UNIQUE",
        "SEQUENCE",
        "RANDARRAY",
        "VSTACK",
        "HSTACK",
        "TAKE",
        "DROP",
        "CHOOSECOLS",
        "CHOOSEROWS",
        "TOCOL",
        "TOROW",
        "WRAPROWS",
        "WRAPCOLS",
        "EXPAND",
        "TEXTSPLIT",
        "LAMBDA",
        "MAP",
        "REDUCE",
        "SCAN",
        "MAKEARRAY",
        "BYROW",
        "BYCOL",
        "GROUPBY",
        "PIVOTBY",
    }
)
# The prefix a file gives the functions newer than Excel 2007's, which FORMULAE lists.
NEWER = "_xlfn."

_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")
_INTEGER = re.compile(r"[+-]?\d+")
_LEADING_ZERO = re.compile(r"[+-]?0\d")
_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2})(?::(\d{2}))?)?")
_CELL = re.compile(r"(\$?)([A-Za-z]{1,3})(\$?)(\d+)")
_COLUMN = re.compile(r"(\$?)([A-Za-z]{1,3})")
_ROW = re.compile(r"(\$?)(\d+)")
_OPERAND = re.compile(r"(?:(?P<sheet>'(?:[^']|'')+'|[^'!\[\]:]+)!)?(?P<ref>[^!]+)")
_WHOLE_COLUMNS = re.compile(r"\$?[A-Za-z]{1,3}:\$?[A-Za-z]{1,3}")
_PLAIN_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")
_BAD_SHEET_CHARS = re.compile(r"[\[\]:*?/\\]")

_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_RELS = "http://schemas.openxmlformats.org/package/2006/relationships"
_DOC_RELS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_XML = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)


class Unreadable(Exception):
    """Bytes openpyxl cannot open as a workbook."""


class TooLarge(Unreadable):
    """A workbook larger than these tools read or edit, with the sentence that says so."""


class EditRefused(Exception):
    """A workbook that cannot be written or changed as asked, with the sentences that say why;
    nothing is changed."""


class _Problem(Exception):
    """What keeps one change from being made, which names the change it is raised in."""


@dataclass(frozen=True)
class CellFormat:
    """A number format, as Excel writes them (`#,##0.00`, `0.0%`, `yyyy-mm-dd`), for a cell, a
    range or whole columns (`C:D`)."""

    cells: str
    format: str


@dataclass(frozen=True)
class Sheet:
    """A sheet to write: its name, its rows from A1 as typed, whether the first row is a header
    (bold, and kept in view as the rest scrolls), and number formats."""

    name: str
    rows: list[list[str]]
    header: bool = True
    formats: tuple[CellFormat, ...] = ()


@dataclass(frozen=True)
class Change:
    """One change of an edit. `set` writes `rows` from the cell `cell` (A1 by default) to the right
    and down, `format` given to the cells it sets, or, without rows, `format` to the range `cell`;
    `add_sheet` adds the sheet `name` at the end, with `rows` from A1 and a header unless `header`
    is false; `rename_sheet` gives `sheet` the name `name`; `delete_sheet` deletes it;
    `insert_rows` inserts `count` rows at `row`, the rows from it moving down, and `delete_rows`
    deletes `count` rows from `row`; `insert_columns` and `delete_columns` likewise at the column
    `column`, by its letters. `sheet` is the first sheet when not given."""

    op: Operation
    sheet: str | None = None
    cell: str | None = None
    rows: list[list[str]] | None = None
    format: str | None = None
    name: str | None = None
    row: int | None = None
    column: str | None = None
    count: int | None = None
    header: bool | None = None


@dataclass(frozen=True)
class _Moved:
    """Rows or columns of a sheet inserted (`count` above 0) or deleted (below), from `at`."""

    sheet: str
    axis: Literal["row", "column"]
    at: int
    count: int


@dataclass(frozen=True)
class _Renamed:
    """A sheet renamed `new`, or deleted (`new` None)."""

    old: str
    new: str | None


# Creating


def created(sheets: list[Sheet]) -> bytes:
    """A new workbook of these sheets; `EditRefused` saying what keeps it from being written."""
    if not sheets:
        raise EditRefused("A workbook needs a sheet at least.")
    workbook = Workbook()
    workbook.remove(workbook.active)
    for index, sheet in enumerate(sheets, 1):
        try:
            _add_sheet(workbook, sheet.name, sheet.rows, sheet.header, sheet.formats)
        except _Problem as problem:
            raise EditRefused(f'Sheet {index} ("{sheet.name}"): {problem}') from None
    return _saved(workbook)


def _add_sheet(
    workbook: Any,
    name: str,
    rows: list[list[str]],
    header: bool,
    formats: tuple[CellFormat, ...] = (),
) -> Any:
    _sheet_name(workbook, name)
    sheet = workbook.create_sheet(name)
    _put(sheet, 1, 1, rows)
    if header and rows:
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        if len(rows) > 1:
            sheet.freeze_panes = "A2"
    for each in formats:
        _format(sheet, each.cells, each.format)
    _fit(sheet)
    return sheet


def _put(sheet: Any, top: int, left: int, rows: list[list[str]]) -> tuple[int, int]:
    """Write `rows` as typed from the cell at `top` and `left`; the last row and column written."""
    bottom = top + len(rows) - 1
    right = left + max((len(row) for row in rows), default=1) - 1
    if bottom > LAST_ROW or right > LAST_COLUMN:
        raise _Problem("the cells would run past the sheet's last row or column.")
    for i, row in enumerate(rows):
        for j, text in enumerate(row):
            cell = sheet.cell(top + i, left + j)
            if isinstance(cell, MergedCell):
                raise _Problem(
                    f"{cell.coordinate} is inside merged cells: set the first cell of the merged "
                    "range."
                )
            value, number_format = typed(text)
            if text.startswith("=") and len(text) > 1:
                cell.value = _written_formula(value, cell.coordinate)
            else:
                cell.value = value
                if isinstance(value, str) and value.startswith("="):
                    # Text that only looks like a formula, as an apostrophe made it.
                    cell.data_type = "s"
            if number_format:
                cell.number_format = number_format
    return bottom, right


def typed(text: str) -> tuple[Any, str | None]:
    """What a cell holds once `text` is typed into it, and the number format it takes, as Excel
    reads what is typed: "" empties it, an apostrophe first keeps the rest as text, "=" begins a
    formula, TRUE and FALSE, a number with a point for its decimals, a percentage, a date or a date
    and time as 2026-03-01 14:30. Digits with a leading zero, or more than Excel keeps (15), stay
    text: they are numbers of things, not amounts."""
    if text == "":
        return None, None
    if text.startswith("'"):
        return _text(text[1:]), None
    if text.startswith("=") and len(text) > 1:
        return text, None
    stripped = text.strip()
    if stripped.upper() in ("TRUE", "FALSE"):
        return stripped.upper() == "TRUE", None
    if stripped.endswith("%") and _NUMBER.fullmatch(stripped[:-1]):
        decimals = len(stripped[:-1].partition(".")[2])
        return float(stripped[:-1]) / 100, "0%" if not decimals else f"0.{'0' * decimals}%"
    if _NUMBER.fullmatch(stripped) and not _kept_as_text(stripped):
        return (int(stripped) if _INTEGER.fullmatch(stripped) else float(stripped)), None
    if found := _DATE.fullmatch(stripped):
        try:
            year, month, day, hour, minute, second = (int(part or 0) for part in found.groups())
            if found[4] is None:
                return date(year, month, day), "yyyy-mm-dd"
            return datetime(year, month, day, hour, minute, second), "yyyy-mm-dd hh:mm"
        except ValueError:
            pass
    return _text(text), None


def _kept_as_text(number: str) -> bool:
    digits = sum(character.isdigit() for character in number.partition("e")[0].partition("E")[0])
    return bool(_LEADING_ZERO.match(number)) or digits > 15


def _text(text: str) -> str:
    return ILLEGAL_CHARACTERS_RE.sub("", text)[:TEXT_CHARS]


def _written_formula(formula: str, where: str) -> str:
    """The formula as a file holds it, its newer functions prefixed; `_Problem` for one that would
    fetch from outside the workbook or spill."""
    try:
        tokens = Tokenizer(formula)
    except TokenizerError:
        raise _Problem(f"{where}: {formula} is not a formula Excel reads.") from None
    for token in tokens.items:
        if token.type == Token.FUNC and token.subtype == Token.OPEN:
            name = _bare(token.value[:-1]).upper()
            if name in SPILLING:
                raise _Problem(
                    f"{where}: {name} spills its result into the cells beside it, which these "
                    "tools cannot write. Write the values, or a formula in each cell."
                )
            if not token.value.startswith("_xl") and name not in FORMULAE:
                token.value = NEWER + token.value
    written = tokens.render()
    if reason := _reaches_out(written):
        raise _Problem(f"{where}: the formula {reason}, which a formula here may not do.")
    return written


def _reaches_out(formula: str) -> str | None:
    """Why the formula reaches outside its workbook, or None."""
    try:
        items = Tokenizer(formula).items
    except TokenizerError:
        # Such as a link to another program, cmd|'…'!A0, which the tokenizer does not take.
        return "cannot be read"
    for token in items:
        if token.type == Token.FUNC and token.subtype == Token.OPEN:
            name = _bare(token.value[:-1]).upper()
            if name in FETCHING:
                return f"calls {name}, which fetches from outside the workbook"
        if token.type == Token.OPERAND and token.subtype == Token.RANGE:
            sheet, _, _ = token.value.rpartition("!")
            if "|" in sheet:
                return "starts another program"
            if "[" in sheet or ":\\" in sheet or "/" in sheet:
                return "takes values from another workbook"
    return None


def _bare(formula: str) -> str:
    """A formula as one types it: without the prefixes a file gives newer functions."""
    return formula.replace("_xlfn._xlws.", "").replace(NEWER, "")


def _format(sheet: Any, cells: str, number_format: str) -> str:
    """Give the cells the number format; the range they are, in words."""
    if not number_format or len(number_format) > FORMAT_CHARS or "\n" in number_format:
        raise _Problem(f"a number format is up to {FORMAT_CHARS} characters on one line.")
    top, left, bottom, right = _span(sheet, cells)
    for row in sheet.iter_rows(min_row=top, max_row=bottom, min_col=left, max_col=right):
        for cell in row:
            cell.number_format = number_format
    return _range_words(top, left, bottom, right)


def _span(sheet: Any, cells: str) -> tuple[int, int, int, int]:
    """The rows and columns a cell, a range or whole columns are, within the cells the sheet uses
    beyond the first cell named: openpyxl formats a cell at a time."""
    try:
        left, top, right, bottom = range_boundaries(cells.replace("$", "").strip().upper())
    except ValueError:
        raise _Problem(f'"{cells}" is no cell or range, such as B4, B4:D9 or C:D.') from None
    if left is None or right is None:
        raise _Problem(f'"{cells}" names whole rows; name cells or columns, such as A3:F3.')
    if top is None or bottom is None:
        top, bottom = 1, sheet.max_row
    if bottom > LAST_ROW or right > LAST_COLUMN:
        raise _Problem(f'"{cells}" runs past the sheet.')
    return top, left, min(bottom, max(sheet.max_row, top)), min(right, max(sheet.max_column, left))


def _fit(sheet: Any) -> None:
    """Widen each column to the longest text or number in it, within NARROWEST and WIDEST."""
    widths: dict[int, int] = {}
    for row in sheet.iter_rows():
        for cell in row:
            value = cell.value
            if value is None or (isinstance(value, str) and value.startswith("=")):
                continue
            shown = value.isoformat() if isinstance(value, date | datetime) else str(value)
            longest = max(len(line) for line in shown.split("\n"))
            widths[cell.column] = max(widths.get(cell.column, 0), longest)
    for column, width in widths.items():
        sheet.column_dimensions[get_column_letter(column)].width = min(
            max(width + 2, NARROWEST), WIDEST
        )


def _sheet_name(workbook: Any, name: str, *, renaming: Any = None) -> None:
    """`_Problem` unless `name` is one Excel takes for a new sheet of the workbook."""
    if not name or len(name) > SHEET_CHARS or name != name.strip():
        raise _Problem(
            f"a sheet's name is 1 to {SHEET_CHARS} characters, without spaces at its ends."
        )
    if _BAD_SHEET_CHARS.search(name) or name.startswith("'") or name.endswith("'"):
        raise _Problem(
            f'"{name}" cannot name a sheet: it may not hold [ ] : * ? / \\ or begin or end with \'.'
        )
    if name.lower() == "history":
        raise _Problem('Excel keeps "History" for itself; choose another name.')
    taken = [each.title for each in workbook.worksheets if each is not renaming]
    if name.lower() in (title.lower() for title in taken):
        raise _Problem(f'the workbook has a sheet "{name}" already.')


def _saved(workbook: Any) -> bytes:
    out = io.BytesIO()
    workbook.save(out)
    return out.getvalue()


# Reading


def reading(data: bytes, sheet: str | None = None, start: int = 1) -> str:
    """The workbook as the agent reads it, from row `start` of `sheet` (the first by default) on:
    each sheet's used range, then a line a row of its cells, each with its address, its value and
    its formula, up to READ_BUDGET characters."""
    names = _names(data, MAX_READ, "read")
    formulas = _opened(data, read_only=True)
    values = _opened(data, read_only=True, data_only=True)
    try:
        sheets = formulas.worksheets
        shown = values.worksheets
        first = 0
        if sheet is not None:
            found = [i for i, each in enumerate(sheets) if each.title.lower() == sheet.lower()]
            if not found:
                return f'The workbook has no sheet "{sheet}". Its sheets: {_titles(sheets)}.'
            first = found[0]
        lines = [
            f"{_counted(len(sheets), 'sheet')}: {', '.join(_extent(each) for each in sheets)}."
        ]
        if held := _held(names):
            lines.append(f"It holds {held}, so it is read here but changed only in Excel.")
        used = 0
        for index in range(first, len(sheets)):
            begin = start if index == first else 1
            title = sheets[index].title
            lines.append(f'Sheet "{title}"' + (f", from row {begin}:" if begin > 1 else ":"))
            empty = True
            rows = zip(
                sheets[index].iter_rows(min_row=begin),
                shown[index].iter_rows(min_row=begin),
                strict=False,
            )
            for row, row_values in rows:
                words = [
                    _cell_words(cell, value)
                    for cell, value in zip(row, row_values, strict=False)
                    if cell.value is not None
                ]
                if not words:
                    continue
                line = " | ".join(words)
                number = next(cell.row for cell in row if cell.value is not None)
                if used and used + len(line) > READ_BUDGET:
                    lines.append(
                        f'... Call read_file again with sheet="{title}" and from_row={number} '
                        "to continue."
                    )
                    return "\n".join(lines)
                lines.append(line)
                used += len(line)
                empty = False
            if empty:
                lines.append("(empty)" if begin == 1 else "(nothing from there)")
        return "\n".join(lines)
    finally:
        formulas.close()
        values.close()


def _cell_words(cell: Any, value_cell: Any) -> str:
    """A cell as read: its address, then its value, or its formula and the value it gives, and the
    number format of a number that has one."""
    formula = cell.value
    if isinstance(formula, ArrayFormula):
        formula = formula.text
    value = value_cell.value
    if cell.data_type == "f":
        result = _shown(value, value_cell.data_type) if value is not None else "(not worked out)"
        words = f"{cell.coordinate} {_bare(str(formula))} → {result}"
    else:
        words = f"{cell.coordinate} {_shown(value, value_cell.data_type)}"
    number_format = cell.number_format
    if (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and number_format
        and number_format != "General"
    ):
        words += f" [{number_format}]"
    return words


def _shown(value: Any, data_type: str | None) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float):
        return f"{value:.15g}"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, datetime):
        return value.date().isoformat() if value.time() == time() else value.isoformat(" ")
    if isinstance(value, date | time):
        return value.isoformat()
    text = str(value)
    if data_type == "e":
        return text
    if len(text) > CELL_CHARS:
        text = text[:CELL_CHARS] + "…"
    return json.dumps(text, ensure_ascii=False)


def _extent(sheet: Any) -> str:
    try:
        extent = sheet.calculate_dimension()
    except ValueError:
        return f'"{sheet.title}"'
    return f'"{sheet.title}" ({extent})'


# Editing


def edited(data: bytes, changes: list[Change]) -> tuple[bytes, str]:
    """The workbook with the changes made in order, and what was done in words; `EditRefused`
    when the workbook may not be edited here, or any change cannot be made."""
    workbook = _editable(data)
    done = []
    for index, change in enumerate(changes, 1):
        try:
            done.append(_apply(workbook, change))
        except _Problem as problem:
            raise EditRefused(f"Change {index} ({change.op}): {problem}") from None
    return _saved(workbook), "; ".join(done)


def _editable(data: bytes) -> Any:
    """The workbook opened whole to edit, or `EditRefused` saying why it may not be."""
    names = _names(data, MAX_EDITED, "edit")
    if held := _held(names):
        raise EditRefused(
            f"The workbook holds {held}, which these tools would lose. {EDIT_IN_EXCEL}"
        )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        workbook = _opened(data, rich_text=True)
    lost = [str(warning.message) for warning in caught if "will be removed" in str(warning.message)]
    if lost:
        what = lost[0].split(" extension")[0].lower()
        raise EditRefused(
            f"The workbook holds {what} of a kind these tools would lose. {EDIT_IN_EXCEL}"
        )
    for home, formula, _ in _every_formula(workbook):
        if reason := _reaches_out(formula):
            raise EditRefused(
                f'A formula of the sheet "{home}" {reason}, which these tools do not work out. '
                f"{EDIT_IN_EXCEL}"
            )
    return workbook


def _apply(workbook: Any, change: Change) -> str:
    """Make one change; what was done, in words."""
    if change.op == "add_sheet":
        if not change.name:
            raise _Problem("give the new sheet's name as name.")
        rows = change.rows or []
        _add_sheet(
            workbook, change.name, rows, bool(rows) if change.header is None else change.header
        )
        return f'added the sheet "{change.name}"'
    sheet = _sheet(workbook, change.sheet)
    if change.op == "set":
        return _set(sheet, change)
    if change.op == "rename_sheet":
        if not change.name:
            raise _Problem("give the sheet's new name as name.")
        _sheet_name(workbook, change.name, renaming=sheet)
        old = sheet.title
        sheet.title = change.name
        _transform(workbook, _Renamed(old, change.name))
        return f'renamed the sheet "{old}" "{change.name}"'
    if change.op == "delete_sheet":
        if len(workbook.worksheets) == 1:
            raise _Problem("a workbook keeps at least one sheet.")
        workbook.remove(sheet)
        _transform(workbook, _Renamed(sheet.title, None))
        return f'deleted the sheet "{sheet.title}"'
    return _move(workbook, sheet, change)


def _set(sheet: Any, change: Change) -> str:
    if change.rows is None:
        if change.format is None or change.cell is None:
            raise _Problem("give rows of cells to set from cell, or a format and the cells for it.")
        cells = _format(sheet, change.cell, change.format)
        return f'formatted {cells} of "{sheet.title}"'
    if not change.rows or not any(change.rows):
        raise _Problem("give the rows of cells to set.")
    top, left = _address((change.cell or "A1").split(":")[0])
    bottom, right = _put(sheet, top, left, change.rows)
    cells = _range_words(top, left, bottom, right)
    if change.format is not None:
        _format(sheet, cells, change.format)
    return f'set {cells} of "{sheet.title}"'


def _move(workbook: Any, sheet: Any, change: Change) -> str:
    count = 1 if change.count is None else change.count
    if not 1 <= count <= MAX_MOVED:
        raise _Problem(f"count is 1 to {MAX_MOVED:,}.")
    rows = change.op in ("insert_rows", "delete_rows")
    inserting = change.op.startswith("insert")
    if rows:
        if change.row is None or not 1 <= change.row <= LAST_ROW:
            raise _Problem("give the row by its number as row.")
        at = change.row
        what = f"row {at}" if count == 1 else f"rows {at}–{at + count - 1}"
    else:
        if change.column is None or not _COLUMN.fullmatch(change.column.strip()):
            raise _Problem("give the column by its letters as column, such as C.")
        at = column_index_from_string(change.column.strip().replace("$", "").upper())
        if at > LAST_COLUMN:
            raise _Problem(f"{change.column} runs past the sheet.")
        what = (
            f"column {get_column_letter(at)}"
            if count == 1
            else f"columns {get_column_letter(at)}–{get_column_letter(at + count - 1)}"
        )
    if reason := _structured(sheet):
        raise _Problem(
            f'the sheet "{sheet.title}" has {reason}, which these tools cannot move with its rows '
            f"and columns. {EDIT_IN_EXCEL}"
        )
    moved = _Moved(sheet.title, "row" if rows else "column", at, count if inserting else -count)
    merged = [str(each) for each in sheet.merged_cells.ranges]
    for each in merged:
        sheet.unmerge_cells(each)
    if rows:
        (sheet.insert_rows if inserting else sheet.delete_rows)(at, count)
    else:
        (sheet.insert_cols if inserting else sheet.delete_cols)(at, count)
    for each in merged:
        shifted = _moved_ref(each, moved)
        if shifted is not None and len(set(shifted.split(":"))) == 2:
            sheet.merge_cells(shifted)
    if sheet.auto_filter.ref:
        sheet.auto_filter.ref = _moved_ref(sheet.auto_filter.ref, moved)
    for cell in sheet._cells.values():
        # openpyxl moves a cell without its link's own address.
        if getattr(cell, "hyperlink", None) is not None:
            cell.hyperlink.ref = cell.coordinate
        if isinstance(cell.value, ArrayFormula):
            cell.value.ref = _moved_ref(cell.value.ref, moved) or cell.coordinate
    _transform(workbook, moved)
    return f'{"inserted" if inserting else "deleted"} {what} of "{sheet.title}"'


def _structured(sheet: Any) -> str | None:
    """What the sheet holds whose ranges inserting or deleting rows would leave behind."""
    held = []
    if len(sheet.conditional_formatting):
        held.append("conditional formatting")
    if sheet.data_validations.dataValidation:
        held.append("data validation")
    if sheet.tables:
        held.append("tables")
    return " and ".join(held) or None


def _sheet(workbook: Any, name: str | None) -> Any:
    sheets = workbook.worksheets
    if name is None:
        return sheets[0]
    for sheet in sheets:
        if sheet.title.lower() == name.strip().lower():
            return sheet
    raise _Problem(f'the workbook has no sheet "{name}". Its sheets: {_titles(sheets)}.')


def _address(text: str) -> tuple[int, int]:
    found = _CELL.fullmatch(text.strip())
    if found is None:
        raise _Problem(f'"{text}" is no cell, such as B4.')
    row, column = int(found[4]), column_index_from_string(found[2].upper())
    if not 1 <= row <= LAST_ROW or column > LAST_COLUMN:
        raise _Problem(f'"{text}" is outside the sheet.')
    return row, column


# Formulas that name what moved


def _every_formula(workbook: Any) -> Iterator[tuple[str | None, str, Callable[[str], None]]]:
    """Every formula of the workbook with the sheet it is on (None for a name of the workbook)
    and how to put it back changed: the cells', the names', and those of data validation and
    conditional formatting."""
    for sheet in workbook.worksheets:
        for cell in sheet._cells.values():
            if cell.data_type != "f":
                continue
            value = cell.value
            if isinstance(value, ArrayFormula):
                yield (
                    sheet.title,
                    value.text,
                    lambda text, value=value: setattr(value, "text", text),
                )
            elif isinstance(value, str):
                yield sheet.title, value, lambda text, cell=cell: setattr(cell, "value", text)
        for name in sheet.defined_names.values():
            if name.attr_text:
                yield sheet.title, f"={name.attr_text}", _setter(name, "attr_text")
        for validation in sheet.data_validations.dataValidation:
            for field in ("formula1", "formula2"):
                if getattr(validation, field):
                    yield sheet.title, f"={getattr(validation, field)}", _setter(validation, field)
        for formatting in sheet.conditional_formatting:
            for rule in formatting.rules:
                for index, formula in enumerate(rule.formula or []):
                    yield sheet.title, f"={formula}", _list_setter(rule.formula, index)
    for name in workbook.defined_names.values():
        if name.attr_text:
            yield None, f"={name.attr_text}", _setter(name, "attr_text")


def _setter(owner: Any, field: str) -> Callable[[str], None]:
    return lambda text: setattr(owner, field, text[1:])


def _list_setter(items: list[str], index: int) -> Callable[[str], None]:
    def put(text: str) -> None:
        items[index] = text[1:]

    return put


def _transform(workbook: Any, change: _Moved | _Renamed) -> None:
    """Every formula of the workbook changed as Excel changes it when rows or columns move or a
    sheet is renamed or deleted."""
    for home, formula, put in list(_every_formula(workbook)):
        try:
            tokens = Tokenizer(formula)
        except TokenizerError:
            continue
        changed = False
        for token in tokens.items:
            if token.type == Token.OPERAND and token.subtype == Token.RANGE:
                new = _operand(token.value, home, change)
                if new != token.value:
                    token.value = new
                    changed = True
        if changed:
            put(tokens.render())


def _operand(text: str, home: str | None, change: _Moved | _Renamed) -> str:
    found = _OPERAND.fullmatch(text)
    if found is None:
        return text
    written, ref = found["sheet"], found["ref"]
    sheet = _unquoted(written) if written else home
    if sheet is None:
        return text
    if isinstance(change, _Renamed):
        if sheet.lower() != change.old.lower():
            return text
        if change.new is None:
            return "#REF!"
        return f"{_quoted(change.new)}!{ref}" if written else text
    if sheet.lower() != change.sheet.lower():
        return text
    moved = _moved_ref(ref, change)
    if moved is None:
        return "#REF!"
    return f"{written}!{moved}" if written else moved


def _moved_ref(ref: str, change: _Moved) -> str | None:
    """A reference within the sheet whose rows or columns moved, moved with them as Excel moves
    it: a cell after those inserted moves on, one deleted is gone (None), and a range takes in what
    was inserted within it and loses what was deleted. Anything else, a name among them, is kept."""
    parts = ref.split(":")
    if len(parts) > 2:
        return ref
    kinds = [_kind(part) for part in parts]
    if None in kinds or len(set(kinds)) > 1 or (len(parts) == 1 and kinds[0] != "cell"):
        return ref
    kind = kinds[0]
    if (change.axis == "row" and kind == "column") or (change.axis == "column" and kind == "row"):
        return ref
    shifted = []
    for index, part in enumerate(parts):
        end = "start" if len(parts) == 2 and index == 0 else ("end" if len(parts) == 2 else "only")
        new = _shift(part, kind, change, end)
        if new is None:
            return None
        shifted.append(new)
    if len(shifted) == 2 and _position(shifted[0], kind, change.axis) > _position(
        shifted[1], kind, change.axis
    ):
        return None
    return ":".join(shifted)


def _kind(part: str) -> str | None:
    if _CELL.fullmatch(part):
        return "cell"
    if _COLUMN.fullmatch(part):
        return "column"
    if _ROW.fullmatch(part):
        return "row"
    return None


def _position(part: str, kind: str, axis: str) -> int:
    if kind == "cell":
        found = _CELL.fullmatch(part)
        assert found is not None
        return int(found[4]) if axis == "row" else column_index_from_string(found[2].upper())
    if kind == "row":
        return int(part.lstrip("$"))
    return column_index_from_string(part.lstrip("$").upper())


def _shift(part: str, kind: str, change: _Moved, end: str) -> str | None:
    """One end of a reference moved; None when what it names was deleted."""
    if kind == "cell":
        found = _CELL.fullmatch(part)
        assert found is not None
        column_mark, letters, row_mark, digits = found.groups()
        if change.axis == "row":
            row = _moved_number(int(digits), change, end, LAST_ROW)
            return None if row is None else f"{column_mark}{letters}{row_mark}{row}"
        column = _moved_number(column_index_from_string(letters.upper()), change, end, LAST_COLUMN)
        if column is None:
            return None
        return f"{column_mark}{get_column_letter(column)}{row_mark}{digits}"
    mark = "$" if part.startswith("$") else ""
    if kind == "row":
        row = _moved_number(int(part.lstrip("$")), change, end, LAST_ROW)
        return None if row is None else f"{mark}{row}"
    column = _moved_number(
        column_index_from_string(part.lstrip("$").upper()), change, end, LAST_COLUMN
    )
    return None if column is None else f"{mark}{get_column_letter(column)}"


def _moved_number(number: int, change: _Moved, end: str, last: int) -> int | None:
    if change.count > 0:
        if number < change.at:
            return number
        moved = number + change.count
        return moved if moved <= last else None
    first, final = change.at, change.at - change.count - 1
    if number < first:
        return number
    if number > final:
        return number + change.count
    # Within what was deleted: a range keeps what is left of it, a cell is gone.
    if end == "start":
        return first
    if end == "end":
        return first - 1 if first > 1 else None
    return None


def _unquoted(written: str) -> str:
    if written.startswith("'") and written.endswith("'"):
        return written[1:-1].replace("''", "'")
    return written


def _quoted(name: str) -> str:
    if _PLAIN_NAME.fullmatch(name) and _kind(name) is None:
        return name
    return "'" + name.replace("'", "''") + "'"


# The file itself


def _names(data: bytes, most: int, verb: str) -> list[str]:
    """The workbook's parts; `Unreadable` for what is no workbook, `TooLarge` for one larger
    than `most` unpacked."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as package:
            entries = package.infolist()
    except (zipfile.BadZipFile, ValueError):
        raise Unreadable("not a ZIP package") from None
    if sum(entry.file_size for entry in entries) > most:
        raise TooLarge(
            f"The workbook is larger than these tools {verb} ({most // (1024 * 1024)} MB "
            f"unpacked). {EDIT_IN_EXCEL}"
        )
    return [entry.filename for entry in entries]


def _opened(data: bytes, **options: Any) -> Any:
    try:
        return load_workbook(io.BytesIO(data), **options)
    except Exception as error:
        raise Unreadable(type(error).__name__) from None


def _held(names: list[str]) -> str | None:
    """What of the workbook's parts openpyxl would lose, in words, or None."""
    found = [words for prefixes, words in HELD if any(name.startswith(prefixes) for name in names)]
    if not found:
        return None
    return found[0] if len(found) == 1 else f"{', '.join(found[:-1])} and {found[-1]}"


def has_formulas(data: bytes) -> bool:
    """Whether any sheet of the workbook holds a formula."""
    with zipfile.ZipFile(io.BytesIO(data)) as package:
        for name in package.namelist():
            if name.startswith("xl/worksheets/") and name.endswith(".xml"):
                part = package.read(name)
                if b"<f>" in part or b"<f " in part:
                    return True
    return False


def with_values(written: bytes, calculated: bytes) -> bytes:
    """`written`, a workbook openpyxl saved, with the value of each of its formulas as
    `calculated`, the same workbook saved by LibreOffice, worked them out, every other part as
    openpyxl wrote it. `Unreadable` when `calculated` is not such a workbook."""
    try:
        values = _formula_values(calculated)
    except (zipfile.BadZipFile, KeyError, ValueError, etree.XMLSyntaxError):
        raise Unreadable("the worked out workbook could not be read") from None
    out = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(written)) as source,
        zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target,
    ):
        parts = {part: name for name, part in _sheet_parts(source).items()}
        for entry in source.infolist():
            data = source.read(entry)
            sheet = parts.get(entry.filename)
            if sheet is not None and values.get(sheet):
                data = _filled(data, values[sheet])
            target.writestr(entry, data)
    return out.getvalue()


def _sheet_parts(package: zipfile.ZipFile) -> dict[str, str]:
    """Each sheet's part in the package, by the sheet's name."""
    workbook = etree.fromstring(package.read("xl/workbook.xml"), _XML)
    relations = etree.fromstring(package.read("xl/_rels/workbook.xml.rels"), _XML)
    targets = {
        relation.get("Id"): relation.get("Target")
        for relation in relations.iter(f"{{{_RELS}}}Relationship")
    }
    parts = {}
    for sheet in workbook.iter(f"{{{_MAIN}}}sheet"):
        target = targets.get(sheet.get(f"{{{_DOC_RELS}}}id"), "")
        parts[sheet.get("name")] = target.lstrip("/") if target.startswith("/") else f"xl/{target}"
    return parts


def _formula_values(calculated: bytes) -> dict[str, dict[str, tuple[str, str]]]:
    """Each formula cell's type and value in the workbook LibreOffice saved, by sheet and
    address."""
    values: dict[str, dict[str, tuple[str, str]]] = {}
    with zipfile.ZipFile(io.BytesIO(calculated)) as package:
        if sum(entry.file_size for entry in package.infolist()) > MAX_READ:
            raise ValueError("too large")
        strings: list[str] = []
        if "xl/sharedStrings.xml" in package.namelist():
            shared = etree.fromstring(package.read("xl/sharedStrings.xml"), _XML)
            strings = ["".join(item.itertext()) for item in shared.iter(f"{{{_MAIN}}}si")]
        for name, part in _sheet_parts(package).items():
            cells: dict[str, tuple[str, str]] = {}
            sheet = etree.fromstring(package.read(part), _XML)
            for cell in sheet.iter(f"{{{_MAIN}}}c"):
                if cell.find(f"{{{_MAIN}}}f") is None:
                    continue
                kind = cell.get("t", "n")
                value = cell.find(f"{{{_MAIN}}}v")
                if kind == "inlineStr":
                    text = "".join(cell.find(f"{{{_MAIN}}}is").itertext())
                    cells[cell.get("r")] = ("str", text)
                elif value is not None and value.text is not None:
                    if kind == "s":
                        cells[cell.get("r")] = ("str", strings[int(value.text)])
                    else:
                        cells[cell.get("r")] = (kind, value.text)
            values[name] = cells
    return values


def _filled(part: bytes, values: dict[str, tuple[str, str]]) -> bytes:
    """A sheet's part with the values of its formula cells written in."""
    sheet = etree.fromstring(part, _XML)
    for cell in sheet.iter(f"{{{_MAIN}}}c"):
        formula = cell.find(f"{{{_MAIN}}}f")
        found = values.get(cell.get("r"))
        if formula is None or found is None:
            continue
        kind, text = found
        if kind == "n":
            cell.attrib.pop("t", None)
        else:
            cell.set("t", kind)
        value = cell.find(f"{{{_MAIN}}}v")
        if value is None:
            value = etree.SubElement(cell, f"{{{_MAIN}}}v")
        value.text = text
    return etree.tostring(sheet, xml_declaration=True, encoding="UTF-8", standalone=True)


def _range_words(top: int, left: int, bottom: int, right: int) -> str:
    first = f"{get_column_letter(left)}{top}"
    last = f"{get_column_letter(right)}{bottom}"
    return first if first == last else f"{first}:{last}"


def _titles(sheets: list[Any]) -> str:
    return ", ".join(f'"{sheet.title}"' for sheet in sheets)


def _counted(n: int, thing: str) -> str:
    return f"{n} {thing}{'' if n == 1 else 's'}"
