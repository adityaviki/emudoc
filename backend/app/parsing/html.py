"""HTML parsing with the standard library's parser: a saved web page or a document exported as
HTML, its headings, paragraphs, list items and tables kept as blocks, its title as the
document's, and what is no part of its text left out (scripts, styles, navigation, forms)."""

import codecs
import re
from html.parser import HTMLParser
from pathlib import Path

from app.parsing.types import ParsedBlock, ParsedDocument, clean_text, table_to_markdown

# A page's encoding, where a byte-order mark does not say it: a `<meta charset>` or an
# http-equiv content type within the first bytes, where browsers look for it.
DECLARED = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9_.:-]+)""", re.IGNORECASE)
PRESCAN = 4096
MARKS = (
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)
# What a page that declares nothing is read as where it is not UTF-8: the encoding the HTML
# standard has browsers fall back to.
FALLBACK = "windows-1252"

HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
# Elements whose text is no part of the document: code, styling, and the controls and
# navigation a page puts round its content.
HIDDEN = {
    "script",
    "style",
    "noscript",
    "template",
    "svg",
    "math",
    "nav",
    "button",
    "select",
    "textarea",
    "iframe",
    "object",
}
# Elements whose start and end close the text before them as a block of its own.
BLOCKS = {
    "address",
    "article",
    "aside",
    "blockquote",
    "body",
    "dd",
    "details",
    "div",
    "dl",
    "dt",
    "fieldset",
    "figcaption",
    "figure",
    "footer",
    "form",
    "header",
    "hr",
    "html",
    "main",
    "ol",
    "p",
    "section",
    "summary",
    "ul",
}
# Elements that never hold anything, and so are never closed.
VOID = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "source",
    "track",
    "wbr",
}


def parse_html(path: Path) -> ParsedDocument:
    reader = _Reader()
    reader.feed(decode_html(path.read_bytes()))
    reader.close()
    return ParsedDocument(blocks=reader.blocks, title=reader.title)


def decode_html(data: bytes) -> str:
    """The page's text: in the encoding its byte-order mark names; else as UTF-8 where its bytes
    are that, whatever a `<meta>` says, since a page saved again as UTF-8 often still names
    what it was written in; else in the encoding the `<meta>` names, or the standard's
    fallback."""
    for mark, encoding in MARKS:
        if data.startswith(mark):
            return data.decode(encoding, errors="replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    encoding = FALLBACK
    if declared := DECLARED.search(data[:PRESCAN]):
        try:
            named = codecs.lookup(declared.group(1).decode("ascii")).name
        except LookupError:
            named = FALLBACK
        # A page whose `<meta>` could be read as ASCII is in no UTF-16 or UTF-32.
        if not named.startswith(("utf-16", "utf-32")):
            encoding = named
    return data.decode(encoding, errors="replace")


class _Reader(HTMLParser):
    """The page's blocks as the parser meets its tags: text gathers until a block's start or end
    closes it, a heading's or a list item's taking that kind, and a table's cells gather into
    rows, a table nested in a cell joining its text to the cell."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[ParsedBlock] = []
        self.title: str | None = None
        self._text: list[str] = []
        self._hidden: list[str] = []
        self._in_title = False
        self._heading: int | None = None
        self._items = 0
        self._pre = 0
        self._tables: list[list[list[str]]] = []
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._hidden or tag in HIDDEN or any(name == "hidden" for name, _ in attrs):
            if tag not in VOID:
                self._hidden.append(tag)
            return
        if tag == "title":
            self._in_title = True
        elif self._tables:
            self._table_start(tag)
        elif tag == "table":
            self._close()
            self._tables.append([])
        elif tag in HEADINGS:
            self._close()
            self._heading = HEADINGS[tag]
        elif tag == "li":
            self._close()
            self._items += 1
        elif tag == "pre":
            self._close()
            self._pre += 1
        elif tag == "br":
            self._text.append("\n")
        elif tag in BLOCKS:
            self._close()

    def handle_endtag(self, tag: str) -> None:
        if self._hidden:
            # An end tag for what was not opened is ignored, as a browser ignores it.
            if tag in self._hidden:
                del self._hidden[self._hidden.index(tag) :]
            return
        if tag == "title":
            self._in_title = False
        elif self._tables:
            self._table_end(tag)
        elif tag in HEADINGS:
            self._close()
            self._heading = None
        elif tag == "li":
            self._close()
            self._items = max(0, self._items - 1)
        elif tag == "pre":
            self._close()
            self._pre = max(0, self._pre - 1)
        elif tag in BLOCKS:
            self._close()

    def handle_data(self, data: str) -> None:
        if self._hidden:
            return
        if self._in_title:
            self.title = clean_text(f"{self.title or ''} {data}") or None
        elif self._cell is not None:
            self._cell.append(data)
        elif not self._tables:
            self._text.append(data)

    def close(self) -> None:
        super().close()
        while self._tables:
            self._table_end("table")
        self._close()

    def _close(self) -> None:
        """End the block whose text has gathered, as the kind the open tags make it."""
        raw = "".join(self._text)
        self._text.clear()
        if self._pre:
            text = "\n".join(line.rstrip() for line in raw.strip("\n").splitlines())
            text = text.replace("\x00", "")
        else:
            text = clean_text(" ".join(raw.split()))
        if not text:
            return
        if self._heading is not None:
            self.blocks.append(ParsedBlock("heading", text, level=self._heading))
        elif self._items:
            self.blocks.append(ParsedBlock("list_item", text))
        else:
            self.blocks.append(ParsedBlock("paragraph", text))

    def _table_start(self, tag: str) -> None:
        rows = self._tables[-1]
        if tag == "table":
            self._tables.append([])
        elif tag == "tr":
            rows.append([])
        elif tag in ("td", "th"):
            if not rows:
                rows.append([])
            self._end_cell()
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def _table_end(self, tag: str) -> None:
        if tag in ("td", "th"):
            self._end_cell()
        elif tag == "table":
            self._end_cell()
            rows = [row for row in self._tables.pop() if any(row)]
            if self._tables:
                # A table inside a cell: its text joins the cell's.
                words = " ".join(cell for row in rows for cell in row)
                self._cell = [*(self._cell or []), " ", words]
            elif rows:
                self.blocks.append(ParsedBlock("table", table_to_markdown(rows)))

    def _end_cell(self) -> None:
        if self._cell is not None and self._tables and self._tables[-1]:
            self._tables[-1][-1].append(clean_text(" ".join("".join(self._cell).split())))
        self._cell = None
