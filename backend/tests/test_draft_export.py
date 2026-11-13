"""What the agent writes in Markdown as a file (app/draft_export.py): the Word document it keeps
in a member's My files, and the Markdown and the PDF an answer's and a chat's downloads are laid
out by, each citation a numbered note naming the passage it cites."""

import io
from datetime import UTC, datetime

import docx
import pdfplumber
import pytest
from reportlab.platypus import SimpleDocTemplate
from reportlab.platypus.doctemplate import LayoutError

from app.draft_export import (
    NOT_LAID_OUT,
    ExportRefused,
    Note,
    Span,
    blocks_of,
    draft_file,
    file_stem,
    markdown_file,
    notes_of,
    source_label,
)

INVOICED, ORDERED = 11, 12
# The passages the letter cites, as the agent was shown them.
SOURCES = [
    {
        "id": INVOICED,
        "document_title": "INV-1.md",
        "context": "INV-1.md › Totals",
        "page_start": 1,
        "page_end": 1,
    },
    {
        "id": ORDERED,
        "document_title": "PO-7701.md",
        "context": "PO-7701.md › Totals",
        "page_start": 1,
        "page_end": 1,
    },
    {"id": 13, "document_title": "Contract.pdf", "context": "Contract.pdf", "page_start": 4},
]
LETTER = (
    "Acme Supplies Ltd\n12 Harbour Road\n\n"
    "# Invoice INV-1\n\n"
    f"We dispute **invoice INV-1**: its total of 1,255.00 EUR [P{INVOICED}] is *5 EUR* over "
    f"the order's 1,250.00 EUR [P{ORDERED}] [P{INVOICED}]. A passage it never read "
    "[P999999] stays as it is written.\n\n"
    "- A credit note of 5.00 EUR\n- An answer by [Date]\n\n"
    "| Document | Total |\n| --- | ---: |\n"
    f"| INV-1 | 1,255.00 EUR [P{INVOICED}] |\n| PO-7701 | 1,250.00 EUR [P{ORDERED}] |\n\n"
    "<b>Zażółć</b> gęślą jaźń, see [the portal](https://example.com/claims).\n\n"
    "Kind regards,\n[Your name]"
)
WRITTEN_AT = datetime(2026, 11, 13, 9, 30, tzinfo=UTC)


def made(written_as: str) -> bytes:
    return draft_file(written_as, LETTER, SOURCES, "Dispute letter about INV-1.md", WRITTEN_AT)


def test_a_text_is_written_as_markdown_with_its_citations_as_footnotes():
    text = made("md").decode()
    # Numbered as the page numbers them, in the order the text first cites them.
    assert "1,255.00 EUR[^1] is *5 EUR* over the order's 1,250.00 EUR[^2][^1]." in text
    assert "| INV-1 | 1,255.00 EUR[^1] |" in text
    # A passage it was not shown, and a placeholder, stay as they are written.
    assert "[P999999] stays" in text and "[Your name]" in text and "[Date]" in text
    # Nothing a viewer of the file would run as HTML.
    assert "\\<b>Zażółć\\</b>" in text and "<b>" not in text.replace("\\<b>", "")
    # Only the passages it cites are notes.
    assert text.endswith("\n\n[^1]: INV-1.md · Totals · p. 1\n[^2]: PO-7701.md · Totals · p. 1\n")


def test_a_text_is_written_as_a_word_document_laid_out_from_its_markdown():
    document = docx.Document(io.BytesIO(made("docx")))
    paragraphs = [p for p in document.paragraphs if p.text]
    texts = [p.text for p in paragraphs]
    # The address a line each, as it is written.
    assert texts[0] == "Acme Supplies Ltd\n12 Harbour Road"
    assert (paragraphs[1].text, paragraphs[1].style.name) == ("Invoice INV-1", "Heading 1")
    claim = paragraphs[2]
    assert claim.text.startswith("We dispute invoice INV-1: its total of 1,255.00 EUR1 is 5 EUR")
    assert "the order's 1,250.00 EUR2,1. A passage it never read [P999999] stays" in claim.text
    assert [run.text for run in claim.runs if run.bold] == ["invoice INV-1"]
    assert [run.text for run in claim.runs if run.italic] == ["5 EUR"]
    assert [run.text for run in claim.runs if run.font.superscript] == ["1", "2", ",", "1"]
    assert [(p.text, p.style.name) for p in paragraphs[3:5]] == [
        ("A credit note of 5.00 EUR", "List Bullet"),
        ("An answer by [Date]", "List Bullet"),
    ]
    assert "<b>Zażółć</b> gęślą jaźń, see the portal (https://example.com/claims)." in texts
    assert "Kind regards,\n[Your name]" in texts
    # The notes after the text.
    assert texts[-3:] == [
        "Sources",
        "1.\tINV-1.md · Totals · p. 1",
        "2.\tPO-7701.md · Totals · p. 1",
    ]
    [table] = document.tables
    assert [[cell.text for cell in row.cells] for row in table.rows] == [
        ["Document", "Total"],
        ["INV-1", "1,255.00 EUR1"],
        ["PO-7701", "1,250.00 EUR2"],
    ]
    # Named as the file is, and by nobody: the template's own words are gone.
    properties = document.core_properties
    assert (properties.title, properties.author, properties.comments) == (
        "Dispute letter about INV-1.md",
        "",
        "",
    )


def test_a_text_is_written_as_a_pdf_set_in_its_own_font():
    with pdfplumber.open(io.BytesIO(made("pdf"))) as pdf:
        assert pdf.metadata["Title"] == "Dispute letter about INV-1.md"
        assert pdf.metadata["Creator"] == "Emudoc"
        [page] = pdf.pages
        text = page.extract_text()
        fonts = {char["fontname"].split("+")[-1] for char in page.chars}
    assert "Acme Supplies Ltd\n12 Harbour Road\nInvoice INV-1" in text
    # Letters beyond Latin-1, which a PDF's standard fonts lack.
    assert "<b>Zażółć</b> gęślą jaźń" in text
    assert "[P999999]" in text and "Kind regards,\n[Your name]" in text
    assert "Sources\n1. INV-1.md · Totals · p. 1\n2. PO-7701.md · Totals · p. 1" in text
    assert fonts == {"NotoSans-Regular", "NotoSans-Bold", "NotoSans-Italic"}


def test_a_pdf_that_cannot_be_laid_out_says_so(monkeypatch):
    def too_large(*args, **kwargs):
        raise LayoutError("Flowable too large")

    monkeypatch.setattr(SimpleDocTemplate, "build", too_large)
    with pytest.raises(ExportRefused, match=NOT_LAID_OUT):
        made("pdf")


def test_citations_are_numbered_and_named_as_the_page_does():
    sources = [
        {"id": 7, "document_title": "Contract.pdf", "context": "Contract.pdf › 4 Delivery"},
        {"id": 8, "document_title": "Log.xlsx", "context": "Log.xlsx", "page_start": 2},
        {"id": 9, "document_title": "Memo", "context": "", "page_start": 3, "page_end": 5},
    ]
    notes = notes_of("Late [P8], see [P7] and [P8] [P7]; not [P1].", sources)
    assert notes == {8: Note(1, "Log.xlsx · p. 2"), 7: Note(2, "Contract.pdf · 4 Delivery")}
    assert source_label(sources[2]) == "Memo · pp. 3–5"
    [paragraph] = blocks_of("Late [P8], see [P7] and [P8] [P7]; not [P1].", notes)
    assert paragraph.spans == [
        Span("Late"),
        Span("1", sup=True),
        Span(", see"),
        Span("2", sup=True),
        Span(" and"),
        Span("1", sup=True),
        Span(",", sup=True),
        Span("2", sup=True),
        Span("; not [P1]."),
    ]


def test_the_markdown_escapes_html_but_keeps_an_address_written_as_a_link():
    text = "<script>alert(1)</script> <!-- x --> a < b, <https://example.com/a>"
    assert markdown_file(text, {}) == (
        "\\<script>alert(1)\\</script> \\<!-- x --> a < b, <https://example.com/a>\n"
    )
    notes = {3: Note(1, "my_file*.pdf · [Draft]")}
    assert markdown_file("Cited [P3].", notes).endswith("[^1]: my\\_file\\*.pdf · \\[Draft\\]\n")


def test_a_files_name_holds_no_character_a_system_refuses():
    assert file_stem('Reply email: "Q3" a/b\\c?', "Letter") == "Reply email Q3 a b c"
    assert file_stem("::", "Letter") == "Letter"
