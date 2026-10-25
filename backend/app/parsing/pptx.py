"""PowerPoint parsing with python-pptx: a slide a page, its title a heading, then its text and
tables as a slide is read, and its speaker notes after."""

from collections.abc import Iterable, Iterator
from pathlib import Path

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE, PP_PLACEHOLDER
from pptx.shapes.base import BaseShape
from pptx.slide import Slide

from app.parsing.types import (
    FileTooLarge,
    ParsedBlock,
    ParsedDocument,
    UnreadableFile,
    clean_text,
    table_to_markdown,
)

TITLES = {PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE, PP_PLACEHOLDER.VERTICAL_TITLE}
# The placeholders a layout lays its points out in, where every paragraph is a bullet even at
# the first level; a text box's paragraphs are bullets only when indented.
BULLETED = {PP_PLACEHOLDER.BODY, PP_PLACEHOLDER.OBJECT, PP_PLACEHOLDER.VERTICAL_BODY}
NO_SLIDES = "This presentation has no slides. Add one, or upload another file."


def parse_pptx(path: Path, max_pages: int = 0) -> ParsedDocument:
    """Parse the deck, each slide a page; one with more than `max_pages` slides (0: any number)
    is refused, as a PDF with more pages is, and one without slides as unreadable."""
    deck = Presentation(str(path))
    slides = list(deck.slides)
    if not slides:
        raise UnreadableFile(NO_SLIDES)
    if max_pages and len(slides) > max_pages:
        raise FileTooLarge(
            f"This presentation has {len(slides):,} slides, more than the {max_pages:,} that "
            "can be indexed. Split it and upload the parts."
        )
    blocks = [
        block for number, slide in enumerate(slides, start=1) for block in _slide(slide, number)
    ]
    title = clean_text(deck.core_properties.title or "") or None
    return ParsedDocument(blocks=blocks, page_count=len(slides), title=title)


def _slide(slide: Slide, number: int) -> Iterator[ParsedBlock]:
    """The slide's title as a heading of the first level, "Slide 3" where it has none, so that
    each slide is a section of the outline; then what it shows, and its speaker notes."""
    shapes = list(slide.shapes)
    title = next((shape for shape in shapes if _is_title(shape)), None)
    heading = clean_text(title.text_frame.text) if title is not None else ""
    yield ParsedBlock("heading", heading or f"Slide {number}", level=1, page=number)
    for shape in _in_reading_order(shape for shape in shapes if shape is not title):
        yield from _shape(shape, number)
    if slide.has_notes_slide and (frame := slide.notes_slide.notes_text_frame) is not None:
        notes = [text for paragraph in frame.paragraphs if (text := clean_text(paragraph.text))]
        if notes:
            yield ParsedBlock("heading", "Speaker notes", level=2, page=number)
            for text in notes:
                yield ParsedBlock("paragraph", text, page=number)


def _is_title(shape: BaseShape) -> bool:
    return shape.is_placeholder and shape.placeholder_format.type in TITLES and shape.has_text_frame


def _in_reading_order(shapes: Iterable[BaseShape]) -> list[BaseShape]:
    """Top to bottom, then left to right, as a slide is read rather than as its shapes were
    stacked; a placeholder whose place its layout keeps has none here, and reads as at the top."""
    return sorted(shapes, key=lambda shape: (shape.top or 0, shape.left or 0))


def _shape(shape: BaseShape, page: int) -> Iterator[ParsedBlock]:
    if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
        for inner in _in_reading_order(shape.shapes):
            yield from _shape(inner, page)
    elif getattr(shape, "has_table", False):
        rows = [[cell.text for cell in row.cells] for row in shape.table.rows]
        if any(any(cell.strip() for cell in row) for row in rows):
            yield ParsedBlock("table", table_to_markdown(rows), page=page)
    elif shape.has_text_frame:
        bulleted = shape.is_placeholder and shape.placeholder_format.type in BULLETED
        for paragraph in shape.text_frame.paragraphs:
            if text := clean_text(paragraph.text):
                kind = "list_item" if bulleted or paragraph.level > 0 else "paragraph"
                yield ParsedBlock(kind, text, page=page)
