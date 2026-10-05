from app.parsing import parse_file
from app.parsing.text import parse_text
from app.parsing.types import table_to_markdown


def test_pdf_headings_tables_and_positions(sample_pdf):
    parsed = parse_file(sample_pdf, "msa.pdf")

    assert parsed.page_count == 2
    assert len(parsed.page_sizes) == 2
    assert parsed.ocr_pages == []

    headings = [(b.text, b.level) for b in parsed.blocks if b.kind == "heading"]
    assert headings == [
        ("Master Services Agreement", 1),
        ("1. Definitions", 1),
        ("1.1 Interpretation", 2),
        ("2. Fees and payment", 1),
        ("3. Termination", 1),
    ]

    table = next(b for b in parsed.blocks if b.kind == "table")
    assert table.text.splitlines()[0] == "| Service | Rate | Unit |"
    assert "| Consulting | EUR 1,200 | per day |" in table.text
    # The table's cells are not repeated as loose paragraphs.
    assert not any(b.kind == "paragraph" and "EUR 1,200" in b.text for b in parsed.blocks)

    termination = next(b for b in parsed.blocks if "ninety (90) days" in b.text)
    assert termination.page == 2
    x0, top, x1, bottom = termination.bbox
    width, height = parsed.page_sizes[1]
    assert 0 <= x0 < x1 <= width and 0 <= top < bottom <= height


def test_pdf_drops_running_headers_and_page_numbers(sample_pdf):
    texts = [b.text for b in parse_file(sample_pdf, "msa.pdf").blocks]
    assert not any("Confidential" in text for text in texts)
    assert not any(text.startswith("Page ") for text in texts)


def test_pdf_joins_wrapped_lines_into_one_paragraph(sample_pdf):
    blocks = parse_file(sample_pdf, "msa.pdf").blocks
    opening = next(b for b in blocks if b.text.startswith("This agreement is made"))
    assert opening.text.endswith("consulting services are provided.")


def test_docx_styles_become_structure(sample_docx):
    parsed = parse_file(sample_docx, "policy.docx")
    kinds = [(b.kind, b.level, b.text) for b in parsed.blocks]

    assert kinds[0] == ("heading", 1, "Travel Policy")
    assert ("heading", 1, "Booking") in kinds
    assert ("list_item", None, "Economy class for flights under six hours.") in kinds
    table = next(b for b in parsed.blocks if b.kind == "table")
    assert "| Europe | EUR 60 |" in table.text
    assert all(b.page is None for b in parsed.blocks)


def test_markdown_blocks():
    parsed = parse_text(
        "# Title\n\nFirst paragraph\ncontinues here.\n\n"
        "## Section\n\n- one\n- two\n\n"
        "| a | b |\n| --- | --- |\n| 1 | 2 |\n\n"
        "```\ncode line\n```\n\n---\n\nLast.",
        markdown=True,
    )
    assert [(b.kind, b.level) for b in parsed.blocks] == [
        ("heading", 1),
        ("paragraph", None),
        ("heading", 2),
        ("list_item", None),
        ("list_item", None),
        ("table", None),
        ("paragraph", None),
        ("paragraph", None),
    ]
    assert parsed.blocks[1].text == "First paragraph continues here."
    assert parsed.blocks[6].text == "code line"


def test_plain_text_headings():
    parsed = parse_text("SUMMARY\n\nSome text here.\n\nDetails\n=======\n\nMore text.")
    assert [(b.kind, b.text) for b in parsed.blocks] == [
        ("heading", "SUMMARY"),
        ("paragraph", "Some text here."),
        ("heading", "Details"),
        ("paragraph", "More text."),
    ]


def test_table_to_markdown_pads_and_escapes():
    markdown = table_to_markdown([["a", "b|c"], ["only one"], [None, "x\ny"]])
    assert markdown.splitlines() == [
        "| a | b\\|c |",
        "| --- | --- |",
        "| only one |  |",
        "|  | x y |",
    ]
