"""PDF parsing with pdfplumber: lines grouped into blocks, headings found by font."""

import re
import threading
from bisect import bisect_left, bisect_right
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber
import pypdfium2 as pdfium

from app.parsing.types import (
    BBox,
    ParsedBlock,
    ParsedDocument,
    clean_text,
    table_to_markdown,
)

# Share of the page height, at top and bottom, where running headers and footers sit.
MARGIN_BAND = 0.08
HEADING_SIZE_RATIO = 1.15
MAX_HEADING_CHARS = 200

_BOLD = re.compile(r"bold|black|heavy|semibold|demi", re.IGNORECASE)
_LIST_START = re.compile(
    r"^(?:[•◦▪■●○‣·–—-]|\(?\d{1,3}[.)]|\(?[a-zA-Z][.)]|\(?[ivxIVX]{2,5}[.)])\s+\S"
)
_PAGE_NUMBER = re.compile(r"^(?:page\s+)?[-–—\s]*\d+[-–—\s]*(?:(?:of|/)\s*\d+)?$", re.IGNORECASE)
_DIGITS = re.compile(r"\d+")
# pdfminer writes glyphs it cannot map to Unicode (often bullets) as "(cid:N)".
_UNMAPPED_GLYPH = re.compile(r"\(cid:\d+\)")

# pdfium is not thread-safe and the workers render pages concurrently.
_pdfium_lock = threading.Lock()


@dataclass
class _Line:
    text: str
    x0: float
    top: float
    x1: float
    bottom: float
    size: float
    bold: bool

    @property
    def height(self) -> float:
        return self.bottom - self.top


@dataclass
class _Table:
    bbox: BBox
    markdown: str


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
    def bbox(self) -> BBox:
        return (
            min(line.x0 for line in self.lines),
            min(line.top for line in self.lines),
            max(line.x1 for line in self.lines),
            max(line.bottom for line in self.lines),
        )

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


def parse_pdf(path: Path) -> ParsedDocument:
    pages: list[tuple[list[_Line], list[_Table]]] = []
    page_sizes: list[tuple[float, float]] = []
    ocr_pages: list[int] = []

    with pdfplumber.open(path, laparams={}) as pdf:
        title = _metadata_title(pdf.metadata.get("Title"))
        for number, page in enumerate(pdf.pages, start=1):
            page_sizes.append((float(page.width), float(page.height)))
            tables = _tables(page)
            lines = [line for line in _lines(page) if not _inside_any(line, tables)]
            if not lines and not tables and page.images:
                ocr_pages.append(number)
            pages.append((lines, tables))
            page.close()

    repeated = _repeated_margin_lines(pages, page_sizes)
    body_size = _body_size(pages)

    items: list[_Group | tuple[int, _Table]] = []
    for number, (lines, tables) in enumerate(pages, start=1):
        height = page_sizes[number - 1][1]
        kept = [line for line in lines if not _is_margin_noise(line, height, repeated)]
        groups: list[_Group | tuple[int, _Table]] = list(_group_lines(kept, number))
        for table in sorted(tables, key=lambda t: t.bbox[1]):
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

    heading_levels = _heading_levels(
        [item for item in items if isinstance(item, _Group)], body_size
    )
    blocks: list[ParsedBlock] = []
    for item in items:
        if isinstance(item, tuple):
            number, table = item
            blocks.append(ParsedBlock("table", table.markdown, page=number, bbox=table.bbox))
            continue
        text = item.text
        level = heading_levels.get(id(item))
        if level is not None:
            kind = "heading"
        elif _LIST_START.match(text):
            kind = "list_item"
        else:
            kind = "paragraph"
        blocks.append(ParsedBlock(kind, text, level=level, page=item.page, bbox=item.bbox))

    return ParsedDocument(
        blocks=blocks,
        page_count=len(page_sizes),
        page_sizes=page_sizes,
        title=title,
        ocr_pages=ocr_pages,
    )


def render_page(path: Path, page_number: int, scale: float = 2.0) -> bytes:
    """Render one page (1-based) to PNG bytes."""
    import io

    with _pdfium_lock:
        pdf = pdfium.PdfDocument(str(path))
        try:
            image = pdf[page_number - 1].render(scale=scale).to_pil()
        finally:
            pdf.close()
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


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


def _lines(page) -> list[_Line]:
    chars = sorted(
        (c for c in page.chars if not c["text"].isspace()),
        key=lambda c: (c["top"] + c["bottom"]) / 2,
    )
    middles = [(c["top"] + c["bottom"]) / 2 for c in chars]
    lines = []
    for raw in page.textlinehorizontals:
        text = clean_text(_UNMAPPED_GLYPH.sub("", raw["text"]))
        if not text:
            continue
        sizes: Counter[float] = Counter()
        bold = 0
        low = bisect_left(middles, raw["top"])
        high = bisect_right(middles, raw["bottom"])
        for char in chars[low:high]:
            if raw["x0"] - 1 <= (char["x0"] + char["x1"]) / 2 <= raw["x1"] + 1:
                sizes[round(char["size"] * 2) / 2] += 1
                bold += bool(_BOLD.search(char["fontname"]))
        total = sum(sizes.values())
        lines.append(
            _Line(
                text=text,
                x0=float(raw["x0"]),
                top=float(raw["top"]),
                x1=float(raw["x1"]),
                bottom=float(raw["bottom"]),
                size=sizes.most_common(1)[0][0] if sizes else 0.0,
                bold=total > 0 and bold / total > 0.8,
            )
        )
    return lines


def _tables(page) -> list[_Table]:
    tables = []
    for found in page.find_tables():
        rows = [row for row in found.extract() if any(cell and cell.strip() for cell in row)]
        if len(rows) < 2 or max(len(row) for row in rows) < 2:
            continue
        tables.append(_Table(tuple(float(v) for v in found.bbox), table_to_markdown(rows)))
    return tables


def _inside_any(line: _Line, tables: list[_Table]) -> bool:
    x = (line.x0 + line.x1) / 2
    y = (line.top + line.bottom) / 2
    return any(t.bbox[0] <= x <= t.bbox[2] and t.bbox[1] <= y <= t.bbox[3] for t in tables)


def _in_margin(line: _Line, page_height: float) -> bool:
    return line.bottom < page_height * MARGIN_BAND or line.top > page_height * (1 - MARGIN_BAND)


def _margin_key(text: str) -> str:
    return _DIGITS.sub("#", text.lower())


def _repeated_margin_lines(pages, page_sizes) -> set[str]:
    """Margin lines that recur across pages: running headers and footers."""
    if len(pages) < 2:
        return set()
    counts: Counter[str] = Counter()
    for (lines, _), (_, height) in zip(pages, page_sizes, strict=True):
        counts.update({_margin_key(line.text) for line in lines if _in_margin(line, height)})
    threshold = max(2, len(pages) * 0.4)
    return {key for key, count in counts.items() if count >= threshold}


def _is_margin_noise(line: _Line, page_height: float, repeated: set[str]) -> bool:
    if not _in_margin(line, page_height):
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
