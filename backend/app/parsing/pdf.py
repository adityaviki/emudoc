"""PDF parsing with pdfplumber: lines grouped into blocks, headings found by font."""

import copy
import math
import re
from bisect import bisect_left, bisect_right
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber
from pdfminer.pdfdocument import PDFEncryptionError, PDFPasswordIncorrect
from pdfplumber.page import Page
from pdfplumber.utils.exceptions import PdfminerException

from app.parsing.types import (
    BBox,
    FileTooLarge,
    ParsedBlock,
    ParsedDocument,
    UnreadableFile,
    clean_text,
    table_to_markdown,
)

# Share of the page height, at top and bottom, where running headers and footers sit.
MARGIN_BAND = 0.08
HEADING_SIZE_RATIO = 1.15
MAX_HEADING_CHARS = 200
# Characters turned against the rest of their page, fewer than this, are left out rather than
# read in a frame of their own: a turned arrow or symbol, not a line of text.
MIN_TURNED_CHARS = 4
# Text on each side of a gutter is read as columns, each whole before the next, when each side
# holds a table or a line of at least this many characters: labels beside their values, or a
# short note beside a paragraph, are not columns of text.
MIN_COLUMN_CHARS = 20
# The gutters tried on a page, those the most blocks start at: a drawing's hundreds of labels
# would otherwise each be tried against all the others.
MAX_GUTTERS = 32
# Whitespace across the page this many lines high ends a band of columns, as a heading or a
# table across the page does: what stands below the columns is not the last column's.
BAND_GAP = 2.0

_BOLD = re.compile(r"bold|black|heavy|semibold|demi", re.IGNORECASE)
_LIST_START = re.compile(
    r"^(?:[•◦▪■●○‣·–—-]|\(?\d{1,3}[.)]|\(?[a-zA-Z][.)]|\(?[ivxIVX]{2,5}[.)])\s+\S"
)
_PAGE_NUMBER = re.compile(r"^(?:page\s+)?[-–—\s]*\d+[-–—\s]*(?:(?:of|/)\s*\d+)?$", re.IGNORECASE)
_DIGITS = re.compile(r"\d+")
# pdfminer writes glyphs it cannot map to Unicode (often bullets) as "(cid:N)".
_UNMAPPED_GLYPH = re.compile(r"\(cid:\d+\)")

LOCKED = "This PDF is protected by a password. Save a copy without the password and upload that."
# Encryption pdfminer does not do: a certificate instead of a password, or an algorithm it lacks.
UNKNOWN_PROTECTION = (
    "This PDF's protection cannot be opened here. Save a copy without the protection and upload "
    "that."
)
NO_PAGES = "This PDF has no pages. Export it again from the program that made it and upload that."


@dataclass
class _Line:
    """A line of text, placed in the frame it was read in: the page turned `turn` degrees
    anticlockwise from how it is shown, so that the line reads upright, `frame` that turned
    page's width and height (`_shown` places it on the page as shown)."""

    text: str
    x0: float
    top: float
    x1: float
    bottom: float
    size: float
    bold: bool
    turn: int
    frame: tuple[float, float]

    @property
    def height(self) -> float:
        return self.bottom - self.top

    @property
    def shown(self) -> BBox:
        return _shown((self.x0, self.top, self.x1, self.bottom), self.turn, self.frame)


@dataclass
class _Table:
    bbox: BBox
    markdown: str
    turn: int
    frame: tuple[float, float]

    @property
    def shown(self) -> BBox:
        return _shown(self.bbox, self.turn, self.frame)


@dataclass
class _Group:
    """Consecutive lines that form one block."""

    page: int
    lines: list[_Line] = field(default_factory=list)

    @property
    def size(self) -> float:
        return self.lines[0].size

    @property
    def bold(self) -> bool:
        return all(line.bold for line in self.lines)

    @property
    def turn(self) -> int:
        return self.lines[0].turn

    @property
    def bbox(self) -> BBox:
        return (
            min(line.x0 for line in self.lines),
            min(line.top for line in self.lines),
            max(line.x1 for line in self.lines),
            max(line.bottom for line in self.lines),
        )

    @property
    def shown(self) -> BBox:
        return _shown(self.bbox, self.turn, self.lines[0].frame)

    @property
    def text(self) -> str:
        parts: list[str] = []
        for line in self.lines:
            text = line.text
            # Rejoin a word hyphenated across a line break.
            if parts and re.search(r"[a-z]-$", parts[-1]) and text[:1].islower():
                parts[-1] = parts[-1][:-1] + text
            else:
                parts.append(text)
        return " ".join(parts)


@dataclass
class _PageRead:
    """What reading one page found, before the margins, the body's size and the headings of
    the whole document are known: its lines and tables, how far its own text is turned (text
    turned against it is read after it), and whether it is a picture alone, a scan."""

    number: int
    size: tuple[float, float]
    turn: int
    lines: list[_Line]
    tables: list[_Table]
    scanned: bool


@dataclass
class PdfPart:
    """Pages of a PDF read, in order, with what the file says of itself.

    A PDF too long for one parse child is read a range of pages at a time (app/parsing/
    limits.py), each range a part kept as JSON (`part_to_json`) until every part is read, and
    the document is made of them all at once (`assemble`): a running header is one that recurs
    across the whole document, and a heading's level is ranked among all its headings.
    """

    page_count: int
    title: str | None
    pages: list[_PageRead]

    @property
    def chars(self) -> int:
        """The characters of text its pages hold, about those they bring the document."""
        return sum(
            sum(len(line.text) for line in page.lines)
            + sum(len(table.markdown) for table in page.tables)
            for page in self.pages
        )

    @property
    def scanned(self) -> int:
        return sum(page.scanned for page in self.pages)


def parse_pdf(path: Path, max_pages: int = 0) -> ParsedDocument:
    """Parse the PDF; one with more than `max_pages` pages (0: any number) is refused."""
    return assemble([read_pdf(path, max_pages)])


def read_pdf(path: Path, max_pages: int = 0, first: int = 1, last: int = 0) -> PdfPart:
    """Read pages `first` to `last` of the PDF (0: to its end). One with more than `max_pages`
    pages (0: any number) is refused before a page is read, whichever are asked for."""
    pages: list[_PageRead] = []
    with _open(path) as pdf:
        title = _metadata_title(pdf.metadata.get("Title"))
        if not pdf.pages:
            raise UnreadableFile(NO_PAGES)
        if max_pages and len(pdf.pages) > max_pages:
            raise FileTooLarge(
                f"This PDF has {len(pdf.pages):,} pages, more than the {max_pages:,} that can be "
                "indexed. Split it and upload the parts."
            )
        for number, page in enumerate(pdf.pages[first - 1 : last or None], start=first):
            lines, tables, turn = _read(page)
            scanned = not lines and not tables and bool(page.images)
            size = (float(page.width), float(page.height))
            pages.append(_PageRead(number, size, turn, lines, tables, scanned))
            page.close()
        return PdfPart(len(pdf.pages), title, pages)


def assemble(parts: list[PdfPart]) -> ParsedDocument:
    """The document that the parts' pages make, which must be every page of the PDF once and
    in order."""
    read = [page for part in parts for page in part.pages]
    if [page.number for page in read] != list(range(1, parts[0].page_count + 1)):
        raise ValueError("The parts do not hold every page of the PDF in order.")
    pages = [(page.lines, page.tables) for page in read]
    repeated = _repeated_margin_lines(pages)
    body_size = _body_size(pages)
    turns = [page.turn for page in read]

    items: list[_Group | tuple[int, _Table]] = []
    for number, (lines, tables) in enumerate(pages, start=1):
        kept = [line for line in lines if not _is_margin_noise(line, repeated)]
        own = [line for line in kept if line.turn == turns[number - 1]]
        aside = [line for line in kept if line.turn != turns[number - 1]]
        parts_read = _in_reading_order(list(_group_lines(own, number)), tables)
        for part_groups, part_tables in [*parts_read, (list(_group_lines(aside, number)), [])]:
            groups: list[_Group | tuple[int, _Table]] = list(part_groups)
            for table in sorted(part_tables, key=lambda t: t.bbox[1]):
                position = next(
                    (
                        index
                        for index, item in enumerate(groups)
                        if isinstance(item, _Group) and item.bbox[1] >= table.bbox[1]
                    ),
                    len(groups),
                )
                groups.insert(position, (number, table))
            items.extend(groups)

    # Text turned against its page, a reference up the margin, heads no section.
    heading_levels = _heading_levels(
        [item for item in items if isinstance(item, _Group) and item.turn == turns[item.page - 1]],
        body_size,
    )
    blocks: list[ParsedBlock] = []
    for item in items:
        if isinstance(item, tuple):
            number, table = item
            blocks.append(ParsedBlock("table", table.markdown, page=number, bbox=table.shown))
            continue
        text = item.text
        level = heading_levels.get(id(item))
        if level is not None:
            kind = "heading"
        elif _LIST_START.match(text):
            kind = "list_item"
        else:
            kind = "paragraph"
        blocks.append(ParsedBlock(kind, text, level=level, page=item.page, bbox=item.shown))

    return ParsedDocument(
        blocks=blocks,
        page_count=len(read),
        page_sizes=[page.size for page in read],
        title=parts[0].title,
        ocr_pages=[page.number for page in read if page.scanned],
    )


def part_to_json(part: PdfPart) -> dict:
    """The part as JSON (`part_from_json` reads it back), a line or a table as a list of its
    fields: a part of a few hundred pages holds tens of thousands of lines."""
    return {
        "page_count": part.page_count,
        "title": part.title,
        "pages": [
            {
                "number": page.number,
                "size": page.size,
                "turn": page.turn,
                "scanned": page.scanned,
                "lines": [_line_fields(line) for line in page.lines],
                "tables": [
                    [table.bbox, table.markdown, table.turn, table.frame] for table in page.tables
                ],
            }
            for page in part.pages
        ],
    }


def _line_fields(line: _Line) -> list:
    return [
        line.text,
        line.x0,
        line.top,
        line.x1,
        line.bottom,
        line.size,
        line.bold,
        line.turn,
        line.frame,
    ]


def part_from_json(data: dict) -> PdfPart:
    return PdfPart(
        page_count=data["page_count"],
        title=data["title"],
        pages=[
            _PageRead(
                number=page["number"],
                size=tuple(page["size"]),
                turn=page["turn"],
                lines=[
                    _Line(text, x0, top, x1, bottom, size, bold, turn, tuple(frame))
                    for text, x0, top, x1, bottom, size, bold, turn, frame in page["lines"]
                ],
                tables=[
                    _Table(tuple(bbox), markdown, turn, tuple(frame))
                    for bbox, markdown, turn, frame in page["tables"]
                ],
                scanned=page["scanned"],
            )
            for page in data["pages"]
        ],
    )


def _open(path: Path) -> pdfplumber.PDF:
    """Open the PDF, or refuse with `UnreadableFile` one whose encryption keeps it shut: a
    password to open it, or a protection pdfminer cannot undo.

    A PDF with only an owner password, the kind that restricts printing or copying, opens with
    the empty user password that any viewer tries, and is read like any other.
    """
    try:
        return pdfplumber.open(path, laparams={})
    except PdfminerException as error:
        cause = error.args[0] if error.args else None
        if isinstance(cause, PDFPasswordIncorrect):
            raise UnreadableFile(LOCKED) from error
        if isinstance(cause, PDFEncryptionError):
            raise UnreadableFile(UNKNOWN_PROTECTION) from error
        raise


def _metadata_title(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    title = clean_text(value)
    if not 4 <= len(title) <= 200:
        return None
    # Authoring tools write placeholders or the source file name here.
    if title.lower() in {"(anonymous)", "untitled"} or re.search(r"\.\w{2,5}$", title):
        return None
    return title


def _read(page: Page) -> tuple[list[_Line], list[_Table], int]:
    """The page's lines and tables, each line read in the frame where its text runs upright,
    and how far the page's own text is turned (`_turn`).

    pdfminer lays a page out as its /Rotate shows it, so text that runs down, up or upside down
    as shown (a landscape page drawn sideways on a portrait one, a page turned by a /Rotate that
    did not need one) comes out a letter a line. The page is read turned the way most of its
    text runs, tables included; text turned against that (a reference up the margin) is read in
    a frame of its own, after it.
    """
    turns = Counter(_turn(char) for char in page.chars if not char["text"].isspace())
    main = turns.most_common(1)[0][0] if turns else 0
    others = sorted(t for t, count in turns.items() if t != main and count >= MIN_TURNED_CHARS)
    lines: list[_Line] = []
    tables: list[_Table] = []
    for turn in [main, *others]:
        framed = _turned(page, turn)
        try:
            if turn == main:
                tables = _tables(framed, turn)
            lines.extend(line for line in _lines(framed, turn) if not _inside_any(line, tables))
        finally:
            if framed is not page:
                framed.close()
    return lines, tables, main


def _turn(char: dict) -> int:
    """How far the character runs turned clockwise from upright on the page as shown, to the
    nearest quarter: 90 reads down, 180 upside down, 270 up."""
    a, b = char["matrix"][:2]
    return round(-math.degrees(math.atan2(b, a)) / 90) % 4 * 90


def _turned(page: Page, turn: int) -> Page:
    """The page turned `turn` degrees anticlockwise from how it is shown, so that text that runs
    `turn` degrees clockwise reads upright in it."""
    if not turn:
        return page
    obj = copy.copy(page.page_obj)
    rotation = (page.rotation - turn) % 360
    # pdfplumber sizes the page by the attribute, pdfminer turns the content by the field.
    obj.attrs = {**obj.attrs, "Rotate": rotation}
    obj.rotate = rotation
    return Page(page.pdf, obj, page_number=page.page_number)


def _shown(box: BBox, turn: int, frame: tuple[float, float]) -> BBox:
    """Where a box read in a frame turned `turn` degrees anticlockwise from the page as shown,
    `frame` wide and high, sits on the page as shown: the frame turned back, clockwise."""
    x0, top, x1, bottom = box
    width, height = frame
    if turn == 90:
        return (height - bottom, x0, height - top, x1)
    if turn == 180:
        return (width - x1, height - bottom, width - x0, height - top)
    if turn == 270:
        return (top, width - x1, bottom, width - x0)
    return box


def _lines(page: Page, turn: int) -> list[_Line]:
    """The page's lines whose text reads upright in it, `page` being the one as shown turned
    `turn` degrees (`_turned`)."""
    chars = sorted(
        (c for c in page.chars if not c["text"].isspace()),
        key=lambda c: (c["top"] + c["bottom"]) / 2,
    )
    middles = [(c["top"] + c["bottom"]) / 2 for c in chars]
    frame = (float(page.width), float(page.height))
    lines = []
    for raw in page.textlinehorizontals:
        text = clean_text(_UNMAPPED_GLYPH.sub("", raw["text"]))
        if not text:
            continue
        sizes: Counter[float] = Counter()
        bold = 0
        upright = 0
        low = bisect_left(middles, raw["top"])
        high = bisect_right(middles, raw["bottom"])
        for char in chars[low:high]:
            if raw["x0"] - 1 <= (char["x0"] + char["x1"]) / 2 <= raw["x1"] + 1:
                sizes[round(char["size"] * 2) / 2] += 1
                bold += bool(_BOLD.search(char["fontname"]))
                upright += not _turn(char)
        total = sum(sizes.values())
        if upright * 2 < total:
            # Text that runs another way, a letter a line here: its own frame reads it.
            continue
        lines.append(
            _Line(
                text=text,
                x0=float(raw["x0"]),
                top=float(raw["top"]),
                x1=float(raw["x1"]),
                bottom=float(raw["bottom"]),
                size=sizes.most_common(1)[0][0] if sizes else 0.0,
                bold=total > 0 and bold / total > 0.8,
                turn=turn,
                frame=frame,
            )
        )
    return lines


def _tables(page: Page, turn: int) -> list[_Table]:
    frame = (float(page.width), float(page.height))
    tables = []
    for found in page.find_tables():
        rows = [row for row in found.extract() if any(cell and cell.strip() for cell in row)]
        if len(rows) < 2 or max(len(row) for row in rows) < 2:
            continue
        bbox = tuple(float(v) for v in found.bbox)
        tables.append(_Table(bbox, table_to_markdown(rows), turn, frame))
    return tables


def _inside_any(line: _Line, tables: list[_Table]) -> bool:
    x0, top, x1, bottom = line.shown
    x = (x0 + x1) / 2
    y = (top + bottom) / 2
    return any(
        left <= x <= right and upper <= y <= lower
        for left, upper, right, lower in (table.shown for table in tables)
    )


def _in_reading_order(
    groups: list[_Group], tables: list[_Table]
) -> list[tuple[list[_Group], list[_Table]]]:
    """The page's blocks and tables as the parts it is read in, in order: a column whole before
    the one beside it where the page sets text in columns, the page whole where it does not.

    pdfminer orders a page's lines by gathering them into boxes, which on a page of two columns
    can put the right column's first heading before the left column's text, and a table went
    before the first block below its top, which on such a page may be in the other column. A
    gutter is an x that no block or table crosses with a column of text on either side, of
    those that read the most of the page that way; what does cross it (a heading, a paragraph
    or a table across the page) splits the page into bands, read top to bottom, a band's
    columns left to right, each column by its own gutters. A part keeps pdfminer's order of its
    blocks, and its tables are placed among them as a page's were.
    """
    heights = sorted(line.height for group in groups for line in group.lines)
    gap = BAND_GAP * heights[len(heights) // 2] if heights else 0.0
    return list(_parts([*groups, *tables], gap))


def _parts(items: list[_Group | _Table], gap: float):
    gutter = _gutter(items, gap)
    if gutter is None:
        yield (
            [item for item in items if isinstance(item, _Group)],
            [item for item in items if isinstance(item, _Table)],
        )
        return
    for run, across in _bands(items, gutter, gap):
        left = [item for item in run if item.bbox[2] <= gutter]
        right = [item for item in run if item.bbox[0] >= gutter]
        if not across and _columns(left, right):
            yield from _parts(left, gap)
            yield from _parts(right, gap)
        else:
            yield from _parts(run, gap)


def _gutter(items: list[_Group | _Table], gap: float) -> float | None:
    """The x between columns that reads the most blocks and tables as columns, if any does.

    Each tried is just left of a block or table with another beside it on its left: where a
    column's lines start.
    """
    by_top = sorted(items, key=lambda item: item.bbox[1])
    starts = Counter(right.bbox[0] - 0.5 for _, right in _side_by_side(by_top))
    best, read = None, 0
    for x in sorted(x for x, _ in starts.most_common(MAX_GUTTERS)):
        columns = 0
        for run, across in _bands(items, x, gap, by_top):
            left = [item for item in run if item.bbox[2] <= x]
            right = [item for item in run if item.bbox[0] >= x]
            if not across and _columns(left, right):
                columns += len(run)
        if columns > read:
            best, read = x, columns
    return best


def _side_by_side(by_top: list[_Group | _Table]):
    """Pairs of a block or table and one to its right at the same height, the left one first."""
    for index, item in enumerate(by_top):
        for other in by_top[index + 1 :]:
            if other.bbox[1] >= item.bbox[3]:
                break
            left, right = (item, other) if item.bbox[0] <= other.bbox[0] else (other, item)
            if left.bbox[2] <= right.bbox[0]:
                yield left, right


def _bands(
    items: list[_Group | _Table],
    x: float,
    gap: float,
    by_top: list[_Group | _Table] | None = None,
) -> list[tuple[list[_Group | _Table], bool]]:
    """`items` top to bottom in runs of those that cross `x` and those that do not, a run also
    ending at whitespace across them `gap` high or more, each run in the order of `items`, with
    whether it crosses."""
    order = {id(item): index for index, item in enumerate(items)}
    runs: list[tuple[list[_Group | _Table], bool]] = []
    lowest = None
    for item in by_top or sorted(items, key=lambda item: item.bbox[1]):
        across = item.bbox[0] < x < item.bbox[2]
        apart = lowest is not None and item.bbox[1] - lowest >= gap
        if runs and runs[-1][1] == across and not apart:
            runs[-1][0].append(item)
        else:
            runs.append(([item], across))
        lowest = item.bbox[3] if lowest is None else max(lowest, item.bbox[3])
    return [(sorted(run, key=lambda item: order[id(item)]), across) for run, across in runs]


def _columns(left: list[_Group | _Table], right: list[_Group | _Table]) -> bool:
    return _column(left) and _column(right)


def _column(side: list[_Group | _Table]) -> bool:
    return any(
        isinstance(item, _Table) or any(len(line.text) >= MIN_COLUMN_CHARS for line in item.lines)
        for item in side
    )


def _in_margin(line: _Line) -> bool:
    height = line.frame[1]
    return line.bottom < height * MARGIN_BAND or line.top > height * (1 - MARGIN_BAND)


def _margin_key(text: str) -> str:
    return _DIGITS.sub("#", text.lower())


def _repeated_margin_lines(pages) -> set[str]:
    """Margin lines that recur across pages: running headers and footers."""
    if len(pages) < 2:
        return set()
    counts: Counter[str] = Counter()
    for lines, _ in pages:
        counts.update({_margin_key(line.text) for line in lines if _in_margin(line)})
    threshold = max(2, len(pages) * 0.4)
    return {key for key, count in counts.items() if count >= threshold}


def _is_margin_noise(line: _Line, repeated: set[str]) -> bool:
    if not _in_margin(line):
        return False
    return bool(_PAGE_NUMBER.match(line.text)) or _margin_key(line.text) in repeated


def _body_size(pages) -> float:
    sizes: Counter[float] = Counter()
    for lines, _ in pages:
        for line in lines:
            sizes[line.size] += len(line.text)
    return sizes.most_common(1)[0][0] if sizes else 0.0


def _group_lines(lines: list[_Line], page: int):
    group: _Group | None = None
    for line in lines:
        if group and _continues(group, line):
            group.lines.append(line)
            continue
        if group:
            yield group
        group = _Group(page, [line])
    if group:
        yield group


def _continues(group: _Group, line: _Line) -> bool:
    last = group.lines[-1]
    if line.turn != last.turn:
        return False
    if abs(line.size - last.size) > 0.6 or line.bold != last.bold:
        return False
    gap = line.top - last.bottom
    if gap < -last.height or gap > 0.65 * max(last.height, 1.0):
        return False
    if abs(line.x0 - group.lines[0].x0) > 40:
        return False
    return not _LIST_START.match(line.text)


def _heading_levels(groups: list[_Group], body_size: float) -> dict[int, int]:
    """Map heading groups (by id) to a level: larger and bolder styles rank higher."""
    headings = [group for group in groups if _is_heading(group, body_size)]
    styles = sorted({(g.size, g.bold) for g in headings}, reverse=True)
    rank = {style: min(index + 1, 6) for index, style in enumerate(styles)}
    return {id(group): rank[(group.size, group.bold)] for group in headings}


def _is_heading(group: _Group, body_size: float) -> bool:
    text = group.text
    if not body_size or len(group.lines) > 3 or len(text) > MAX_HEADING_CHARS:
        return False
    if not any(char.isalpha() for char in text):
        return False
    if group.size >= body_size * HEADING_SIZE_RATIO:
        return True
    # Body-sized but set apart in bold: a run-in heading on its own line.
    return (
        group.bold and group.size >= body_size * 0.95 and len(text) <= 120 and text[-1] not in ".;,"
    )
