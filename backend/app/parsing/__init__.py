"""Parse stage: turn an uploaded file into ordered blocks with page and position."""

from pathlib import Path

from app.parsing.types import ParsedBlock, ParsedDocument, UnsupportedFile

IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md", *IMAGE_TYPES}


def parse_file(path: Path, filename: str) -> ParsedDocument:
    """Parse a file by its extension. Images come back as a single page that needs OCR."""
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        from app.parsing.pdf import parse_pdf

        return parse_pdf(path)
    if suffix == ".docx":
        from app.parsing.docx import parse_docx

        return parse_docx(path)
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
    "SUPPORTED_EXTENSIONS",
    "ParsedBlock",
    "ParsedDocument",
    "UnsupportedFile",
    "parse_file",
]
