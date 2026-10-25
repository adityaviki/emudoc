"""A draft's version as a file to save (app/draft_export.py, `GET
/api/drafts/{id}/versions/{n}/export`): its Markdown, a Word document or a PDF, each citation a
numbered note naming the passage it cites, each download a row of the audit log."""

import io

import docx
import pdfplumber
import pytest
from pydantic_ai.messages import TextPart
from reportlab.platypus import SimpleDocTemplate
from reportlab.platypus.doctemplate import LayoutError

from app.config import get_settings
from app.draft_export import (
    NOT_LAID_OUT,
    Note,
    Span,
    blocks_of,
    file_name,
    markdown_file,
    notes_of,
    source_label,
)
from app.storage import content_disposition
from tests.conftest import FakeTurn
from tests.test_api import create_project
from tests.test_audit import actions
from tests.test_drafts import (
    LETTER,
    ask,
    disputed,
    flags_on,  # noqa: F401 (fixture)
    saved,
    write,
)

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def letter(docs: dict[str, int]) -> str:
    invoiced, ordered = docs["invoiced"], docs["ordered"]
    return (
        "Acme Supplies Ltd\n12 Harbour Road\n\n"
        "# Invoice INV-1\n\n"
        f"We dispute **invoice INV-1**: its total of 1,255.00 EUR [P{invoiced}] is *5 EUR* over "
        f"the order's 1,250.00 EUR [P{ordered}] [P{invoiced}]. A passage it never read "
        "[P999999] stays as it is written.\n\n"
        "- A credit note of 5.00 EUR\n- An answer by [Date]\n\n"
        "| Document | Total |\n| --- | ---: |\n"
        f"| INV-1 | 1,255.00 EUR [P{invoiced}] |\n| PO-7701 | 1,250.00 EUR [P{ordered}] |\n\n"
        "<b>Zażółć</b> gęślą jaźń, see [the portal](https://example.com/claims).\n\n"
        "Kind regards,\n[Your name]"
    )


@pytest.fixture
def written(client, session, services) -> dict[str, int]:
    """A dispute letter of INV-1 written by the model, citing the totals' passages."""
    project = create_project(client)
    docs = disputed(client, session, services, project["id"])
    services.llm.agent_turns.append(FakeTurn(TextPart(letter(docs))))
    docs["draft"] = ask(client, project["id"], **LETTER, document_ids=[docs["INV-1"]])["id"]
    write(services)
    return docs


def export(client, draft_id: int, number: int = 1, **params):
    return client.get(f"/api/drafts/{draft_id}/versions/{number}/export", params=params)


def exported(session) -> list[dict]:
    return [detail for action, detail in actions(session) if action == "draft_exported"]


def test_a_version_downloads_as_its_markdown_with_its_citations_as_footnotes(
    client, session, written
):
    response = export(client, written["draft"], format="md")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "text/markdown; charset=utf-8"
    assert response.headers["content-disposition"] == content_disposition(
        "Dispute letter INV-1.md (version 1).md"
    )
    text = response.text
    # Numbered as the page numbers them, in the order the text first cites them.
    assert "1,255.00 EUR[^1] is *5 EUR* over the order's 1,250.00 EUR[^2][^1]." in text
    assert "| INV-1 | 1,255.00 EUR[^1] |" in text
    # A passage the version does not keep, and a placeholder, stay as they are written.
    assert "[P999999] stays" in text and "[Your name]" in text and "[Date]" in text
    # Nothing a viewer of the file would run as HTML.
    assert "\\<b>Zażółć\\</b>" in text and "<b>" not in text.replace("\\<b>", "")
    assert text.endswith("\n\n[^1]: INV-1.md · Totals · p. 1\n[^2]: PO-7701.md · Totals · p. 1\n")
    # Recorded with what was downloaded, never what it says.
    assert exported(session) == [{"kind": "dispute_letter", "version": 1, "format": "md"}]


def test_a_version_downloads_as_a_word_document_laid_out_from_its_markdown(
    client, session, written
):
    response = export(client, written["draft"])
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == DOCX
    assert response.headers["content-disposition"] == content_disposition(
        "Dispute letter INV-1.md (version 1).docx"
    )
    document = docx.Document(io.BytesIO(response.content))
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
    # Named as the draft, and by nobody: the template's own words are gone.
    properties = document.core_properties
    assert (properties.title, properties.author, properties.comments) == (
        "Dispute letter: INV-1.md",
        "",
        "",
    )
    assert exported(session) == [{"kind": "dispute_letter", "version": 1, "format": "docx"}]


def test_a_version_downloads_as_a_pdf_set_in_its_own_font(client, session, written):
    response = export(client, written["draft"], format="pdf")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/pdf"
    with pdfplumber.open(io.BytesIO(response.content)) as pdf:
        assert pdf.metadata["Title"] == "Dispute letter: INV-1.md"
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
    assert exported(session) == [{"kind": "dispute_letter", "version": 1, "format": "pdf"}]


def test_an_older_version_downloads_as_it_was(client, session, written):
    draft = written["draft"]
    mine = f"Dear supplier, the total is 5 EUR over [P{written['ordered']}]."
    assert saved(client, draft, mine, base=1).status_code == 201
    second = export(client, draft, 2, format="md")
    assert second.text == "Dear supplier, the total is 5 EUR over[^1].\n\n" + (
        "[^1]: PO-7701.md · Totals · p. 1\n"
    )
    assert second.headers["content-disposition"] == content_disposition(
        "Dispute letter INV-1.md (version 2).md"
    )
    assert "We dispute" in export(client, draft, 1, format="md").text
    assert export(client, draft, 3).status_code == 404
    assert export(client, draft, 0).status_code == 422
    assert export(client, draft, format="html").status_code == 422
    assert [detail["version"] for detail in exported(session)] == [2, 1]


def test_a_pdf_that_cannot_be_laid_out_says_so(client, session, written, monkeypatch):
    def too_large(*args, **kwargs):
        raise LayoutError("Flowable too large")

    monkeypatch.setattr(SimpleDocTemplate, "build", too_large)
    refused = export(client, written["draft"], format="pdf")
    assert (refused.status_code, refused.json()["detail"]) == (422, NOT_LAID_OUT)
    assert exported(session) == []


def test_a_viewer_downloads_a_draft_within_the_rate_of_downloads(
    client, account, written, monkeypatch, make_account, act_as
):
    act_as(make_account("vic@example.com", account.organization, role="viewer"))
    assert export(client, written["draft"], format="md").status_code == 200
    monkeypatch.setattr(get_settings(), "rate_exports", 1)
    refused = export(client, written["draft"], format="pdf")
    assert refused.status_code == 429
    assert "downloads of a facts table, a draft or a chat" in refused.json()["detail"]


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
    assert (
        file_name('Reply email: "Q3" a/b\\c?', 2, "pdf") == "Reply email Q3 a b c (version 2).pdf"
    )
    assert file_name("::", 1, "md") == "Draft (version 1).md"
