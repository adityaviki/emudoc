"""Enrich stage: the model writes the summaries for the map and extracts the fact table."""

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal

from pydantic import BaseModel

from app.extraction import Template, chosen
from app.indexing.structure import SectionHint
from app.llm import LLM
from app.models import Block, Document, Section
from app.usage import ModelUsage

# Text per request. Sections are kept whole unless one alone is larger than this, and a block
# larger than this is shown in pieces of it.
WINDOW_CHARS = 40_000
# A table of more rows than this, as one block or as blocks that repeat its header (a sheet's,
# or one running on over a PDF's pages), is shown to the model in part: its header, its first
# SAMPLE_FIRST rows and its last SAMPLE_LAST. What the model makes of a sheet of orders is a
# summary, which a few of its rows give as well as all of them, and the rows are searched and
# read in answers all the same; sent whole, a sheet of 90,000 rows took hundreds of requests.
# A shorter table is shown whole: an invoice's lines and a price list are facts.
WHOLE_TABLE_ROWS = 100
SAMPLE_FIRST = 20
SAMPLE_LAST = 5
# The roll-up of a long document shows its opening as the windows show it, this much at most.
OPENING_ITEMS = 30
OPENING_CHARS = 10_000
# A listing of the project's extraction templates past this many characters names each field
# and its type alone, without what it is or its examples (app/extraction.py).
LISTING_CHARS = 16_000
# Beyond this the block listing for section inference gets too large to be worth it.
MAX_BLOCKS_FOR_INFERENCE = 4000
PREVIEW_CHARS = 160


class SectionSummary(BaseModel):
    section: int
    summary: str


class ExtractedFact(BaseModel):
    name: str
    value: str
    type: Literal["text", "number", "date", "money"]
    number: float | None
    date: str | None
    currency: str | None
    block: int
    quote: str


class WindowAnalysis(BaseModel):
    sections: list[SectionSummary]
    facts: list[ExtractedFact]


class DocumentProfile(BaseModel):
    title: str
    doc_type: str
    doc_date: str | None
    summary: str


class DocumentAnalysis(DocumentProfile, WindowAnalysis):
    """Everything about a document that fits in one request."""


class TemplatedWindowAnalysis(BaseModel):
    """The first part of a long document, asked which of the project's templates it is for. The
    template comes before the facts, so that the reply names it before it reads its fields."""

    template: str | None
    sections: list[SectionSummary]
    facts: list[ExtractedFact]


class TemplatedDocumentAnalysis(DocumentProfile):
    """A document in one request, asked which of the project's templates it is for."""

    template: str | None
    sections: list[SectionSummary]
    facts: list[ExtractedFact]


class ProfileWithSections(DocumentProfile):
    sections: list[SectionSummary]


class InferredSection(BaseModel):
    start_block: int
    title: str
    level: int


class InferredSections(BaseModel):
    sections: list[InferredSection]


@dataclass(frozen=True)
class Reading:
    """How far the model read a document that ENRICH_MAX_REQUESTS stopped short of its end."""

    # The last page it read, for a document with pages.
    page: int | None
    # The share of the document's text up to the last block it read.
    share: float


@dataclass
class Analysis:
    profile: DocumentProfile
    summaries: dict[int, str]  # section id -> summary
    facts: list[ExtractedFact]
    # The blocks of the tables shown in part, which no fact may come from: their rows are a
    # few of many, and a fact table holding a few orders of thousands answers a count wrong.
    sampled: frozenset[int] = frozenset()
    # None when the model read the whole document.
    reading: Reading | None = None
    # The extraction template the model found the document is for, whose fields its facts
    # were read for; None when it is for none of the project's, or none were listed.
    template: Template | None = None


PROFILE_INSTRUCTIONS = """\
- title: the document's own title as written, or a short descriptive title if it has none.
- doc_type: one or two lowercase words for the kind of document, such as invoice, contract, \
purchase order, bank statement, report, letter, resume, policy or minutes.
- doc_date: the document's own date (issue, signing or effective date) as YYYY-MM-DD, or null \
if it states none.
- summary: two or three sentences on what this document is and its key particulars: who, \
what, when, how much."""

SECTION_INSTRUCTIONS = """\
- sections: for every section marker [S...] {scope}, one sentence saying what that section \
contains, specific enough to tell it apart from its neighbours. Use the number from the \
marker as `section`."""

_FACT_KINDS = """\
fields someone would later filter, compare or check: parties and their roles, \
identifiers (invoice, contract, order and account numbers), dates (issue, due, effective, \
expiry, signature), amounts (totals, taxes, fees, limits, rates) and terms (payment terms, \
notice periods, durations, governing law)."""

_FACT_RULES = """ Take only what the text states, and at most \
about 40 facts, the most important first. Take no facts from a table shown in part. For each \
fact:
  - name: snake_case. Prefer these names when they fit: document_number, issue_date, \
due_date, effective_date, expiry_date, signature_date, party, vendor_name, customer_name, \
total_amount, subtotal_amount, tax_amount, payment_terms, notice_period, term_length, \
governing_law, po_number, account_number, contact_email, contact_phone, address. Otherwise \
choose a clear name of your own. Use one name for one kind of field; a document can have \
several facts with the same name, for example several `party` facts.
  - value: as written in the document. For a party, add its role in brackets when stated.
  - type: text, number, date or money.
  - number: the numeric value for number and money facts, without separators or symbols; \
otherwise null.
  - date: YYYY-MM-DD for date facts; otherwise null.
  - currency: the ISO 4217 code for money facts when stated or evident; otherwise null.
  - block: the number of the [B...] block the fact comes from.
  - quote: the exact words from that block that state the fact, copied verbatim, at most \
about 20 words."""

FACT_INSTRUCTIONS = f"- facts: the {_FACT_KINDS}{_FACT_RULES}"

# With the project's extraction templates listed (app/extraction.py): which one the document is
# for, then its fields first among the facts.
TEMPLATE_INSTRUCTIONS = """\
- template: the kind of document this is when it is one of the kinds in <templates> below, \
written exactly as listed there (such as "invoice"); otherwise null."""


def template_facts(which: str) -> str:
    """The facts asked for beside a template: its fields first, then the others as without one;
    `which` says which template ("when you named a template")."""
    return (
        f"- facts: first, {which}, each of its fields that the text states, under the field's "
        "own name and of its type; a field stated more than once, such as the parties or the "
        "lines of an invoice, has a fact for each. A required field is one every document of "
        "the kind should state, so look for it with care; but leave out any field the text does "
        f"not state, and never guess one. Then the other {_FACT_KINDS}{_FACT_RULES}"
    )


def infer_sections(
    llm: LLM, blocks: Sequence[Block], usage: ModelUsage | None = None
) -> list[SectionHint]:
    """Ask the model where the sections are in a document that has no headings. The call is
    added to `usage` when it is answered."""
    if len(blocks) > MAX_BLOCKS_FOR_INFERENCE:
        return []
    listing = "\n".join(
        f"[B{block.ordinal}] {block.text[:PREVIEW_CHARS]}"
        + ("…" if len(block.text) > PREVIEW_CHARS else "")
        for block in blocks
    )
    prompt = (
        "This document has no headings. Below is the start of each of its blocks, in order.\n\n"
        f"<blocks>\n{listing}\n</blocks>\n\n"
        "Divide the document into the sections a reader would recognise. For each section "
        "give start_block (the number of its first block), a short title of two to six words "
        "taken from or describing its content, and level (1 for a top-level section, 2 for a "
        "subsection). The first section must start at the first block. A short document may "
        "be a single section."
    )
    result = llm.structured(prompt, InferredSections, usage)
    return [
        SectionHint(s.start_block, s.title.strip(), s.level)
        for s in result.sections
        if s.title.strip()
    ]


def analyze_document(
    llm: LLM,
    document: Document,
    sections: Sequence[Section],
    blocks: Sequence[Block],
    usage: ModelUsage | None = None,
    max_requests: int = 0,
    templates: Sequence[Template] = (),
    conventions: str = "",
) -> Analysis:
    """Summaries, profile and facts for the document, in as many requests as its size takes
    and `max_requests` allows (0: any number); each is added to `usage` as it is answered, so
    a run that fails at one window has still counted the ones before.

    A document longer than the requests allow is read from its start for as many windows as
    leave one request for the roll-up, and `Analysis.reading` says how far that went: the
    sections after it have no summary, and its facts are those of the part read.

    With `templates`, the project's extraction templates (app/extraction.py), the request that
    reads the document's start lists them and names the one the document is for, whose fields
    the facts are read for there and in the windows after it, which are shown that template
    alone; `Analysis.template` is the one named. Without, the requests are as they always were.

    With `conventions`, a paragraph on how the document writes its dates and numbers
    (`reading_line` in app/locales.py), every request has it after the document; without, the
    requests say nothing of them.
    """
    windows = _windows(sections, blocks)
    written = f"{conventions}\n" if conventions else ""
    summaries: dict[int, str] = {}
    facts: list[ExtractedFact] = []
    section_ids = {section.id for section in sections}
    sampled = frozenset(
        ordinal
        for window in windows
        for _, items in window
        for item in items
        if item.rows
        for ordinal in range(item.first.ordinal, item.last.ordinal + 1)
    )

    if len(windows) == 1 or max_requests == 1:
        # The whole document in one request, or as much of it as one request holds.
        whole = len(windows) == 1
        if whole:
            ask, scope = "Return these fields for the document above.", "in the document"
        else:
            ask = (
                "This is the opening part of a longer document, and all of it that is read. "
                "Return these fields for the document as far as this part shows them."
            )
            scope = "in this part"
        if templates:
            facts_asked = (
                f"{TEMPLATE_INSTRUCTIONS}\n"
                f"{SECTION_INSTRUCTIONS.format(scope=scope)}\n"
                f"{template_facts('when you named a template')}\n\n"
                f"{_listing(templates)}"
            )
        else:
            facts_asked = f"{SECTION_INSTRUCTIONS.format(scope=scope)}\n{FACT_INSTRUCTIONS}"
        prompt = (
            f"{_render(document, windows[0], whole=whole)}\n\n"
            f"{written}{ask}\n"
            f"{PROFILE_INSTRUCTIONS}\n"
            f"{facts_asked}"
        )
        schema = TemplatedDocumentAnalysis if templates else DocumentAnalysis
        result = llm.structured(prompt, schema, usage)
        template = chosen(templates, getattr(result, "template", None))
        _collect(result.sections, section_ids, summaries)
        reading = None if whole else _reading(windows[:1], blocks)
        return Analysis(_profile(result), summaries, result.facts, sampled, reading, template)

    # Every window and the roll-up, or as many windows as leave the roll-up its request.
    if max_requests and len(windows) >= max_requests:
        read = windows[: max_requests - 1]
    else:
        read = windows
    template = None
    for number, window in enumerate(read):
        if number == 0 and templates:
            result = _choose_in_window(llm, document, window, usage, templates, written)
            template = chosen(templates, result.template)
        else:
            result = _read_window(llm, document, window, usage, template, written)
        _collect(result.sections, section_ids, summaries)
        facts.extend(result.facts)

    # Roll up: the profile, and any section read whose summary none of its parts gave, such
    # as a heading with only subsections beneath it.
    seen = {section.id for window in read for section, _ in window}
    unread = section_ids - seen
    missing = [section for section in sections if section.id in seen - summaries.keys()]
    wanted = ", ".join(f"[S{section.id}]" for section in missing) or "none, return an empty list"
    prompt = (
        f"{_document_tag(document)}\n"
        f"<outline>\n{_outline(sections, summaries, unread)}\n</outline>\n"
        f"<opening>\n{_opening(read)}\n</opening>\n"
        "</document>\n\n"
        f"{written}"
        "Above is the outline of a long document, with a one-sentence summary of each "
        "section already read, and the document's opening lines. Return these fields.\n"
        f"{PROFILE_INSTRUCTIONS}\n"
        "- sections: one sentence for each of these sections that has no summary yet, "
        f"written from the summaries of the sections beneath it: {wanted}."
    )
    result = llm.structured(prompt, ProfileWithSections, usage)
    # Only the sections asked for: one not read has nothing to be summarised from but its title.
    _collect(result.sections, {section.id for section in missing}, summaries)
    reading = _reading(read, blocks) if len(read) < len(windows) else None
    return Analysis(_profile(result), summaries, facts, sampled, reading, template)


def _choose_in_window(
    llm: LLM,
    document: Document,
    window: "_Window",
    usage: ModelUsage | None,
    templates: Sequence[Template],
    written: str = "",
) -> TemplatedWindowAnalysis:
    """A long document's first part, beside the templates, which it names the one of."""
    prompt = (
        f"{_render(document, window, whole=False)}\n\n"
        f"{written}This is one part of a longer document. Return these fields for this part.\n"
        f"{TEMPLATE_INSTRUCTIONS}\n"
        f"{SECTION_INSTRUCTIONS.format(scope='in this part')}\n"
        f"{template_facts('when you named a template')}\n\n"
        f"{_listing(templates)}"
    )
    return llm.structured(prompt, TemplatedWindowAnalysis, usage)


def _read_window(
    llm: LLM,
    document: Document,
    window: "_Window",
    usage: ModelUsage | None,
    known: Template | None,
    written: str = "",
) -> WindowAnalysis:
    """One part of a long document, beside the template its first part named, or none."""
    if known is None:
        about, facts = "", FACT_INSTRUCTIONS
    else:
        about = ", which its first part showed to be of the kind in <templates> below"
        facts = f"{template_facts('for the template below')}\n\n{_listing([known])}"
    prompt = (
        f"{_render(document, window, whole=False)}\n\n"
        f"{written}This is one part of a longer document{about}. Return these fields for this "
        "part.\n"
        f"{SECTION_INSTRUCTIONS.format(scope='in this part')}\n"
        f"{facts}"
    )
    return llm.structured(prompt, WindowAnalysis, usage)


def _listing(templates: Sequence[Template]) -> str:
    """The templates as a prompt lists them: each field with what it is and its examples while
    the listing is short, and past LISTING_CHARS with its type alone."""
    body = "\n".join(_described(template, full=True) for template in templates)
    if len(body) > LISTING_CHARS:
        body = "\n".join(_described(template, full=False) for template in templates)
    return f"<templates>\n{body}\n</templates>"


def _described(template: Template, full: bool) -> str:
    about = f" {_sentence(template.description)}" if full and template.description else ""
    lines = [f"{template.doc_type}: {_sentence(template.title)}{about}"]
    for field in template.fields:
        line = f"  - {field.name} ({field.type}{', required' if field.required else ''})"
        said = []
        if full and field.description:
            said.append(_sentence(field.description))
        if full and field.examples:
            examples = ", ".join(
                json.dumps(example, ensure_ascii=False) for example in field.examples
            )
            said.append(f"For example {examples}.")
        lines.append(f"{line}: {' '.join(said)}" if said else line)
    return "\n".join(lines)


def _sentence(text: str) -> str:
    text = text.strip()
    return text if not text or text[-1] in ".!?" else f"{text}."


def parse_iso_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def normalize_fact_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:100]


def _profile(result: DocumentProfile) -> DocumentProfile:
    return DocumentProfile(
        title=result.title,
        doc_type=result.doc_type,
        doc_date=result.doc_date,
        summary=result.summary,
    )


def _collect(items: list[SectionSummary], valid: set[int], into: dict[int, str]) -> None:
    for item in items:
        if item.section in valid and item.summary.strip():
            into[item.section] = item.summary.strip()


@dataclass(frozen=True)
class _Item:
    """What the model is shown of the blocks from `first` to `last`: one block whole, a piece
    of one too long for a request (`piece` of `pieces`), or a long table in part (`rows` is
    how many it has in all)."""

    first: Block
    last: Block
    text: str
    rows: int = 0
    piece: int = 0
    pieces: int = 0


_Window = list[tuple[Section, list[_Item]]]


def _windows(sections: Sequence[Section], blocks: Sequence[Block]) -> list[_Window]:
    """Group sections, each with what is shown of its own blocks, into requests of bounded
    size."""
    by_ordinal = {block.ordinal: block for block in blocks}
    ordered = sorted(sections, key=lambda section: section.ordinal)
    windows: list[_Window] = []
    current: _Window = []
    size = 0

    for position, section in enumerate(ordered):
        # Own blocks run up to the next section in document order.
        end = (
            ordered[position + 1].block_start if position + 1 < len(ordered) else section.block_end
        )
        own = [by_ordinal[o] for o in range(section.block_start, end) if o in by_ordinal]
        for part in _split_by_size(_condensed(own)):
            part_size = sum(len(item.text) for item in part)
            if current and size + part_size > WINDOW_CHARS:
                windows.append(current)
                current, size = [], 0
            current.append((section, part))
            size += part_size

    if current:
        windows.append(current)
    return windows


def _split_by_size(items: list[_Item]) -> list[list[_Item]]:
    """Split one section's items only when the section alone exceeds a window."""
    parts: list[list[_Item]] = []
    part: list[_Item] = []
    size = 0
    for item in items:
        if part and size + len(item.text) > WINDOW_CHARS:
            parts.append(part)
            part, size = [], 0
        part.append(item)
        size += len(item.text)
    parts.append(part)
    return parts


def _condensed(blocks: Sequence[Block]) -> list[_Item]:
    """What the model is shown of a section's own blocks, in order: each block whole, but a
    long table in part and a block longer than a window in pieces."""
    items: list[_Item] = []
    position = 0
    while position < len(blocks):
        block = blocks[position]
        if block.kind != "table":
            items.extend(_pieces(block))
            position += 1
            continue
        # The blocks that go on with this table: a sheet's repeat its header (sheet.py).
        header = _header(block)
        end = position + 1
        while end < len(blocks) and blocks[end].kind == "table" and _header(blocks[end]) == header:
            end += 1
        run = blocks[position:end]
        rows = sum(_row_count(block) for block in run)
        if rows > WHOLE_TABLE_ROWS:
            items.append(_Item(run[0], run[-1], _sample(run), rows=rows))
        else:
            items.extend(_Item(block, block, block.text) for block in run)
        position = end
    return items


def _pieces(block: Block) -> list[_Item]:
    """The block whole, or in pieces of a window at most, cut at a space where there is one."""
    text = block.text
    if len(text) <= WINDOW_CHARS:
        return [_Item(block, block, text)]
    parts: list[str] = []
    while text:
        cut = len(text) if len(text) <= WINDOW_CHARS else text.rfind(" ", 0, WINDOW_CHARS)
        cut = cut if cut > 0 else WINDOW_CHARS
        parts.append(text[:cut])
        text = text[cut:].lstrip()
    return [
        _Item(block, block, part, piece=number, pieces=len(parts))
        for number, part in enumerate(parts, 1)
    ]


def _header(block: Block) -> str:
    end = block.text.find("\n")
    return block.text if end < 0 else block.text[:end]


def _ruled(block: Block) -> bool:
    """Whether the table's second line is the rule under its header, as a Markdown table has."""
    start = block.text.find("\n") + 1
    if not start:
        return False
    end = block.text.find("\n", start)
    line = block.text[start:] if end < 0 else block.text[start:end]
    return "-" in line and not line.strip("|:- ")


def _head_lines(block: Block) -> int:
    return 2 if _ruled(block) else 1


def _row_count(block: Block) -> int:
    return block.text.count("\n") + 1 - _head_lines(block)


def _sample(run: Sequence[Block]) -> str:
    """A long table as the model is shown it: its header, its first and last rows, and a row
    of ellipses where the others are."""
    first: list[str] = []
    for block in run:
        first.extend(block.text.split("\n")[_head_lines(block) :][: SAMPLE_FIRST - len(first)])
        if len(first) == SAMPLE_FIRST:
            break
    last: list[str] = []
    for block in reversed(run):
        rows = block.text.split("\n")[_head_lines(block) :]
        last[:0] = rows[max(len(rows) - (SAMPLE_LAST - len(last)), 0) :]
        if len(last) == SAMPLE_LAST:
            break
    head = run[0].text.split("\n")[: _head_lines(run[0])]
    columns = max(head[0].count("|") - 1, 1)
    gap = "| " + " | ".join(["…"] * columns) + " |"
    return "\n".join([*head, *first, gap, *last])


def _document_tag(document: Document, part: bool = False) -> str:
    pages = document.page_count or "n/a"
    return f"<document filename={document.filename!r} pages={str(pages)!r}" + (
        ' part="true">' if part else ">"
    )


def _item_line(item: _Item) -> str:
    page = f" (p.{item.first.page})" if item.first.page else ""
    marker = f"[B{item.first.ordinal}]{page}"
    if item.rows:
        blocks = (
            f" in blocks B{item.first.ordinal} to B{item.last.ordinal}"
            if item.last is not item.first
            else ""
        )
        return (
            f"{marker} A table of {item.rows:,} rows{blocks}, shown in part: its header, its "
            f"first {SAMPLE_FIRST} rows and its last {SAMPLE_LAST}.\n{item.text}"
        )
    if item.pieces:
        return f"{marker} (part {item.piece} of {item.pieces}) {item.text}"
    return f"{marker} {item.text}"


def _render(document: Document, window: _Window, whole: bool) -> str:
    lines = [_document_tag(document, part=not whole)]
    for section, items in window:
        lines.append(f"\n## [S{section.id}] {section.path}")
        lines.extend(_item_line(item) for item in items)
    lines.append("</document>")
    return "\n".join(lines)


def _outline(sections: Sequence[Section], summaries: dict[int, str], unread: set[int]) -> str:
    lines = []
    for section in sorted(sections, key=lambda s: s.ordinal):
        summary = summaries.get(
            section.id, "(not read)" if section.id in unread else "(no summary yet)"
        )
        lines.append(f"{'  ' * (section.level - 1)}[S{section.id}] {section.title}: {summary}")
    return "\n".join(lines)


def _opening(windows: Sequence[_Window]) -> str:
    """The document's first lines as the windows showed them, OPENING_ITEMS and OPENING_CHARS
    at most but never none: a sheet's first blocks are thousands of rows."""
    lines: list[str] = []
    size = 0
    for window in windows:
        for _, items in window:
            for item in items:
                line = _item_line(item)
                if lines and (len(lines) == OPENING_ITEMS or size + len(line) > OPENING_CHARS):
                    return "\n".join(lines)
                lines.append(line)
                size += len(line)
    return "\n".join(lines)


def _reading(read: Sequence[_Window], blocks: Sequence[Block]) -> Reading:
    """How far the windows read reach into the document: the last block any of them shows."""
    last = max(
        (item.last.ordinal for window in read for _, items in window for item in items),
        default=-1,
    )
    total = sum(len(block.text) for block in blocks)
    within = sum(len(block.text) for block in blocks if block.ordinal <= last)
    page = max((b.page for b in blocks if b.ordinal <= last and b.page), default=None)
    return Reading(page=page, share=within / total if total else 1.0)
