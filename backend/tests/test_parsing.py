import signal
import subprocess
import zipfile

import pytest
from PIL import Image

from app.parsing import FileTooLarge, UnsupportedFile, limits, parse_file, sheet
from app.parsing.limits import ParseError, ParseLimits, parse_within_limits
from app.parsing.signatures import HEAD, wrong_type
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


def test_xlsx_sheets_become_headings_and_tables(sample_xlsx):
    parsed = parse_file(sample_xlsx, "budget.xlsx")
    assert [(b.kind, b.level) for b in parsed.blocks] == [
        ("heading", 1),
        ("table", None),
        ("heading", 1),
        ("table", None),
    ]
    # The empty "Notes" sheet is left out; empty rows and columns are trimmed.
    assert parsed.blocks[0].text == "Budget" and parsed.blocks[2].text == "Totals"
    table = parsed.blocks[1].text.splitlines()
    assert table[0] == "| Item | Amount | Date |"
    assert "| Consulting | 1200 | 2024-03-01 |" in table
    assert "| Support | 300.5 |  |" in table
    assert parsed.blocks[3].text == "| Total | 1500.5 |\n| --- | --- |"


def test_csv_dialect_is_detected_and_rows_are_chunked(sample_csv, monkeypatch):
    monkeypatch.setattr(sheet, "ROWS_PER_BLOCK", 1)
    parsed = parse_file(sample_csv, "people.csv")
    assert [b.text for b in parsed.blocks] == [
        "| name | city |\n| --- | --- |\n| Ada | London |",
        "| name | city |\n| --- | --- |\n| Bob | Paris |",
    ]


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


@pytest.mark.parametrize("sample", ["sample_pdf", "sample_docx", "sample_xlsx", "sample_csv"])
def test_the_child_process_returns_what_the_parser_finds(sample, request):
    path = request.getfixturevalue(sample)
    assert parse_within_limits(path, path.name, ParseLimits()) == parse_file(path, path.name)


def test_a_parse_that_runs_too_long_is_killed(sample_pdf, monkeypatch):
    children = []
    start = subprocess.Popen

    def watched(*args, **kwargs):
        children.append(child := start(*args, **kwargs))
        return child

    monkeypatch.setattr(limits.subprocess, "Popen", watched)
    with pytest.raises(ParseError) as caught:
        parse_within_limits(sample_pdf, "msa.pdf", ParseLimits(timeout=0.01))
    assert str(caught.value) == (
        "Parsing did not finish within 0.01 seconds and was stopped. Split the file into "
        "smaller parts and upload those."
    )
    assert not caught.value.transient
    # Killed and reaped: nothing is left running or waiting to be waited for.
    [child] = children
    assert child.returncode == -signal.SIGKILL


def test_a_parse_that_needs_too_much_memory_is_stopped(sample_pdf):
    with pytest.raises(ParseError) as caught:
        parse_within_limits(sample_pdf, "msa.pdf", ParseLimits(memory_mb=1))
    assert str(caught.value) == (
        "Parsing needs more than the 1 MB of memory allowed and was stopped. Split the file "
        "into smaller parts and upload those."
    )


def test_a_pdf_with_too_many_pages_is_refused(sample_pdf):
    with pytest.raises(ParseError) as caught:
        parse_within_limits(sample_pdf, "msa.pdf", ParseLimits(max_pages=1))
    assert str(caught.value) == (
        "This PDF has 2 pages, more than the 1 that can be indexed. Split it and upload the parts."
    )


def test_a_sheet_with_too_many_rows_is_refused(sample_xlsx, sample_csv):
    # The caps count the rows with content: the Budget sheet has three, Totals one.
    assert parse_file(sample_xlsx, "budget.xlsx", max_rows=3).blocks
    with pytest.raises(FileTooLarge) as caught:
        parse_file(sample_xlsx, "budget.xlsx", max_rows=2)
    assert str(caught.value) == (
        'The sheet "Budget" has more than 2 rows, the most that can be indexed per sheet. Split '
        "it and upload the parts."
    )
    with pytest.raises(FileTooLarge) as caught:
        parse_file(sample_csv, "people.csv", max_rows=2)
    assert str(caught.value).startswith("This file has more than 2 rows")


def test_a_damaged_file_fails_with_the_parsers_error(tmp_path):
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"%PDF-1.4 but nothing of a PDF follows")
    with pytest.raises(ParseError) as caught:
        parse_within_limits(path, "broken.pdf", ParseLimits())
    assert str(caught.value) == (
        "The file could not be parsed (pdfplumber.PdfminerException). Check that it opens in "
        "another program, then upload it again."
    )


def test_images_and_unknown_files_start_no_process(tmp_path, monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("A process was started")

    monkeypatch.setattr(limits.subprocess, "Popen", refuse)
    assert parse_within_limits(tmp_path / "scan.png", "scan.png", ParseLimits()).ocr_pages == [1]
    with pytest.raises(UnsupportedFile):
        parse_within_limits(tmp_path / "data.bin", "data.bin", ParseLimits())


RENAME = "Upload the original, or rename it to its real type."


def test_bytes_that_are_what_the_extension_says_pass(
    sample_pdf, sample_docx, sample_xlsx, sample_csv, tmp_path
):
    images = {}
    for kind in ("PNG", "JPEG", "WEBP"):
        path = tmp_path / f"photo.{kind.lower()}"
        Image.new("RGB", (4, 4), "white").save(path, format=kind)
        images[kind] = path
    notes = tmp_path / "notes.txt"
    notes.write_text("Dear supplier,\n\nthe delivery was late.\n", encoding="utf-8")
    readme = tmp_path / "README.md"
    readme.write_bytes(b"\xef\xbb\xbf# Caf\xc3\xa9\n")  # a UTF-8 byte-order mark is fine
    # A two-byte character cut by the end of what the check reads is not a wrong byte.
    cut = tmp_path / "cut.csv"
    cut.write_bytes(b"a" * (HEAD - 1) + "é".encode() + b"\n" + b"b" * 10)

    assert wrong_type(sample_pdf, ".pdf") is None
    assert wrong_type(sample_docx, ".docx") is None
    assert wrong_type(sample_xlsx, ".xlsx") is None
    assert wrong_type(sample_csv, ".csv") is None
    assert wrong_type(images["PNG"], ".png") is None
    assert wrong_type(images["JPEG"], ".jpg") is None
    assert wrong_type(images["JPEG"], ".jpeg") is None
    assert wrong_type(images["WEBP"], ".webp") is None
    assert wrong_type(notes, ".txt") is None
    assert wrong_type(readme, ".md") is None
    assert wrong_type(cut, ".csv") is None


def test_bytes_that_are_not_what_the_extension_says_are_named(
    sample_pdf, sample_docx, sample_xlsx, tmp_path
):
    png = tmp_path / "picture.png"
    Image.new("RGB", (4, 4), "white").save(png, format="PNG")
    text = tmp_path / "letter.txt"
    text.write_text("Dear supplier,\n", encoding="utf-8")
    plain_zip = tmp_path / "archive.zip"
    with zipfile.ZipFile(plain_zip, "w") as archive:
        archive.writestr("readme.txt", "Not an Office package.")
    # A macro-enabled workbook declares another main part than a workbook, so it is not what
    # the name says even though openpyxl might read it.
    macros = tmp_path / "macros.zip"
    with zipfile.ZipFile(macros, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.ms-excel.sheet'
            '.macroEnabled.main+xml"/>',
        )
    binary = tmp_path / "binary.bin"
    binary.write_bytes(bytes(range(256)) * 4)
    latin = tmp_path / "latin.csv"
    latin.write_bytes("name;city\nMüller;Köln\n".encode("latin-1"))
    utf16 = tmp_path / "wide.txt"
    utf16.write_bytes("A note.\n".encode("utf-16"))
    # The header deep inside a PDF is junk before it, not a PDF.
    buried = tmp_path / "buried.pdf"
    buried.write_bytes(b"x" * 2000 + sample_pdf.read_bytes())

    assert wrong_type(text, ".pdf") == f"Not a PDF file. {RENAME}"
    assert wrong_type(buried, ".pdf") == f"Not a PDF file. {RENAME}"
    assert wrong_type(sample_pdf, ".docx") == f"Not a Word document. {RENAME}"
    assert wrong_type(sample_xlsx, ".docx") == f"Not a Word document. {RENAME}"
    assert wrong_type(plain_zip, ".docx") == f"Not a Word document. {RENAME}"
    assert wrong_type(sample_docx, ".xlsx") == f"Not an Excel workbook. {RENAME}"
    assert wrong_type(macros, ".xlsx") == f"Not an Excel workbook. {RENAME}"
    assert wrong_type(text, ".xlsx") == f"Not an Excel workbook. {RENAME}"
    assert wrong_type(png, ".jpg") == f"Not a JPEG image. {RENAME}"
    assert wrong_type(png, ".webp") == f"Not a WebP image. {RENAME}"
    assert wrong_type(sample_pdf, ".png") == f"Not a PNG image. {RENAME}"
    assert wrong_type(binary, ".txt") == f"Not a text file. {RENAME}"
    assert wrong_type(png, ".md") == f"Not a text file. {RENAME}"
    assert wrong_type(latin, ".csv") == "Not UTF-8 text. Save it as UTF-8 and upload it again."
    assert wrong_type(utf16, ".txt") == "Not UTF-8 text. Save it as UTF-8 and upload it again."
    assert wrong_type(text, ".zip") == f"Not a supported type. {RENAME}"
