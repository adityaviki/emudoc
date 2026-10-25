import io
import signal
import subprocess
import zipfile
from contextlib import contextmanager

import pytest
from pdfplumber.utils.exceptions import PdfminerException
from PIL import Image

from app.parsing import FileTooLarge, UnreadableFile, UnsupportedFile, limits, parse_file, sheet
from app.parsing.limits import ParseError, ParseLimits, parse_within_limits
from app.parsing.signatures import HEAD, wrong_type
from app.parsing.text import parse_text
from app.parsing.types import table_to_markdown
from tests.conftest import compound_file, pageless_pdf, protected_pdf, turned_pdf


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


PRICE_LIST = [
    ("heading", "Price list 2024"),
    ("paragraph", "Prices hold until December and are quoted per unit, before tax."),
    ("table", "| Item | Price |\n| --- | --- |\n| Pallet | EUR 40 |\n| Crate | EUR 12 |"),
]


def on_the_ink(path, parsed) -> bool:
    """Whether each block's box holds ink on the page as the pane draws it, and no ink lies
    outside every box: the text laid over the page and a cited passage's mark sit on it."""
    from app.parsing.render import draw

    width, height = parsed.page_sizes[0]
    image = Image.open(io.BytesIO(draw(path, 1, 1.0, 2000))).convert("L")
    assert abs(image.width / image.height - width / height) < 0.01
    scale = image.width / width
    ink = image.point(lambda value: 255 if value < 128 else 0)
    boxes = [tuple(round(value * scale) for value in block.bbox) for block in parsed.blocks]
    if any(ink.crop(box).getbbox() is None for box in boxes):
        return False
    for x0, top, x1, bottom in boxes:
        ink.paste(0, (x0 - 2, top - 2, x1 + 2, bottom + 2))
    return ink.getbbox() is None


@pytest.mark.parametrize(
    ("rotation", "drawn_turned"),
    [(0, 0), (90, 90), (90, 0), (270, 0), (0, 90), (180, 0)],
    ids=["upright", "landscape", "reading-down", "reading-up", "drawn-sideways", "upside-down"],
)
def test_a_page_is_read_the_way_its_text_runs_and_placed_where_it_is_drawn(
    tmp_path, rotation, drawn_turned
):
    path = turned_pdf(tmp_path / "prices.pdf", rotation, drawn_turned)
    parsed = parse_file(path, "prices.pdf")

    assert [(block.kind, block.text) for block in parsed.blocks] == PRICE_LIST
    assert on_the_ink(path, parsed)


def test_text_turned_against_its_page_is_read_after_it_and_heads_no_section(tmp_path):
    path = turned_pdf(tmp_path / "prices.pdf", note="Ref ACME-2024-0042, 1 March 2024")
    parsed = parse_file(path, "prices.pdf")

    assert [(block.kind, block.text) for block in parsed.blocks] == [
        *PRICE_LIST,
        ("paragraph", "Ref ACME-2024-0042, 1 March 2024"),
    ]
    assert on_the_ink(path, parsed)


def test_docx_styles_become_structure(sample_docx):
    parsed = parse_file(sample_docx, "policy.docx")
    kinds = [(b.kind, b.level, b.text) for b in parsed.blocks]

    assert kinds[0] == ("heading", 1, "Travel Policy")
    assert ("heading", 1, "Booking") in kinds
    assert ("list_item", None, "Economy class for flights under six hours.") in kinds
    table = next(b for b in parsed.blocks if b.kind == "table")
    assert "| Europe | EUR 60 |" in table.text
    assert all(b.page is None for b in parsed.blocks)


def test_pptx_slides_become_pages_headed_by_their_titles(sample_pptx):
    parsed = parse_file(sample_pptx, sample_pptx.name)
    assert parsed.title == "Supplier review 2024"
    assert parsed.page_count == 3
    assert [(block.page, block.kind, block.level, block.text) for block in parsed.blocks] == [
        (1, "heading", 1, "Supplier review"),
        (1, "paragraph", None, "Acme Logistics, third quarter"),
        (2, "heading", 1, "Deliveries"),
        # A layout's points are bullets at every level.
        (2, "list_item", None, "14 of 16 deliveries on time"),
        (2, "list_item", None, "Two late in August"),
        (2, "heading", 2, "Speaker notes"),
        (2, "paragraph", None, "Ask about the August delays."),
        # A slide without a title is named by its number, and read top to bottom whatever
        # order its shapes were added in.
        (3, "heading", 1, "Slide 3"),
        (3, "paragraph", None, "Price list"),
        (3, "table", None, "| Item | Price |\n| --- | --- |\n| Pallet | EUR 40 |"),
        (3, "paragraph", None, "Prices hold until December."),
    ]


def test_a_presentation_with_too_many_slides_or_none_is_refused(sample_pptx, tmp_path):
    with pytest.raises(ParseError) as refused:
        parse_within_limits(sample_pptx, "review.pptx", ParseLimits(max_pages=2))
    assert str(refused.value) == (
        "This presentation has 3 slides, more than the 2 that can be indexed. Split it and upload "
        "the parts."
    )
    from pptx import Presentation

    empty = tmp_path / "empty.pptx"
    Presentation().save(str(empty))
    with pytest.raises(ParseError) as refused:
        parse_within_limits(empty, "empty.pptx", ParseLimits())
    assert str(refused.value) == "This presentation has no slides. Add one, or upload another file."


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


PAGE = """<!doctype html>
<html><head><meta charset="windows-1252"><title>Delivery terms</title>
<style>p { color: red }</style><script>var shown = "<p>not text</p>";</script></head>
<body><nav><a href="/">Home</a> <a href="/terms">Terms</a></nav>
<h1>Delivery terms</h1>
<p>Goods are delivered within <b>14 days</b>
of the order.<br>Delays are notified in writing.</p>
<h2>Prices</h2>
<ul><li>Pallet: EUR 40</li><li>Crate <ul><li>with lid</li></ul></li></ul>
<table><tr><th>Item</th><th>Price</th></tr><tr><td>Pallet</td><td>EUR&nbsp;40</td></tr></table>
<div hidden>A draft clause</div>
<pre>Item   Qty
Pallet   4</pre>
<p>Signed for Café Nord &amp; Co.</p>
</body></html>"""


def test_html_keeps_its_headings_lists_and_tables_and_leaves_out_its_code_and_navigation(
    tmp_path,
):
    # Saved in the code page it names, which is not UTF-8.
    page = tmp_path / "terms.html"
    page.write_bytes(PAGE.encode("cp1252"))
    parsed = parse_within_limits(page, page.name, ParseLimits())
    assert parsed.title == "Delivery terms"
    assert [(block.kind, block.level, block.text) for block in parsed.blocks] == [
        ("heading", 1, "Delivery terms"),
        (
            "paragraph",
            None,
            "Goods are delivered within 14 days of the order. Delays are notified in writing.",
        ),
        ("heading", 2, "Prices"),
        ("list_item", None, "Pallet: EUR 40"),
        ("list_item", None, "Crate"),
        ("list_item", None, "with lid"),
        ("table", None, "| Item | Price |\n| --- | --- |\n| Pallet | EUR 40 |"),
        ("paragraph", None, "Item   Qty\nPallet   4"),
        ("paragraph", None, "Signed for Café Nord & Co."),
    ]


def test_html_is_read_in_the_encoding_its_bytes_and_its_page_say(tmp_path):
    from app.parsing.html import decode_html

    text = '<meta charset="iso-8859-1"><p>Café</p>'
    # A page saved again as UTF-8 that still names its old encoding is read as UTF-8.
    assert decode_html(text.encode("utf-8")) == text
    assert decode_html(text.encode("latin-1")) == text
    assert decode_html(text.encode("utf-16")) == text
    assert decode_html(b"\xef\xbb\xbf" + text.encode("utf-8")) == text
    # Neither UTF-8 nor saying: the encoding browsers fall back to.
    assert decode_html("<p>Caf\u00e9 \u20ac</p>".encode("cp1252")) == "<p>Café €</p>"


def test_rtf_is_read_a_paragraph_a_line(tmp_path):
    letter = tmp_path / "letter.rtf"
    letter.write_bytes(
        rb"{\rtf1\ansi\ansicpg1252\deff0{\fonttbl{\f0 Arial;}}{\info{\title Late}}"
        rb"\f0 LATE DELIVERY\par Dear supplier,\par the pallets arrived on 3 March,"
        rb" two days late.\par Caf\'e9 Nord\par}"
    )
    parsed = parse_within_limits(letter, letter.name, ParseLimits())
    assert [(block.kind, block.text) for block in parsed.blocks] == [
        ("heading", "LATE DELIVERY"),
        ("paragraph", "Dear supplier,"),
        ("paragraph", "the pallets arrived on 3 March, two days late."),
        ("paragraph", "Café Nord"),
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


@pytest.mark.parametrize(
    "sample", ["sample_pdf", "sample_docx", "sample_xlsx", "sample_pptx", "sample_csv"]
)
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


def test_the_callers_slot_is_held_from_the_childs_start_to_its_end(
    sample_pdf, tmp_path, monkeypatch
):
    # The caller bounds how many children run at once (PARSE_CONCURRENCY in the pipeline), so
    # its slot is held while one runs, one killed at the time limit too, and not for a file
    # that starts none.
    children, held = [], []
    start = subprocess.Popen

    def watched(*args, **kwargs):
        assert held[-1] == "entered"
        children.append(child := start(*args, **kwargs))
        return child

    @contextmanager
    def slot():
        held.append("entered")
        try:
            yield
        finally:
            assert children[-1].returncode is not None
            held.append("left")

    monkeypatch.setattr(limits.subprocess, "Popen", watched)
    assert parse_within_limits(sample_pdf, "msa.pdf", ParseLimits(), slot=slot).page_count == 2
    with pytest.raises(ParseError, match="did not finish"):
        parse_within_limits(sample_pdf, "msa.pdf", ParseLimits(timeout=0.01), slot=slot)
    assert held == ["entered", "left"] * 2 and len(children) == 2
    assert parse_within_limits(tmp_path / "scan.png", "scan.png", ParseLimits(), slot=slot)
    assert len(held) == 4


def test_a_parse_that_needs_too_much_memory_is_stopped(tmp_path):
    # The cap is below what the child holds once its libraries are loaded, so it is hit by the
    # first allocation the heap cannot serve from what it holds already. The two-page sample PDF
    # needs only 64 to 256 KB more, which the allocator's slack sometimes holds (both of CI's
    # suites, 2026-10-23), so the file is a text whose reading needs megabytes at once.
    text = tmp_path / "long.md"
    text.write_text("A line of a long document.\n" * 300_000)
    with pytest.raises(ParseError) as caught:
        parse_within_limits(text, "long.md", ParseLimits(memory_mb=1))
    assert str(caught.value) == (
        "Parsing needs more than the 1 MB of memory allowed and was stopped. Split the file "
        "into smaller parts and upload those."
    )


def test_memory_a_library_reports_as_its_own_error_is_told_as_memory(sample_pdf, monkeypatch):
    # pdfplumber raises what pdfminer raised as its own error, a MemoryError too, so the cap
    # above ends either way, by where it is hit. The child is run here in this process.
    def parse_out_of_memory(*args, **kwargs):
        try:
            raise MemoryError
        except MemoryError as error:
            raise PdfminerException(error)  # noqa: B904, as pdfplumber raises it

    def exit_with(code):
        raise SystemExit(code)

    monkeypatch.setattr(limits, "parse_file", parse_out_of_memory)
    monkeypatch.setattr(limits.os, "_exit", exit_with)
    with pytest.raises(SystemExit) as exited:
        limits.main([str(sample_pdf), "upload.pdf"])
    assert exited.value.code == limits.OUT_OF_MEMORY


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


def test_a_file_with_more_text_than_a_document_may_hold_is_refused(sample_pdf, sample_xlsx):
    for path in (sample_pdf, sample_xlsx):
        text = sum(len(block.text) for block in parse_file(path, path.name).blocks)
        assert parse_file(path, path.name, max_chars=text).blocks
        # The child refuses it before it replies, so the worker never holds the text.
        with pytest.raises(ParseError) as caught:
            parse_within_limits(path, path.name, ParseLimits(max_chars=text - 1))
        assert str(caught.value) == (
            f"This file holds more than {text - 1:,} characters of text, the most one document "
            "can have. Split it and upload the parts."
        )


def test_a_sheet_stops_at_the_row_past_the_text_it_may_hold():
    read = []

    def rows():
        for number in range(1000):
            read.append(number)
            yield [f"row {number}", "x" * 10]

    with pytest.raises(FileTooLarge) as caught:
        sheet._trim(rows(), 0, "This file", max_chars=100)
    assert str(caught.value).startswith("This file holds more than 100 characters of text")
    # Fifteen characters a row: the seventh is past the hundred, and the rest are never read.
    assert len(read) == 7
    # The sheets before it count, at the length of their tables.
    with pytest.raises(FileTooLarge):
        sheet._trim(iter([["a", "b"]]), 0, "This file", max_chars=100, held=99)


def test_a_damaged_file_fails_with_the_parsers_error(tmp_path):
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"%PDF-1.4 but nothing of a PDF follows")
    with pytest.raises(ParseError) as caught:
        parse_within_limits(path, "broken.pdf", ParseLimits())
    assert str(caught.value) == (
        "The file could not be parsed (pdfplumber.PdfminerException). Check that it opens in "
        "another program, then upload it again."
    )


def test_a_pdf_that_needs_a_password_is_refused_and_one_that_restricts_copying_is_read(
    tmp_path,
):
    locked = protected_pdf(tmp_path / "locked.pdf", "secret")
    with pytest.raises(ParseError) as caught:
        parse_within_limits(locked, "locked.pdf", ParseLimits())
    assert str(caught.value) == (
        "This PDF is protected by a password. Save a copy without the password and upload that."
    )
    assert not caught.value.transient

    # Protected by a certificate, which pdfminer does not open, rather than by a password.
    certificate = tmp_path / "certificate.pdf"
    certificate.write_bytes(
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >> endobj\n"
        b"4 0 obj << /Filter /Adobe.PubSec /V 4 /R 4 /SubFilter /adbe.pkcs7.s5 >> endobj\n"
        b"trailer << /Root 1 0 R /Encrypt 4 0 R /ID [<0011> <0011>] >>\n%%EOF\n"
    )
    with pytest.raises(ParseError) as caught:
        parse_within_limits(certificate, "certificate.pdf", ParseLimits())
    assert str(caught.value) == (
        "This PDF's protection cannot be opened here. Save a copy without the protection and "
        "upload that."
    )

    # A password that only restricts printing and copying leaves the one to open it empty,
    # which every viewer tries; such a PDF is read like any other.
    restricted = protected_pdf(tmp_path / "restricted.pdf", "")
    parsed = parse_within_limits(restricted, "restricted.pdf", ParseLimits())
    assert parsed.page_count == 1
    assert [block.text for block in parsed.blocks] == ["Invoice 42 for the delivery of March."]


def test_a_pdf_without_pages_is_refused(tmp_path):
    empty = pageless_pdf(tmp_path / "empty.pdf")
    with pytest.raises(UnreadableFile):
        parse_file(empty, "empty.pdf")
    with pytest.raises(ParseError) as caught:
        parse_within_limits(empty, "empty.pdf", ParseLimits(max_pages=1))
    assert str(caught.value) == (
        "This PDF has no pages. Export it again from the program that made it and upload that."
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
    sample_pdf, sample_docx, sample_xlsx, sample_pptx, sample_csv, tmp_path
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
    assert wrong_type(sample_pptx, ".pptx") is None
    assert wrong_type(sample_csv, ".csv") is None
    assert wrong_type(images["PNG"], ".png") is None
    assert wrong_type(images["JPEG"], ".jpg") is None
    assert wrong_type(images["JPEG"], ".jpeg") is None
    assert wrong_type(images["WEBP"], ".webp") is None
    assert wrong_type(notes, ".txt") is None
    assert wrong_type(readme, ".md") is None
    assert wrong_type(cut, ".csv") is None
    page = tmp_path / "page.html"
    page.write_bytes('<meta charset="windows-1252"><p>Café</p>'.encode("cp1252"))
    wide = tmp_path / "wide.htm"
    wide.write_bytes("<p>A page.</p>".encode("utf-16"))
    letter = tmp_path / "letter.rtf"
    letter.write_bytes(rb"{\rtf1\ansi Dear supplier,\par}")
    # An HTML page names its encoding, so one that is not UTF-8 passes, as one in UTF-16 does.
    assert wrong_type(page, ".html") is None
    assert wrong_type(wide, ".htm") is None
    assert wrong_type(letter, ".rtf") is None


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
    assert wrong_type(sample_docx, ".pptx") == f"Not a PowerPoint presentation. {RENAME}"
    assert wrong_type(plain_zip, ".pptx") == f"Not a PowerPoint presentation. {RENAME}"
    assert wrong_type(macros, ".xlsx") == f"Not an Excel workbook. {RENAME}"
    assert wrong_type(text, ".xlsx") == f"Not an Excel workbook. {RENAME}"
    assert wrong_type(png, ".jpg") == f"Not a JPEG image. {RENAME}"
    assert wrong_type(png, ".webp") == f"Not a WebP image. {RENAME}"
    assert wrong_type(sample_pdf, ".png") == f"Not a PNG image. {RENAME}"
    assert wrong_type(binary, ".txt") == f"Not a text file. {RENAME}"
    assert wrong_type(png, ".md") == f"Not a text file. {RENAME}"
    assert wrong_type(latin, ".csv") == "Not UTF-8 text. Save it as UTF-8 and upload it again."
    assert wrong_type(utf16, ".txt") == "Not UTF-8 text. Save it as UTF-8 and upload it again."
    assert wrong_type(binary, ".html") == f"Not an HTML file. {RENAME}"
    assert wrong_type(text, ".rtf") == f"Not an RTF document. {RENAME}"
    assert wrong_type(text, ".zip") == f"Not a ZIP archive. {RENAME}"
    assert wrong_type(plain_zip, ".zip") is None
    assert wrong_type(text, ".odt") == f"Not a supported type. {RENAME}"


def test_office_files_in_the_older_container_say_how_to_make_them_pass(tmp_path):
    # A Word or Excel file saved with a password is encrypted whole into a compound file, and
    # so is neither a zip nor what python-docx or openpyxl open; so is a .doc or an .xls under
    # the newer name. Office turns each into a file that passes, so the sentence says how. The
    # big encrypted package puts the directory past what the check reads from the head, as
    # LibreOffice writes it.
    locked = {"EncryptionInfo": 0, "EncryptedPackage": HEAD + 4096}
    locked_document = compound_file(tmp_path / "locked.docx", locked)
    locked_workbook = compound_file(tmp_path / "locked.xlsx", locked)
    document = compound_file(tmp_path / "letter.doc", {"WordDocument": 4096, "1Table": 4096})
    workbook = compound_file(tmp_path / "budget.xls", {"Workbook": 4096})
    excel5 = compound_file(tmp_path / "old.xls", {"Book": 4096})
    slides = compound_file(tmp_path / "deck.ppt", {"PowerPoint Document": 4096})
    locked_slides = compound_file(tmp_path / "locked.pptx", locked)
    message = compound_file(tmp_path / "mail.msg", {"__properties_version1.0": 4096})
    # A stream's name in a document's text is no directory entry.
    mention = tmp_path / "mention.doc"
    mention.write_bytes(
        compound_file(tmp_path / "plain.doc", {"Contents": 4096}).read_bytes()
        + "Word keeps the text in WordDocument and the key in EncryptionInfo.".encode("utf-16-le")
    )

    password = "Protected by a password. Save a copy without the password and upload that."
    assert wrong_type(locked_document, ".docx") == password
    assert wrong_type(locked_workbook, ".xlsx") == password
    assert wrong_type(locked_slides, ".pptx") == password
    older_word = "An older Word document (.doc). Save it as .docx and upload that."
    older_excel = "An older Excel workbook (.xls). Save it as .xlsx and upload that."
    assert wrong_type(document, ".docx") == older_word
    assert wrong_type(workbook, ".xlsx") == older_excel
    assert wrong_type(excel5, ".xlsx") == older_excel
    older_slides = "An older PowerPoint presentation (.ppt). Save it as .pptx and upload that."
    assert wrong_type(slides, ".pptx") == older_slides
    assert wrong_type(slides, ".docx") == f"Not a Word document. {RENAME}"
    # Another program's older format, or a compound file of another kind, is a plain mismatch.
    assert wrong_type(document, ".xlsx") == f"Not an Excel workbook. {RENAME}"
    assert wrong_type(workbook, ".docx") == f"Not a Word document. {RENAME}"
    assert wrong_type(message, ".docx") == f"Not a Word document. {RENAME}"
    assert wrong_type(mention, ".docx") == f"Not a Word document. {RENAME}"
    # The other types never look for a compound file.
    assert wrong_type(locked_document, ".pdf") == f"Not a PDF file. {RENAME}"
