"""Parse stage: turn an uploaded file into ordered blocks with page and position."""

from pathlib import Path

from app.parsing.types import FileTooLarge, ParsedBlock, ParsedDocument, UnsupportedFile

IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".csv", ".txt", ".md", *IMAGE_TYPES}
# The type a document of each extension is stored and served as.
MIME_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".csv": "text/csv",
    ".txt": "text/plain",
    ".md": "text/markdown",
    **IMAGE_TYPES,
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
}


def parse_file(
    path: Path, filename: str, *, max_pages: int = 0, max_rows: int = 0
) -> ParsedDocument:
    """Parse a file by its extension. Images come back as a single page that needs OCR.

    A PDF with more than `max_pages` pages or a sheet with more than `max_rows` rows is refused
    with `FileTooLarge`; 0 takes any size.
    """
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        from app.parsing.pdf import parse_pdf

        return parse_pdf(path, max_pages)
    if suffix == ".docx":
        from app.parsing.docx import parse_docx

        return parse_docx(path)
    if suffix == ".xlsx":
        from app.parsing.sheet import parse_xlsx

        return parse_xlsx(path, max_rows)
    if suffix == ".csv":
        from app.parsing.sheet import parse_csv

        return parse_csv(path, max_rows)
    if suffix in {".txt", ".md"}:
        from app.parsing.text import parse_text

        return parse_text(
            path.read_text(encoding="utf-8", errors="replace"), markdown=suffix == ".md"
        )
    if suffix in IMAGE_TYPES:
        return ParsedDocument(blocks=[], page_count=1, ocr_pages=[1])
    raise UnsupportedFile(f"{suffix or 'this'} files are not supported")


__all__ = [
    "IMAGE_TYPES",
    "MIME_TYPES",
    "PARSER_MODULES",
    "SUPPORTED_EXTENSIONS",
    "FileTooLarge",
    "ParsedBlock",
    "ParsedDocument",
    "UnsupportedFile",
    "parse_file",
]
