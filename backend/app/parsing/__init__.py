"""Parse stage: turn an uploaded file into ordered blocks with page and position."""

from pathlib import Path

from app.parsing.types import (
    FileTooLarge,
    ParsedBlock,
    ParsedDocument,
    UnreadableFile,
    UnsupportedFile,
    too_much_text,
)

IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
# Scans a browser does not show and the model does not take as they are: a TIFF of a page a
# frame and a phone's HEIC photo, read and drawn as pages, as a scanned PDF's are
# (app/parsing/scans.py).
SCAN_TYPES = {
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".heic": "image/heic",
    ".heif": "image/heif",
}
# The types whose pages are drawn as images, for the document pane and for the model to read.
PAGE_TYPES = {"application/pdf", *SCAN_TYPES.values()}
SUPPORTED_EXTENSIONS = {
    ".pdf",
    ".docx",
    ".xlsx",
    ".csv",
    ".txt",
    ".md",
    *IMAGE_TYPES,
    *SCAN_TYPES,
}
# The type a document of each extension is stored and served as.
MIME_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".csv": "text/csv",
    ".txt": "text/plain",
    ".md": "text/markdown",
    **IMAGE_TYPES,
    **SCAN_TYPES,
}
# The module that parses each extension, imported when it is first needed; the child process
# that parses an upload (app/parsing/limits.py) loads only the one its file takes.
PARSER_MODULES = {
    ".pdf": "app.parsing.pdf",
    ".docx": "app.parsing.docx",
    ".xlsx": "app.parsing.sheet",
    ".csv": "app.parsing.sheet",
    ".txt": "app.parsing.text",
    ".md": "app.parsing.text",
    **dict.fromkeys(SCAN_TYPES, "app.parsing.scans"),
}


def parse_file(
    path: Path, filename: str, *, max_pages: int = 0, max_rows: int = 0, max_chars: int = 0
) -> ParsedDocument:
    """Parse a file by its extension. Images come back as a single page that needs OCR, and
    scans as a page a frame that each need it.

    A PDF or a scan with more than `max_pages` pages, a sheet with more than `max_rows` rows or
    a file whose blocks hold more than `max_chars` characters of text is refused with
    `FileTooLarge`; 0 takes any size. A PDF locked with a password or without pages is refused with
    `UnreadableFile`.
    """
    parsed = _parsed(path, filename, max_pages, max_rows, max_chars)
    if max_chars and sum(len(block.text) for block in parsed.blocks) > max_chars:
        raise FileTooLarge(too_much_text(max_chars))
    return parsed


def _parsed(
    path: Path, filename: str, max_pages: int, max_rows: int, max_chars: int
) -> ParsedDocument:
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        from app.parsing.pdf import parse_pdf

        return parse_pdf(path, max_pages)
    if suffix == ".docx":
        from app.parsing.docx import parse_docx

        return parse_docx(path)
    # A sheet stops as soon as its rows are past the text it may hold, before it is held
    # whole; the others are read whole anyway, and checked once parsed.
    if suffix == ".xlsx":
        from app.parsing.sheet import parse_xlsx

        return parse_xlsx(path, max_rows, max_chars)
    if suffix == ".csv":
        from app.parsing.sheet import parse_csv

        return parse_csv(path, max_rows, max_chars)
    if suffix in {".txt", ".md"}:
        from app.parsing.text import parse_text

        return parse_text(
            path.read_text(encoding="utf-8", errors="replace"), markdown=suffix == ".md"
        )
    if suffix in SCAN_TYPES:
        from app.parsing.scans import parse_scan

        return parse_scan(path, max_pages)
    if suffix in IMAGE_TYPES:
        return ParsedDocument(blocks=[], page_count=1, ocr_pages=[1])
    raise UnsupportedFile(f"{suffix or 'this'} files are not supported")


__all__ = [
    "IMAGE_TYPES",
    "MIME_TYPES",
    "PAGE_TYPES",
    "PARSER_MODULES",
    "SCAN_TYPES",
    "SUPPORTED_EXTENSIONS",
    "FileTooLarge",
    "ParsedBlock",
    "ParsedDocument",
    "UnreadableFile",
    "UnsupportedFile",
    "parse_file",
]
