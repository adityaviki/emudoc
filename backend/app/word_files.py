"""Word documents as the agent reads and edits them in a member's My files (app/agent/writing.py;
my-files.md): a file read as numbered paragraphs with their styles and its tables between them,
and changed by those numbers, with python-docx, so that whatever an edit does not touch keeps its
formatting, its pictures and its parts.

A paragraph is one of the body's own: those inside a table are shown with it and reached by
finding and replacing their words alone. The numbers of one edit all name the paragraphs of the
version read, whatever the edit's other changes do to them, so the agent works from one reading;
the version kept after numbers them anew.
"""

import io
import re
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

import docx
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.draft_export import CITATION, line_spans, writable

# Characters of a document one reading shows before it asks the agent to go on, as `read` does.
READ_BUDGET = 12_000
# Rows of a table one reading shows, and characters of a cell.
TABLE_ROWS = 60
CELL_CHARS = 300
# Paragraph styles named when an edit asks for one the document lacks.
STYLES_NAMED = 40

Operation = Literal["replace", "insert", "delete", "find_replace"]

_P, _TBL, _SDT, _R, _T = (qn(f"w:{name}") for name in ("p", "tbl", "sdt", "r", "t"))
_PPR, _RPR, _SECT_PR = qn("w:pPr"), qn("w:rPr"), qn("w:sectPr")
_CITED = re.compile(rf" ?{CITATION.pattern}")


class Unreadable(Exception):
    """Bytes python-docx cannot open as a Word document."""


class EditRefused(Exception):
    """An edit that cannot be made as asked, with the sentences that say why; nothing is changed."""


@dataclass(frozen=True)
class Change:
    """One change of an edit: `replace` a paragraph by `text`, `insert` `text` after a paragraph
    (0 for the start), `delete` a paragraph or those through `through`, or `find_replace` the
    words `find` by `text` wherever they are. Each line of `text` is a paragraph, in Markdown's
    bold and italic; `style` names a paragraph style, or leaves the paragraph formatted like the
    one it replaces or follows."""

    op: Operation
    paragraph: int | None = None
    through: int | None = None
    text: str | None = None
    style: str | None = None
    find: str | None = None


def opened(data: bytes) -> Any:
    """The document, or `Unreadable`."""
    try:
        return docx.Document(io.BytesIO(data))
    except Exception as error:
        raise Unreadable(str(error)) from None


def reading(data: bytes, start: int = 1) -> str:
    """The document from paragraph `start` as the agent reads it: each paragraph numbered with
    its style, each table after the paragraph it follows, up to READ_BUDGET characters."""
    document = opened(data)
    body = document.element.body
    paragraphs = list(body.iterchildren(_P))
    tables = sum(1 for _ in body.iterchildren(_TBL))
    names = _style_names(document)
    lines = [f"{_counted(len(paragraphs), 'paragraph')}, {_counted(tables, 'table')}."]
    used = 0
    number = table = 0
    for child in body.iterchildren(_P, _TBL, _SDT):
        if child.tag == _P:
            number += 1
            if number < start:
                continue
            line = f"[{number}] ({_style_words(child, names)}) {_shown(child)}"
        elif child.tag == _TBL:
            table += 1
            if number < start - 1:
                continue
            line = _table(Table(child, document._body), table, number)
        else:
            if number < start - 1:
                continue
            line = f"(A content control, which edits leave as it is: {_text_of(child)[:200]})"
        if used and used + len(line) > READ_BUDGET:
            lines.append(
                f"... Call read_file again with from_paragraph={max(number, start)} to continue."
            )
            break
        lines.append(line)
        used += len(line)
    return "\n".join(lines)


def edited(data: bytes, changes: list[Change], now: datetime | None = None) -> tuple[bytes, str]:
    """The document with the changes made, and what was done in words; `EditRefused` when any
    change cannot be made, before any is."""
    document = opened(data)
    body = document.element.body
    paragraphs = list(body.iterchildren(_P))
    styles = _paragraph_styles(document)
    names = _style_names(document)
    problems = _problems(changes, len(paragraphs), styles, _used_styles(paragraphs, names))
    if problems:
        raise EditRefused(" ".join(problems))

    # Paragraphs inserted after each one so far, so that the next goes after them in order.
    inserted: dict[int, Any] = {}
    deleted: list[Any] = []
    done: list[str] = []

    def put(after: int, element: Any) -> None:
        last = inserted.get(after)
        if last is not None:
            last.addnext(element)
        elif after:
            paragraphs[after - 1].addnext(element)
        else:
            first = next((child for child in body if child.tag != _SECT_PR), None)
            if first is None:
                body.append(element)
            else:
                first.addprevious(element)
        inserted[after] = element

    for change in changes:
        if change.op == "replace":
            first, *more = _lines(change.text)
            target = paragraphs[change.paragraph - 1]
            _rewrite(target, first, styles.get((change.style or "").lower()))
            # A line more is a paragraph more after it, formatted like it.
            for line in more:
                put(change.paragraph, _like(target, line))
            done.append(f"replaced paragraph {change.paragraph}")
        elif change.op == "insert":
            lines = _lines(change.text)
            for line in lines:
                put(change.paragraph, _new(document, paragraphs, change, styles, names, line))
            where = f"after paragraph {change.paragraph}" if change.paragraph else "at the start"
            done.append(f"inserted {_counted(len(lines), 'paragraph')} {where}")
        elif change.op == "delete":
            last = change.through or change.paragraph
            deleted += paragraphs[change.paragraph - 1 : last]
            done.append(
                f"deleted paragraph {change.paragraph}"
                if last == change.paragraph
                else f"deleted paragraphs {change.paragraph}-{last}"
            )
    for paragraph in deleted:
        body.remove(paragraph)
    for change in changes:
        if change.op == "find_replace":
            times = sum(
                _replace_words(paragraph, change.find, change.text or "")
                for paragraph in _every_paragraph(document)
            )
            if not times:
                raise EditRefused(
                    f'"{change.find}" is nowhere in the document: nothing was changed. Read it '
                    "again and give words it holds, as they are written."
                )
            done.append(f'replaced "{change.find}" {_times(times)}')
    properties = document.core_properties
    properties.modified = (now or datetime.now(UTC)).astimezone(UTC).replace(tzinfo=None)
    properties.revision = (properties.revision or 0) + 1
    out = io.BytesIO()
    document.save(out)
    return out.getvalue(), "; ".join(done)


def _problems(
    changes: list[Change], count: int, styles: dict[str, Any], used: list[str]
) -> list[str]:
    """What keeps the changes from being made as asked, each a sentence."""
    problems: list[str] = []
    touched: dict[int, str] = {}

    def number(value: int | None, what: str, *, start: bool = False) -> bool:
        lowest = 0 if start else 1
        if value is None or not lowest <= value <= count:
            span = f"0 to {count}" if start else f"1 to {count}"
            problems.append(f"{what} must name a paragraph by its number, {span}.")
            return False
        return True

    def claim(paragraph: int, how: str) -> None:
        if paragraph in touched:
            problems.append(f"Paragraph {paragraph} is both {touched[paragraph]} and {how}.")
        touched[paragraph] = how

    for index, change in enumerate(changes, 1):
        what = f"Change {index} ({change.op})"
        if change.style and change.style.lower() not in styles:
            others = sorted(style.name for style in styles.values() if style.name not in used)
            named = ", ".join([*used, *others][:STYLES_NAMED])
            problems.append(
                f'{what}: the document has no paragraph style "{change.style}". Its styles: '
                f"{named}."
            )
        if change.op in ("replace", "insert") and change.text is None:
            problems.append(f"{what} needs the text.")
        if change.op == "replace" and number(change.paragraph, what):
            claim(change.paragraph, "replaced")
        elif change.op == "insert":
            number(change.paragraph, what, start=True)
        elif change.op == "delete" and number(change.paragraph, what):
            last = change.through or change.paragraph
            if change.through is not None and not change.paragraph <= last <= count:
                problems.append(f"{what}: through must be {change.paragraph} to {count}.")
                continue
            for paragraph in range(change.paragraph, last + 1):
                claim(paragraph, "deleted")
        elif change.op == "find_replace":
            if not change.find:
                problems.append(f"{what} needs the words to find.")
            if "\n" in (change.text or ""):
                problems.append(f"{what}: a replacement is words within a paragraph, one line.")
    return problems


def _paragraph_styles(document: Any) -> dict[str, Any]:
    """The document's paragraph styles by their names in lower case."""
    return {
        style.name.lower(): style
        for style in document.styles
        if style.type == WD_STYLE_TYPE.PARAGRAPH and style.name
    }


def _used_styles(paragraphs: list[Any], names: dict[str | None, str]) -> list[str]:
    """The paragraph styles the document's paragraphs have, in the order they first do."""
    return list(dict.fromkeys(names.get(p.style) or names[None] for p in paragraphs))


def _lines(text: str | None) -> list[str]:
    """The paragraphs a change's text makes, without the passages' references, which mean nothing
    in a file."""
    return [_CITED.sub("", line) for line in writable(text or "").split("\n")]


def _rewrite(paragraph: Any, text: str, style: Any | None) -> None:
    """The paragraph's words replaced by `text`, keeping its own formatting and the one most of
    its words have, or taking `style`."""
    character = _words_format(paragraph)
    for child in list(paragraph):
        if child.tag != _PPR:
            paragraph.remove(child)
    if style is not None:
        paragraph.style = style.style_id
    _write(paragraph, text, character)


def _like(model: Any, text: str) -> Any:
    """A new paragraph of `text` formatted like `model`: its paragraph's properties without a
    section's end, and the formatting most of its words have."""
    paragraph = deepcopy(model)
    for child in list(paragraph):
        if child.tag != _PPR:
            paragraph.remove(child)
    properties = paragraph.find(_PPR)
    if properties is not None:
        for section in properties.findall(_SECT_PR):
            properties.remove(section)
    _write(paragraph, text, _words_format(model))
    return paragraph


def _new(
    document: Any,
    paragraphs: list[Any],
    change: Change,
    styles: dict[str, Any],
    names: dict[str | None, str],
    text: str,
) -> Any:
    """A paragraph to insert: like the paragraph of the style asked for that comes first in the
    document, or a bare one of it; with no style, like the paragraph it follows, or for the start
    or after a heading like the body's first paragraph from there."""
    if change.style:
        style = styles[change.style.lower()]
        default = document.styles.default(WD_STYLE_TYPE.PARAGRAPH)
        plain = default.style_id if default is not None else None
        model = next((p for p in paragraphs if (p.style or plain) == style.style_id), None)
        if model is not None:
            return _like(model, text)
        paragraph = document.element.body.makeelement(_P, {})
        paragraph.style = style.style_id
        _write(paragraph, text, None)
        return paragraph
    after = paragraphs[change.paragraph - 1] if change.paragraph else None
    if after is not None and not _heading(after, names):
        return _like(after, text)
    model = next(
        (p for p in paragraphs[change.paragraph :] if not _heading(p, names)),
        None,
    )
    if model is not None:
        return _like(model, text)
    paragraph = document.element.body.makeelement(_P, {})
    _write(paragraph, text, None)
    return paragraph


def _heading(paragraph: Any, names: dict[str | None, str]) -> bool:
    """Whether the paragraph is a heading or a title, which body text inserted after it does
    not take the look of."""
    name = (names.get(paragraph.style) or "").lower()
    return name.startswith(("heading", "title", "subtitle"))


def _words_format(paragraph: Any) -> Any | None:
    """The character formatting of the run that holds most of the paragraph's words, a
    hyperlink's included; None when that run has none."""
    runs = [(len(_text_of(run)), index, run) for index, run in enumerate(paragraph.iter(_R))]
    if not runs:
        return None
    # The longest, and of those the first.
    _, _, run = max(runs, key=lambda item: (item[0], -item[1]))
    return run.find(_RPR)


def _write(paragraph: Any, text: str, character: Any | None) -> None:
    """Runs of `text` added to the paragraph, its Markdown's bold, italic and struck words set, each
    run formatted as `character` beside."""
    words = Paragraph(paragraph, None)
    for span in line_spans(text):
        run = words.add_run(span.text)
        if character is not None:
            run._r.insert(0, deepcopy(character))
        if span.bold:
            run.bold = True
        if span.italic:
            run.italic = True
        if span.struck:
            run.font.strike = True


def _every_paragraph(document: Any) -> list[Any]:
    """Every paragraph whose words a find and replace reaches: the body's, its tables' included,
    then each section's own headers and footers'."""
    found = list(document.element.body.iter(_P))
    seen: set[int] = set()
    for section in document.sections:
        for part in (
            section.header,
            section.footer,
            section.first_page_header,
            section.first_page_footer,
            section.even_page_header,
            section.even_page_footer,
        ):
            # A header linked to the section before has none of its own, and asking for its
            # paragraphs would make one.
            if part.is_linked_to_previous or id(part._element) in seen:
                continue
            seen.add(id(part._element))
            found += list(part._element.iter(_P))
    return found


def _replace_words(paragraph: Any, find: str, replacement: str) -> int:
    """Replace `find` by `replacement` in the paragraph's words however its runs split them, the
    replacement taking the formatting of the run the words begin in; how many times."""
    pieces = [piece for piece in paragraph.iter(_T)]
    whole = "".join(piece.text or "" for piece in pieces)
    starts = [match.start() for match in re.finditer(re.escape(find), whole)]
    if not starts:
        return 0
    # Where each piece begins in the paragraph's words.
    offsets = []
    at = 0
    for piece in pieces:
        offsets.append(at)
        at += len(piece.text or "")
    # From the last, so that the offsets before it still hold.
    for start in reversed(starts):
        end = start + len(find)
        first = max(i for i, offset in enumerate(offsets) if offset <= start)
        last = max(i for i, offset in enumerate(offsets) if offset < end)
        head = (pieces[first].text or "")[: start - offsets[first]]
        tail = (pieces[last].text or "")[end - offsets[last] :]
        for index in range(first, last + 1):
            pieces[index].text = ""
        pieces[first].text = head + replacement + (tail if first == last else "")
        if first != last:
            pieces[last].text = tail
        for index in (first, last):
            pieces[index].set(qn("xml:space"), "preserve")
    return len(starts)


def _style_names(document: Any) -> dict[str | None, str]:
    """Each paragraph style's name by its id, None for the document's default style."""
    names: dict[str | None, str] = {
        style.style_id: style.name for style in document.styles if style.name
    }
    default = document.styles.default(WD_STYLE_TYPE.PARAGRAPH)
    names[None] = default.name if default is not None and default.name else "Normal"
    return names


def _style_words(paragraph: Any, names: dict[str | None, str]) -> str:
    """The paragraph's style as the agent reads it, and its level in a list."""
    name = names.get(paragraph.style) or paragraph.style or names[None]
    numbering = paragraph.find(f"{_PPR}/{qn('w:numPr')}")
    if numbering is not None:
        level = numbering.find(qn("w:ilvl"))
        depth = int(level.get(qn("w:val"), "0")) + 1 if level is not None else 1
        return f"{name}, list level {depth}"
    return name


def _shown(paragraph: Any) -> str:
    """A paragraph's words as read, with what it holds besides words."""
    text = _text_of(paragraph)
    extras = []
    if paragraph.find(f".//{qn('w:drawing')}") is not None:
        extras.append("a picture")
    if any(br.get(qn("w:type")) == "page" for br in paragraph.iter(qn("w:br"))):
        extras.append("a page break")
    if paragraph.find(f"{_PPR}/{_SECT_PR}") is not None:
        extras.append("the end of a section")
    held = f" [{', '.join(extras)}]" if extras else ""
    return (text or "(empty)") + held


def _table(table: Table, number: int, after: int) -> str:
    """A table as the agent reads it: where it is and its rows, a cell's paragraphs on one line."""
    rows = table.rows
    where = f"after paragraph {after}" if after else "at the start"
    lines = [f"Table {number}, {where}, {_counted(len(rows), 'row')}:"]
    for row in list(rows)[:TABLE_ROWS]:
        cells = []
        last = None
        for cell in row.cells:
            # A merged cell is listed once for each column it spans.
            if cell._tc is last:
                continue
            last = cell._tc
            words = " / ".join(text for p in cell._tc.iter(_P) if (text := _text_of(p)))
            cells.append(words[:CELL_CHARS].replace("|", "\\|"))
        lines.append("| " + " | ".join(cells) + " |")
    if len(rows) > TABLE_ROWS:
        lines.append(f"... {len(rows) - TABLE_ROWS} more rows.")
    return "\n".join(lines)


def _text_of(element: Any) -> str:
    """An element's words, its tabs as spaces and its line breaks as such."""
    words = []
    for node in element.iter(_T, qn("w:tab"), qn("w:br")):
        if node.tag == _T:
            words.append(node.text or "")
        elif node.tag == qn("w:tab"):
            words.append("\t")
        else:
            words.append("\n")
    return "".join(words).strip()


def _times(n: int) -> str:
    return {1: "once", 2: "twice"}.get(n, f"{n} times")


def _counted(n: int, thing: str) -> str:
    return f"{n} {thing}{'' if n == 1 else 's'}"
