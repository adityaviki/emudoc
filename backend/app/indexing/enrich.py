"""Enrich stage: the model writes the summaries for the map and extracts the fact table."""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal

from pydantic import BaseModel

from app.indexing.structure import SectionHint
from app.llm import LLM
from app.models import Block, Document, Section

# Text per request. Sections are kept whole unless one alone is larger than this.
WINDOW_CHARS = 40_000
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


class ProfileWithSections(DocumentProfile):
    sections: list[SectionSummary]


class InferredSection(BaseModel):
    start_block: int
    title: str
    level: int


class InferredSections(BaseModel):
    sections: list[InferredSection]


@dataclass
class Analysis:
    profile: DocumentProfile
    summaries: dict[int, str]  # section id -> summary
    facts: list[ExtractedFact]


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

FACT_INSTRUCTIONS = """\
- facts: the fields someone would later filter, compare or check: parties and their roles, \
identifiers (invoice, contract, order and account numbers), dates (issue, due, effective, \
expiry, signature), amounts (totals, taxes, fees, limits, rates) and terms (payment terms, \
notice periods, durations, governing law). Take only what the text states, and at most \
about 40 facts, the most important first. For each fact:
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


def infer_sections(llm: LLM, blocks: Sequence[Block]) -> list[SectionHint]:
    """Ask the model where the sections are in a document that has no headings."""
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
    result = llm.structured(prompt, InferredSections)
    return [
        SectionHint(s.start_block, s.title.strip(), s.level)
        for s in result.sections
        if s.title.strip()
    ]


def analyze_document(
    llm: LLM, document: Document, sections: Sequence[Section], blocks: Sequence[Block]
) -> Analysis:
    windows = _windows(sections, blocks)
    summaries: dict[int, str] = {}
    facts: list[ExtractedFact] = []
    section_ids = {section.id for section in sections}

    if len(windows) == 1:
        prompt = (
            f"{_render(document, windows[0], whole=True)}\n\n"
            "Return these fields for the document above.\n"
            f"{PROFILE_INSTRUCTIONS}\n"
            f"{SECTION_INSTRUCTIONS.format(scope='in the document')}\n"
            f"{FACT_INSTRUCTIONS}"
        )
        result = llm.structured(prompt, DocumentAnalysis)
        _collect(result.sections, section_ids, summaries)
        return Analysis(_profile(result), summaries, result.facts)

    for window in windows:
        prompt = (
            f"{_render(document, window, whole=False)}\n\n"
            "This is one part of a longer document. Return these fields for this part.\n"
            f"{SECTION_INSTRUCTIONS.format(scope='in this part')}\n"
            f"{FACT_INSTRUCTIONS}"
        )
        result = llm.structured(prompt, WindowAnalysis)
        _collect(result.sections, section_ids, summaries)
        facts.extend(result.facts)

    # Roll up: the profile, and any section whose text was split across parts.
    missing = [section for section in sections if section.id not in summaries]
    opening = "\n".join(
        _block_line(block) for block in sorted(blocks, key=lambda b: b.ordinal)[:30]
    )
    wanted = ", ".join(f"[S{section.id}]" for section in missing) or "none, return an empty list"
    prompt = (
        f"{_document_tag(document)}\n"
        f"<outline>\n{_outline(sections, summaries)}\n</outline>\n"
        f"<opening>\n{opening}\n</opening>\n"
        "</document>\n\n"
        "Above is the outline of a long document, with a one-sentence summary of each "
        "section already read, and the document's opening lines. Return these fields.\n"
        f"{PROFILE_INSTRUCTIONS}\n"
        "- sections: one sentence for each of these sections that has no summary yet, "
        f"written from the summaries of the sections beneath it: {wanted}."
    )
    result = llm.structured(prompt, ProfileWithSections)
    _collect(result.sections, section_ids, summaries)
    return Analysis(_profile(result), summaries, facts)


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


_Window = list[tuple[Section, list[Block]]]


def _windows(sections: Sequence[Section], blocks: Sequence[Block]) -> list[_Window]:
    """Group sections, each with its own blocks, into requests of bounded size."""
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
        for part in _split_by_size(own):
            part_size = sum(len(block.text) for block in part)
            if current and size + part_size > WINDOW_CHARS:
                windows.append(current)
                current, size = [], 0
            current.append((section, part))
            size += part_size

    if current:
        windows.append(current)
    return windows


def _split_by_size(blocks: list[Block]) -> list[list[Block]]:
    """Split one section's blocks only when the section alone exceeds a window."""
    parts: list[list[Block]] = []
    part: list[Block] = []
    size = 0
    for block in blocks:
        if part and size + len(block.text) > WINDOW_CHARS:
            parts.append(part)
            part, size = [], 0
        part.append(block)
        size += len(block.text)
    parts.append(part)
    return parts


def _document_tag(document: Document, part: bool = False) -> str:
    pages = document.page_count or "n/a"
    return f"<document filename={document.filename!r} pages={str(pages)!r}" + (
        ' part="true">' if part else ">"
    )


def _block_line(block: Block) -> str:
    page = f" (p.{block.page})" if block.page else ""
    return f"[B{block.ordinal}]{page} {block.text}"


def _render(document: Document, window: _Window, whole: bool) -> str:
    lines = [_document_tag(document, part=not whole)]
    for section, blocks in window:
        lines.append(f"\n## [S{section.id}] {section.path}")
        lines.extend(_block_line(block) for block in blocks)
    lines.append("</document>")
    return "\n".join(lines)


def _outline(sections: Sequence[Section], summaries: dict[int, str]) -> str:
    lines = []
    for section in sorted(sections, key=lambda s: s.ordinal):
        summary = summaries.get(section.id, "(no summary yet)")
        lines.append(f"{'  ' * (section.level - 1)}[S{section.id}] {section.title}: {summary}")
    return "\n".join(lines)
