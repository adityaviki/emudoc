"""Structure stage: build the outline from headings and cut sections into passages."""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

TARGET_CHARS = 1200
MAX_CHARS = 2000
MIN_CHARS = 200
PATH_SEPARATOR = " › "

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[\"'(\[]?[A-Z0-9])")


class BlockLike(Protocol):
    kind: str
    level: int | None
    text: str
    page: int | None


@dataclass
class SectionHint:
    """A section start proposed for a document that has no headings of its own."""

    start_block: int
    title: str
    level: int = 1


@dataclass
class SectionDraft:
    title: str
    level: int
    block_start: int
    block_end: int = 0  # exclusive, covers subsections
    # Own content: the blocks between this section's heading and its first subsection.
    content_start: int = 0
    content_end: int = 0
    parent: "SectionDraft | None" = None
    children: list["SectionDraft"] = field(default_factory=list)
    page_start: int | None = None
    page_end: int | None = None

    def path(self, document_title: str) -> str:
        titles = [self.title]
        node = self.parent
        while node:
            titles.append(node.title)
            node = node.parent
        return PATH_SEPARATOR.join([document_title, *reversed(titles)])


@dataclass
class PassageDraft:
    section: SectionDraft
    text: str
    block_start: int
    block_end: int  # exclusive
    page_start: int | None
    page_end: int | None


@dataclass
class Outline:
    title: str
    sections: list[SectionDraft]  # document order, parents before children
    passages: list[PassageDraft]


@dataclass
class _Start:
    index: int
    level: int
    title: str
    is_heading_block: bool


def build_outline(
    blocks: Sequence[BlockLike],
    fallback_title: str,
    page_count: int | None = None,
    hints: Sequence[SectionHint] = (),
) -> Outline:
    starts = [
        _Start(index, block.level or 1, block.text, True)
        for index, block in enumerate(blocks)
        if block.kind == "heading"
    ]
    title = fallback_title

    if starts:
        title_start = _document_title(starts)
        if title_start:
            title = title_start.title
            starts.remove(title_start)
    elif hints:
        starts = [
            _Start(hint.start_block, max(hint.level, 1), hint.title, False)
            for hint in sorted(hints, key=lambda h: h.start_block)
            if 0 <= hint.start_block < len(blocks)
        ]

    if not starts:
        starts = _page_starts(blocks, page_count) or [_Start(0, 1, title, False)]

    # Content ahead of the first heading still needs a home in the outline.
    if any(block.kind != "heading" for block in blocks[: starts[0].index]):
        starts.insert(0, _Start(0, min(s.level for s in starts), "Overview", False))

    sections = _build_tree(starts, blocks)
    passages = [passage for section in sections for passage in _section_passages(section, blocks)]
    return Outline(title=title, sections=sections, passages=passages)


def _document_title(starts: list[_Start]) -> _Start | None:
    """The opening heading, when it alone outranks every other heading, is the title."""
    first = starts[0]
    if first.index > 2 or len(starts) < 2:
        return None
    if all(first.level < other.level for other in starts[1:]):
        return first
    return None


def _page_starts(blocks: Sequence[BlockLike], page_count: int | None) -> list[_Start]:
    """One section per page, for long documents that offer no other structure."""
    if not page_count or page_count < 3:
        return []
    starts: list[_Start] = []
    seen: set[int] = set()
    for index, block in enumerate(blocks):
        if block.page is not None and block.page not in seen:
            seen.add(block.page)
            starts.append(_Start(index, 1, f"Page {block.page}", False))
    return starts


def _build_tree(starts: list[_Start], blocks: Sequence[BlockLike]) -> list[SectionDraft]:
    top_level = min(start.level for start in starts)
    sections: list[SectionDraft] = []
    stack: list[SectionDraft] = []

    for position, start in enumerate(starts):
        level = start.level - top_level + 1
        while stack and stack[-1].level >= level:
            stack.pop()
        next_index = starts[position + 1].index if position + 1 < len(starts) else len(blocks)
        section = SectionDraft(
            title=start.title,
            level=level,
            block_start=start.index,
            content_start=start.index + 1 if start.is_heading_block else start.index,
            content_end=next_index,
            parent=stack[-1] if stack else None,
        )
        if section.parent:
            section.parent.children.append(section)
        stack.append(section)
        sections.append(section)

    # A section ends where the next section of the same or a higher level begins.
    for position, section in enumerate(sections):
        section.block_end = next(
            (
                other.block_start
                for other in sections[position + 1 :]
                if other.level <= section.level
            ),
            len(blocks),
        )
        pages = [
            block.page
            for block in blocks[section.block_start : section.block_end]
            if block.page is not None
        ]
        if pages:
            section.page_start, section.page_end = min(pages), max(pages)
    return sections


@dataclass
class _Piece:
    text: str
    ordinal: int
    page: int | None
    kind: str


def _section_passages(section: SectionDraft, blocks: Sequence[BlockLike]) -> list[PassageDraft]:
    pieces = [
        _Piece(part, ordinal, blocks[ordinal].page, blocks[ordinal].kind)
        for ordinal in range(section.content_start, section.content_end)
        if blocks[ordinal].kind != "heading"
        for part in _split_block(blocks[ordinal])
    ]

    groups: list[list[_Piece]] = []
    current: list[_Piece] = []
    for piece in pieces:
        if piece.kind == "table":
            # Tables stay whole and separate: their rows only make sense together.
            if current:
                groups.append(current)
                current = []
            groups.append([piece])
            continue
        if current and sum(len(p.text) for p in current) + len(piece.text) > TARGET_CHARS:
            groups.append(current)
            current = []
        current.append(piece)
    if current:
        groups.append(current)

    # Fold a short tail into the passage before it rather than index a fragment.
    if len(groups) >= 2 and groups[-1][0].kind != "table" and groups[-2][0].kind != "table":
        tail = sum(len(p.text) for p in groups[-1])
        if tail < MIN_CHARS and tail + sum(len(p.text) for p in groups[-2]) <= MAX_CHARS:
            groups[-2].extend(groups.pop())

    return [_passage(section, group) for group in groups]


def _passage(section: SectionDraft, pieces: list[_Piece]) -> PassageDraft:
    parts: list[str] = []
    for index, piece in enumerate(pieces):
        if index:
            both_list_items = piece.kind == "list_item" and pieces[index - 1].kind == "list_item"
            parts.append("\n" if both_list_items else "\n\n")
        parts.append(piece.text)
    pages = [piece.page for piece in pieces if piece.page is not None]
    return PassageDraft(
        section=section,
        text="".join(parts),
        block_start=pieces[0].ordinal,
        block_end=pieces[-1].ordinal + 1,
        page_start=min(pages) if pages else None,
        page_end=max(pages) if pages else None,
    )


def _split_block(block: BlockLike) -> list[str]:
    text = block.text.strip()
    if not text:
        return []
    if len(text) <= MAX_CHARS:
        return [text]
    if block.kind == "table":
        return _split_table(text)
    return _split_prose(text)


def _split_table(markdown: str) -> list[str]:
    """Split a long pipe table by rows, repeating the header in every part."""
    lines = markdown.split("\n")
    header, rows = lines[:2], lines[2:]
    header_size = sum(len(line) + 1 for line in header)
    parts: list[str] = []
    current: list[str] = []
    size = header_size
    for row in rows:
        if current and size + len(row) + 1 > MAX_CHARS:
            parts.append("\n".join(header + current))
            current, size = [], header_size
        current.append(row)
        size += len(row) + 1
    if current:
        parts.append("\n".join(header + current))
    return parts


def _split_prose(text: str) -> list[str]:
    """Split long prose at sentence ends into parts near the target size."""
    parts: list[str] = []
    current = ""
    for sentence in _SENTENCE_END.split(text):
        while len(sentence) > MAX_CHARS:
            # No sentence break to use: cut at the last space inside the limit.
            cut = sentence.rfind(" ", 0, MAX_CHARS)
            cut = cut if cut > 0 else MAX_CHARS
            if current:
                parts.append(current)
                current = ""
            parts.append(sentence[:cut])
            sentence = sentence[cut:].lstrip()
        if current and len(current) + len(sentence) + 1 > TARGET_CHARS:
            parts.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}" if current else sentence
    if current:
        parts.append(current)
    return parts
