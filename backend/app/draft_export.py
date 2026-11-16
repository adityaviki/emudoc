"""What the agent writes in Markdown as a file: the Word document it writes into a member's My
files (`create_document` in app/agent/writing.py), and the layout an answer or a chat downloaded
as Word, a PDF or Markdown is made with (app/chat_export.py). It was a draft's version saved as a
file until the drafts became files of My files on 2026-11-13, which keeps the module's name.

The text cites passages as [P<id>], which mean nothing outside the app: each file numbers its
citations in the order the text first makes them, as the page does (CitedText.tsx), and lists
them at its end as notes naming the document, section and pages of each passage as the agent read
it (`source_label`, as `sourceLabel` in api.ts). A reference to a passage it was not shown stays
as it is written, and so does a placeholder such as [Your name], which is the member's to fill
in.

The Markdown is the version's own text, its citations as footnotes ([^1]) and whatever could
start HTML escaped, so that no viewer of the file runs what a document steered the model into
writing. The Word document and the PDF are laid out from the text as the page's reader reads it
(CommonMark with tables and strikethrough, by markdown-it, any HTML read as text): headings,
paragraphs, lists, quotes, code, tables, bold, italic and struck text, a link as its text and
address rather than one to follow, and an image as its description, since the agent writes none.
A line break is kept where it is written: a letter's address and its closing are written a line
each.
The PDF is set in Noto Sans, which the app carries (app/fonts, under the SIL Open Font License),
for the Latin, Greek and Cyrillic alphabets: a text in another script is better saved as a Word
document, which takes the reader's own fonts.

What the agent writes is what documents say: a file is made in memory, and kept only as the
member's own in My files.
"""

import datetime as dt
import io
import re
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal
from xml.sax.saxutils import escape

import docx
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt
from docx.text.paragraph import Paragraph as WordParagraph
from markdown_it import MarkdownIt
from markdown_it.token import Token
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Flowable,
    HRFlowable,
    ListFlowable,
    ListItem,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.doctemplate import LayoutError

# A passage the agent cites, as it writes the reference in an answer or a file.
CITATION = re.compile(r"\[P(\d+)\]")

ExportFormat = Literal["md", "docx", "pdf"]
MEDIA_TYPES: dict[str, str] = {
    "md": "text/markdown; charset=utf-8",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
}

NOT_LAID_OUT = (
    "This text could not be laid out as a PDF: something in it is too large for a page. "
    "Download it as a Word document instead."
)

# What XML cannot hold, which a Word document refuses and a PDF's text would garble: the control
# characters but the tab and the line breaks.
_UNWRITABLE = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff\ud800-\udfff]")
# What could start HTML in Markdown: a tag, a closing tag, a comment or a declaration, but not
# an address written as a link (<https://…>).
_HTML_START = re.compile(r"<(?![A-Za-z][A-Za-z0-9+.-]{1,31}:[^\s<>]*>)(?=[A-Za-z/!?])")
# What a footnote's text escapes, as Markdown would read it otherwise.
_MARKDOWN_SPECIAL = re.compile(r"([\\`*_\[\]<>])")
# What a file's name cannot hold on one system or another.
_UNNAMEABLE = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')

READER = MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"])


class ExportRefused(Exception):
    """A version that cannot be made into the file asked for, with the sentence that says why."""


@dataclass(frozen=True)
class Note:
    """A passage a version cites, by its number in the file and what it is."""

    number: int
    label: str


@dataclass(frozen=True)
class Span:
    """A piece of a line's text in one style. A note's number is a span set as a superscript,
    as is the comma between two of them; a hard line break is a span of "\\n"."""

    text: str
    bold: bool = False
    italic: bool = False
    struck: bool = False
    code: bool = False
    sup: bool = False


@dataclass(frozen=True)
class Heading:
    level: int
    spans: list[Span]


@dataclass(frozen=True)
class Para:
    spans: list[Span]


@dataclass(frozen=True)
class Items:
    """A list, each item its own blocks."""

    ordered: bool
    start: int
    items: list[list["Block"]]


@dataclass(frozen=True)
class Quote:
    blocks: list["Block"]


@dataclass(frozen=True)
class Code:
    text: str


@dataclass(frozen=True)
class Rule:
    pass


@dataclass(frozen=True)
class Grid:
    """A table: each column's alignment ("left", "right" or "center"), its header's cells and
    its rows' cells, every row as wide as the header."""

    aligns: list[str]
    head: list[list[Span]]
    rows: list[list[list[Span]]]


Block = Heading | Para | Items | Quote | Code | Rule | Grid


def draft_file(
    written_as: ExportFormat,
    text: str,
    sources: Sequence[Mapping[str, Any]],
    title: str,
    written_at: dt.datetime,
) -> bytes:
    """The text as the file asked for, its citations numbered notes naming the passages of
    `sources` they cite. Raises ExportRefused for a PDF that cannot be laid out."""
    text = writable(text)
    notes = notes_of(text, sources)
    if written_as == "md":
        return markdown_file(text, notes).encode()
    blocks = blocks_of(text, notes)
    if written_as == "docx":
        return docx_file(blocks, notes, title, written_at)
    return pdf_file(blocks, notes, title)


def file_stem(title: str, otherwise: str) -> str:
    """A name a file can have on any system, from a title; `otherwise` for a title of nothing
    a file's name can hold."""
    return " ".join(_UNNAMEABLE.sub(" ", title).split()) or otherwise


def writable(text: str) -> str:
    """The text without what XML cannot hold."""
    return _UNWRITABLE.sub("", text)


def notes_of(text: str, sources: Sequence[Mapping[str, Any]]) -> dict[int, Note]:
    """The passages the text cites that the version keeps, by id, each numbered in the order the
    text first cites it."""
    known = {source["id"]: source for source in sources}
    cited = [
        passage_id
        for passage_id in dict.fromkeys(int(match) for match in CITATION.findall(text))
        if passage_id in known
    ]
    return {
        passage_id: Note(number, source_label(known[passage_id]))
        for number, passage_id in enumerate(cited, 1)
    }


def source_label(source: Mapping[str, Any]) -> str:
    """A cited passage in a line, as the page words it: its document, its section without the
    document's name it starts with, and its pages."""
    title = source.get("document_title") or ""
    context = source.get("context") or ""
    prefix = f"{title} › "
    section = context[len(prefix) :] if context.startswith(prefix) else context
    if section == title:
        section = ""
    start, end = source.get("page_start"), source.get("page_end")
    pages = (
        ""
        if start is None
        else f"p. {start}"
        if end is None or end == start
        else f"pp. {start}–{end}"
    )
    return " · ".join(part for part in (title, section, pages) if part)


def markdown_file(text: str, notes: Mapping[int, Note]) -> str:
    """The version's Markdown, each known citation a footnote of its note, defined at the end."""
    body = markdown_body(text, notes)
    return f"{body}\n\n{footnotes(notes)}\n" if notes else body + "\n"


def markdown_body(text: str, notes: Mapping[int, Note]) -> str:
    """The Markdown, each citation of `notes` a footnote's reference and whatever could start
    HTML escaped."""

    def cited(match: re.Match[str]) -> str:
        note = notes.get(int(match.group(2)))
        return match.group(0) if note is None else f"[^{note.number}]"

    return re.sub(r"( ?)\[P(\d+)\]", cited, _HTML_START.sub(r"\\<", text)).rstrip()


def footnotes(notes: Mapping[int, Note]) -> str:
    """The footnotes' definitions, a line each."""
    return "\n".join(
        f"[^{note.number}]: " + _MARKDOWN_SPECIAL.sub(r"\\\1", note.label)
        for note in notes.values()
    )


def blocks_of(text: str, notes: Mapping[int, Note]) -> list[Block]:
    """The text as the page's reader reads it, its known citations as their notes' numbers."""
    blocks, _ = _blocks(READER.parse(text), 0, notes, None)
    return blocks


def _blocks(
    tokens: list[Token], at: int, notes: Mapping[int, Note], until: str | None
) -> tuple[list[Block], int]:
    """The blocks from `at` to the token that closes them (`until`), and where reading goes on."""
    blocks: list[Block] = []
    while at < len(tokens):
        token = tokens[at]
        if token.type == until:
            return blocks, at + 1
        if token.type == "heading_open":
            blocks.append(Heading(int(token.tag[1:]), _spans(tokens[at + 1], notes)))
            at += 3
        elif token.type == "paragraph_open":
            blocks.append(Para(_spans(tokens[at + 1], notes)))
            at += 3
        elif token.type in ("bullet_list_open", "ordered_list_open"):
            items: list[list[Block]] = []
            at += 1
            while tokens[at].type == "list_item_open":
                item, at = _blocks(tokens, at + 1, notes, "list_item_close")
                items.append(item)
            ordered = token.type == "ordered_list_open"
            blocks.append(Items(ordered, int(token.attrGet("start") or 1), items))
            at += 1
        elif token.type == "blockquote_open":
            quoted, at = _blocks(tokens, at + 1, notes, "blockquote_close")
            blocks.append(Quote(quoted))
        elif token.type in ("fence", "code_block"):
            blocks.append(Code(token.content.rstrip("\n")))
            at += 1
        elif token.type == "hr":
            blocks.append(Rule())
            at += 1
        elif token.type == "table_open":
            grid, at = _grid(tokens, at + 1, notes)
            blocks.append(grid)
        else:
            at += 1
    return blocks, at


def _grid(tokens: list[Token], at: int, notes: Mapping[int, Note]) -> tuple[Grid, int]:
    aligns: list[str] = []
    head: list[list[Span]] = []
    rows: list[list[list[Span]]] = []
    row: list[list[Span]] = []
    while tokens[at].type != "table_close":
        token = tokens[at]
        if token.type == "th_open":
            style = str(token.attrGet("style") or "")
            align = style.removeprefix("text-align:")
            aligns.append(align if align in ("right", "center") else "left")
            head.append(_spans(tokens[at + 1], notes))
            at += 2
        elif token.type == "td_open":
            row.append(_spans(tokens[at + 1], notes))
            at += 2
        elif token.type == "tr_close" and row:
            rows.append((row + [[]] * len(head))[: len(head)])
            row = []
        at += 1
    return Grid(aligns, head, rows), at + 1


def line_spans(text: str) -> list[Span]:
    """One line of Markdown in its styles (bold, italic, struck, code; a link as its text and
    address), as a paragraph the agent writes into a Word document is set (app/word_files.py)."""
    (inline,) = READER.parseInline(writable(text)) or [Token("inline", "", 0)]
    return _spans(inline, {})


def _spans(inline: Token, notes: Mapping[int, Note]) -> list[Span]:
    """A line's text in its styles, each known citation its note's number."""
    spans: list[Span] = []
    style = Span("")
    link: str | None = None
    linked = ""
    for child in inline.children or []:
        kind = child.type
        if kind in ("text", "image"):
            spans += _cited(child.content, notes, style)
            linked += child.content
        elif kind == "code_inline":
            spans.append(replace(style, text=child.content, code=True))
            linked += child.content
        elif kind in ("softbreak", "hardbreak"):
            # A line break is kept where it is written, as a letter's address needs.
            spans.append(replace(style, text="\n"))
        elif kind in ("strong_open", "strong_close"):
            style = replace(style, bold=kind == "strong_open")
        elif kind in ("em_open", "em_close"):
            style = replace(style, italic=kind == "em_open")
        elif kind in ("s_open", "s_close"):
            style = replace(style, struck=kind == "s_open")
        elif kind == "link_open":
            link, linked = str(child.attrGet("href") or ""), ""
        elif kind == "link_close":
            # Its address after its text, to be read and copied, unless its text is the address.
            if link and link not in (linked, f"mailto:{linked}"):
                spans.append(replace(style, text=f" ({link})"))
            link = None
    return [span for span in spans if span.text]


def _cited(text: str, notes: Mapping[int, Note], style: Span) -> list[Span]:
    """The text in the style, each citation of a passage the version keeps its note's number
    set straight after the word it follows, and two numbers in a row parted by a comma."""
    spans: list[Span] = []
    at = 0
    for match in CITATION.finditer(text):
        note = notes.get(int(match.group(1)))
        if note is None:
            continue
        before = text[at : match.start()]
        if spans and spans[-1].sup and not before.strip():
            spans.append(replace(style, text=",", sup=True))
        else:
            spans.append(replace(style, text=before.rstrip(" ")))
        spans.append(replace(style, text=str(note.number), sup=True))
        at = match.end()
    spans.append(replace(style, text=text[at:]))
    return spans


# The Word document.

# How far a list's level is set in, and where its marker hangs.
_INDENT = Cm(0.63)
_MONOSPACE = "Courier New"


def docx_file(
    blocks: list[Block], notes: Mapping[int, Note], title: str, written_at: dt.datetime
) -> bytes:
    """The blocks as a Word document in its default styles, then its notes."""
    document = docx.Document()
    properties = document.core_properties
    # The template's own, which name python-docx.
    properties.author = properties.last_modified_by = properties.comments = ""
    properties.title = title
    properties.revision = 1
    properties.created = properties.modified = written_at.astimezone(dt.UTC).replace(tzinfo=None)
    for block in blocks:
        _word(document, block, 0, False)
    if notes:
        heading = document.add_paragraph()
        heading.paragraph_format.space_before = Pt(18)
        _run(heading, Span("Sources", bold=True), Pt(9))
        for note in notes.values():
            line = document.add_paragraph()
            line.paragraph_format.left_indent = _INDENT
            line.paragraph_format.first_line_indent = -_INDENT
            line.paragraph_format.space_after = Pt(2)
            _run(line, Span(f"{note.number}.\t{note.label}"), Pt(9))
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


def _word(document: Any, block: Block, depth: int, quoted: bool) -> None:
    if isinstance(block, Heading):
        _runs(document.add_heading(level=min(block.level, 6)), block.spans)
    elif isinstance(block, Para):
        _runs(_paragraph(document, depth, quoted), block.spans)
    elif isinstance(block, Items):
        for number, item in enumerate(block.items, block.start):
            first, rest = (
                (item[0], item[1:]) if item and isinstance(item[0], Para) else (None, item)
            )
            if block.ordered:
                # Numbered by hand: Word's numbered style would go on counting from the list before.
                marker = _paragraph(document, depth + 1, quoted)
                marker.paragraph_format.first_line_indent = -_INDENT
                marker.add_run(f"{number}.\t")
            else:
                marker = document.add_paragraph(
                    style="List Bullet" if depth == 0 else f"List Bullet {min(depth + 1, 3)}"
                )
            _runs(marker, first.spans if first else [])
            for inner in rest:
                _word(document, inner, depth + 1, quoted)
    elif isinstance(block, Quote):
        for inner in block.blocks:
            _word(document, inner, depth, True)
    elif isinstance(block, Code):
        _run(_paragraph(document, depth, quoted), Span(block.text, code=True), Pt(9.5))
    elif isinstance(block, Rule):
        # A line under an empty paragraph, as Word draws a rule.
        border = OxmlElement("w:pBdr")
        bottom = OxmlElement("w:bottom")
        for name, value in (("val", "single"), ("sz", "6"), ("space", "1"), ("color", "999999")):
            bottom.set(qn(f"w:{name}"), value)
        border.append(bottom)
        _paragraph(document, depth, quoted)._p.get_or_add_pPr().append(border)
    elif isinstance(block, Grid):
        table = document.add_table(rows=0, cols=len(block.head))
        table.style = "Table Grid"
        for number, row in enumerate([block.head, *block.rows]):
            for cell, spans, align in zip(table.add_row().cells, row, block.aligns, strict=True):
                paragraph = cell.paragraphs[0]
                paragraph.alignment = _WORD_ALIGNS[align]
                _runs(
                    paragraph, [replace(span, bold=True) for span in spans] if not number else spans
                )
        # Room between the table and what follows it.
        document.add_paragraph()


_WORD_ALIGNS = {
    "left": WD_ALIGN_PARAGRAPH.LEFT,
    "right": WD_ALIGN_PARAGRAPH.RIGHT,
    "center": WD_ALIGN_PARAGRAPH.CENTER,
}


def _paragraph(document: Any, depth: int, quoted: bool) -> WordParagraph:
    paragraph = document.add_paragraph(style="Quote" if quoted else None)
    if depth:
        paragraph.paragraph_format.left_indent = _INDENT * depth
    return paragraph


def _runs(paragraph: WordParagraph, spans: list[Span]) -> None:
    for span in spans:
        _run(paragraph, span)


def _run(paragraph: WordParagraph, span: Span, size: Pt | None = None) -> None:
    for number, line in enumerate(span.text.split("\n")):
        if number:
            paragraph.add_run().add_break()
        if not line:
            continue
        run = paragraph.add_run(line)
        run.bold = span.bold or None
        run.italic = span.italic or None
        run.font.strike = span.struck or None
        run.font.superscript = span.sup or None
        if span.code:
            run.font.name = _MONOSPACE
        if size is not None:
            run.font.size = size


# The PDF.

FONT = "NotoSans"
_FONTS = Path(__file__).parent / "fonts"
# reportlab keeps registered fonts and their subsets in globals of its own: one PDF is laid out at
# a time, a few hundredths of a second for a letter.
_PDF = threading.Lock()

_INK = colors.HexColor("#14161C")
_MUTED = colors.HexColor("#5B5F6B")
_LINE = colors.HexColor("#C9C8C2")
_SUNKEN = colors.HexColor("#EDECE7")
_MARGIN = 2.2 * cm
_PDF_ALIGNS = {"left": TA_LEFT, "right": TA_RIGHT, "center": TA_CENTER}
_LEADING = re.compile(r"^ +")


@dataclass(frozen=True)
class _Styles:
    body: ParagraphStyle
    item: ParagraphStyle
    quote: ParagraphStyle
    code: ParagraphStyle
    note: ParagraphStyle
    headings: dict[int, ParagraphStyle]
    cells: dict[str, ParagraphStyle]


def _styles() -> _Styles:
    body = ParagraphStyle(
        "body",
        fontName=FONT,
        bulletFontName=FONT,
        fontSize=10.5,
        leading=15,
        spaceAfter=7,
        textColor=_INK,
    )

    def heading(level: int, size: float, leading: float) -> ParagraphStyle:
        return ParagraphStyle(
            f"h{level}",
            parent=body,
            fontName=f"{FONT}-Bold",
            fontSize=size,
            leading=leading,
            spaceBefore=8,
            spaceAfter=6,
            keepWithNext=True,
        )

    cell = ParagraphStyle("cell", parent=body, fontSize=9.5, leading=13, spaceAfter=0)
    return _Styles(
        body=body,
        item=ParagraphStyle("item", parent=body, spaceAfter=3),
        quote=ParagraphStyle("quote", parent=body, leftIndent=14, textColor=_MUTED),
        code=ParagraphStyle(
            "code", parent=body, fontSize=9.5, leading=13, backColor=_SUNKEN, borderPadding=4
        ),
        note=ParagraphStyle(
            "note", parent=body, fontSize=8.5, leading=11.5, spaceAfter=2, leftIndent=14
        ),
        headings={
            1: heading(1, 16, 21),
            2: heading(2, 13.5, 18),
            3: heading(3, 12, 16),
            4: heading(4, 10.5, 15),
        },
        cells={
            align: ParagraphStyle(f"cell-{align}", parent=cell, alignment=value)
            for align, value in _PDF_ALIGNS.items()
        },
    )


def pdf_file(blocks: list[Block], notes: Mapping[int, Note], title: str) -> bytes:
    """The blocks as an A4 PDF set in Noto Sans, then its notes, each page numbered."""
    with _PDF:
        _register_fonts()
        styles = _styles()
        out = io.BytesIO()
        page = SimpleDocTemplate(
            out,
            pagesize=A4,
            leftMargin=_MARGIN,
            rightMargin=_MARGIN,
            topMargin=2 * cm,
            bottomMargin=2 * cm,
            title=title,
            author="",
            subject="",
            creator="Emudoc",
        )
        story = [flowable for block in blocks for flowable in _flowables(block, styles, page.width)]
        if notes:
            story += [
                Spacer(1, 10),
                HRFlowable(width="30%", thickness=0.5, color=_LINE, hAlign="LEFT", spaceAfter=4),
                Paragraph("<b>Sources</b>", _restyled(styles.note, leftIndent=0)),
                *(
                    Paragraph(escape(note.label), styles.note, bulletText=f"{note.number}.")
                    for note in notes.values()
                ),
            ]
        try:
            page.build(story, onFirstPage=_numbered, onLaterPages=_numbered)
        except LayoutError:
            raise ExportRefused(NOT_LAID_OUT) from None
        return out.getvalue()


def _restyled(style: ParagraphStyle, **changes: Any) -> ParagraphStyle:
    return ParagraphStyle(f"{style.name}-", parent=style, **changes)


def _register_fonts() -> None:
    if FONT in pdfmetrics.getRegisteredFontNames():
        return
    for name, file in (
        (FONT, "NotoSans-Regular.ttf"),
        (f"{FONT}-Bold", "NotoSans-Bold.ttf"),
        (f"{FONT}-Italic", "NotoSans-Italic.ttf"),
        (f"{FONT}-BoldItalic", "NotoSans-BoldItalic.ttf"),
    ):
        pdfmetrics.registerFont(TTFont(name, _FONTS / file))
    pdfmetrics.registerFontFamily(
        FONT,
        normal=FONT,
        bold=f"{FONT}-Bold",
        italic=f"{FONT}-Italic",
        boldItalic=f"{FONT}-BoldItalic",
    )


def _numbered(canvas: Any, page: Any) -> None:
    canvas.saveState()
    canvas.setFont(FONT, 8)
    canvas.setFillColor(_MUTED)
    canvas.drawCentredString(A4[0] / 2, 1.2 * cm, str(page.page))
    canvas.restoreState()


def _flowables(block: Block, styles: _Styles, width: float, quoted: bool = False) -> list[Flowable]:
    if isinstance(block, Heading):
        return [Paragraph(_markup(block.spans), styles.headings[min(block.level, 4)])]
    if isinstance(block, Para):
        return [Paragraph(_markup(block.spans), styles.quote if quoted else styles.body)]
    if isinstance(block, Items):
        items = []
        for item in block.items:
            inner = [
                flowable
                for part in item
                for flowable in (
                    [Paragraph(_markup(part.spans), styles.item)]
                    if isinstance(part, Para)
                    else _flowables(part, styles, width - 18, quoted)
                )
            ]
            items.append(ListItem(inner or [Paragraph("", styles.item)]))
        return [
            ListFlowable(
                items,
                bulletType="1" if block.ordered else "bullet",
                start=block.start if block.ordered else "•",
                bulletFormat="%s." if block.ordered else None,
                leftIndent=18,
                bulletFontName=FONT,
                bulletFontSize=10,
                spaceAfter=6,
            )
        ]
    if isinstance(block, Quote):
        return [f for inner in block.blocks for f in _flowables(inner, styles, width, True)]
    if isinstance(block, Code):
        # Its indentation kept, which a paragraph would fold away.
        lines = (
            _LEADING.sub(lambda spaces: "&nbsp;" * len(spaces.group()), escape(line))
            for line in block.text.split("\n")
        )
        return [Paragraph("<br/>".join(lines), styles.code), Spacer(1, 4)]
    if isinstance(block, Rule):
        return [HRFlowable(width="100%", thickness=0.5, color=_LINE, spaceBefore=4, spaceAfter=10)]
    columns = len(block.head)
    rows = [
        [
            Paragraph(_markup(spans, bold=number == 0), styles.cells[align])
            for spans, align in zip(row, block.aligns, strict=True)
        ]
        for number, row in enumerate([block.head, *block.rows])
    ]
    table = Table(
        rows,
        colWidths=[width / columns] * columns,
        repeatRows=1,
        splitInRow=1,
        hAlign="LEFT",
        spaceAfter=10,
    )
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, _LINE),
                ("BACKGROUND", (0, 0), (-1, 0), _SUNKEN),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    return [table]


def _markup(spans: list[Span], bold: bool = False) -> str:
    """The spans as reportlab's paragraph markup, every text escaped."""
    out = []
    for span in spans:
        text = escape(span.text).replace("\n", "<br/>")
        if span.struck:
            text = f"<strike>{text}</strike>"
        if span.italic:
            text = f"<i>{text}</i>"
        if span.bold or bold:
            text = f"<b>{text}</b>"
        if span.sup:
            text = f"<super>{text}</super>"
        out.append(text)
    return "".join(out)
