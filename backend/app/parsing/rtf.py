"""Rich Text Format parsing with striprtf: the document's text, a paragraph a line as RTF ends
them, read as plain text is (app/parsing/text.py). RTF keeps a heading as a style the text does
not show, so a heading is found as plain text finds one, a short line in capitals."""

from pathlib import Path

from striprtf.striprtf import rtf_to_text

from app.parsing.text import parse_text
from app.parsing.types import ParsedDocument

# What the file's bytes are read as. RTF is seven-bit text that writes every other character as
# an escape, in the code page the document names; a writer that put raw bytes in mostly wrote
# Windows' Western code page, which striprtf takes for an escape's too where none is named.
CODE_PAGE = "cp1252"


def parse_rtf(path: Path) -> ParsedDocument:
    source = path.read_bytes().decode(CODE_PAGE, errors="replace")
    text = rtf_to_text(source, encoding=CODE_PAGE, errors="replace")
    # A line is a paragraph in RTF, where plain text runs lines on until a blank one.
    return parse_text("\n\n".join(text.splitlines()))
