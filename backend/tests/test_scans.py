"""Scans that a browser does not show (app/parsing/scans.py): a TIFF of a page a frame and a
phone's HEIC photo, checked at the upload, counted by the parse child, drawn by the render child
and read by the model a page at a time, as a scanned PDF's pages are."""

from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image, TiffImagePlugin

from app.config import get_settings
from app.indexing.pipeline import process_document
from app.llm import OcrBlock
from app.models import Block, Document
from app.parsing import ParsedDocument
from app.parsing.limits import ParseError, ParseLimits, parse_within_limits
from app.parsing.render import UNDRAWABLE_SCAN
from app.parsing.signatures import OTHER_HEIF, wrong_type
from app.rendering import FOR_PANE, Renderer, RenderError
from tests.test_api import create_project, upload
from tests.test_parsing import RENAME
from tests.test_pipeline import rows
from tests.test_rendering import drawing_jobs

# A photo of 96 by 64 pixels, a black bar over a grey one, saved as HEIC by pillow-heif's
# encoder, which the app leaves out: what it decodes with, pi-heif, has none.
PHOTO = Path(__file__).parent / "fixtures" / "photo.heic"


def tiff(path: Path, *frames: Image.Image, **options) -> Path:
    frames[0].save(
        path,
        format="TIFF",
        save_all=True,
        append_images=frames[1:],
        compression="tiff_lzw",
        **options,
    )
    return path


def three_pages(path: Path) -> Path:
    """A scanner's TIFF: a page in black and white, one in grey and one in a print shop's colours,
    each of its own size, so that a drawing says which it is."""
    return tiff(
        path,
        Image.new("1", (200, 100), 1),
        Image.new("L", (50, 60), 128),
        Image.new("CMYK", (30, 40), (0, 0, 0, 0)),
    )


def drawn(png: bytes) -> Image.Image:
    image = Image.open(BytesIO(png))
    assert image.format == "PNG"
    return image


@pytest.fixture
def renderer():
    made = Renderer(1, 1024)
    yield made
    made.close()


def test_a_tiff_is_a_page_a_frame_and_a_heic_photo_a_page(tmp_path):
    scan = three_pages(tmp_path / "fax.tiff")
    assert parse_within_limits(scan, "fax.tiff", ParseLimits()) == ParsedDocument(
        blocks=[],
        page_count=3,
        page_sizes=[(200.0, 100.0), (50.0, 60.0), (30.0, 40.0)],
        ocr_pages=[1, 2, 3],
    )
    assert parse_within_limits(PHOTO, "photo.heic", ParseLimits()) == ParsedDocument(
        blocks=[], page_count=1, page_sizes=[(96.0, 64.0)], ocr_pages=[1]
    )
    # The pane lays a page out by its size upright, as it is drawn.
    turned = TiffImagePlugin.ImageFileDirectory_v2()
    turned[274] = 6
    sideways = tiff(tmp_path / "sideways.tif", Image.new("L", (200, 100)), tiffinfo=turned)
    assert parse_within_limits(sideways, "sideways.tif", ParseLimits()).page_sizes == [
        (100.0, 200.0)
    ]


def test_a_scan_with_too_many_pages_or_a_page_too_large_is_refused(tmp_path):
    with pytest.raises(ParseError) as refused:
        parse_within_limits(three_pages(tmp_path / "fax.tif"), "fax.tif", ParseLimits(max_pages=2))
    assert str(refused.value) == (
        "This file has 3 pages, more than the 2 that can be indexed. Split it and upload the parts."
    )
    # A page past the first is checked too, from its header, before the model reads any.
    poster = tiff(
        tmp_path / "poster.tif", Image.new("1", (10, 10), 1), Image.new("1", (8000, 7000))
    )
    with pytest.raises(ParseError) as refused:
        parse_within_limits(poster, "poster.tif", ParseLimits())
    assert str(refused.value) == (
        "This image is 56 megapixels, and images over 50 megapixels cannot be read. Scale it down "
        "and upload it again."
    )


def test_a_scans_bytes_are_checked_at_the_upload(tmp_path):
    scan = three_pages(tmp_path / "fax.tif")
    big = tmp_path / "big.tif"
    Image.new("L", (4, 4)).save(big, format="TIFF", big_tiff=True)
    png = tmp_path / "photo.png"
    Image.new("RGB", (4, 4)).save(png)
    # An AVIF image under a HEIF name: an ISO media file, but coded in AV1, which libheif's
    # HEVC decoder does not read.
    avif = tmp_path / "photo.heif"
    avif.write_bytes(b"\x00\x00\x00\x1cftypavif\x00\x00\x00\x00avifmif1miaf" + b"\x00" * 64)

    for suffix in (".tif", ".tiff"):
        assert wrong_type(scan, suffix) is None
        assert wrong_type(big, suffix) is None
    for suffix in (".heic", ".heif"):
        assert wrong_type(PHOTO, suffix) is None
    assert wrong_type(png, ".tiff") == f"Not a TIFF image. {RENAME}"
    assert wrong_type(png, ".heic") == f"Not a HEIC image. {RENAME}"
    assert wrong_type(png, ".heif") == f"Not a HEIF image. {RENAME}"
    assert wrong_type(avif, ".heif") == OTHER_HEIF
    assert wrong_type(PHOTO, ".png") == f"Not a PNG image. {RENAME}"


def test_a_scans_pages_are_drawn_upright_in_colours_a_png_holds(renderer, tmp_path):
    scan = three_pages(tmp_path / "fax.tif")
    first = drawn(renderer.render(scan, 1, purpose=FOR_PANE))
    assert (first.size, first.mode) == ((200, 100), "L")
    assert first.getpixel((0, 0)) == 255
    assert drawn(renderer.render(scan, 2, purpose=FOR_PANE)).size == (50, 60)
    third = drawn(renderer.render(scan, 3, purpose=FOR_PANE))
    assert (third.size, third.mode) == ((30, 40), "RGB")
    # No larger than asked, as a thumbnail is.
    assert drawn(renderer.render(scan, 1, purpose=FOR_PANE, scale=None, edge=50)).size == (50, 25)
    photo = drawn(renderer.render(PHOTO, 1, purpose=FOR_PANE))
    assert (photo.size, photo.mode) == ((96, 64), "RGB")
    assert photo.getpixel((10, 10)) == (0, 0, 0)

    # A page scanned on its side is turned as its tag says; sixteen bits of grey are scaled to
    # eight, not clipped to white.
    turned = TiffImagePlugin.ImageFileDirectory_v2()
    turned[274] = 6
    sideways = tiff(tmp_path / "sideways.tif", Image.new("L", (200, 100)), tiffinfo=turned)
    assert drawn(renderer.render(sideways, 1, purpose=FOR_PANE)).size == (100, 200)
    deep = tiff(tmp_path / "deep.tif", Image.new("I;16", (4, 4), 40000))
    assert drawn(renderer.render(deep, 1, purpose=FOR_PANE)).getpixel((0, 0)) == 156


def test_a_scans_page_that_cannot_be_drawn_is_refused_saying_why(renderer, tmp_path):
    scan = three_pages(tmp_path / "fax.tif")
    damaged = tmp_path / "damaged.tif"
    damaged.write_bytes(b"II*\x00" + b"\xff" * 64)
    for path, page, message in (
        (scan, 4, "This file has no page 4."),
        (damaged, 1, UNDRAWABLE_SCAN),
    ):
        with pytest.raises(RenderError) as refused:
            renderer.render(path, page, purpose=FOR_PANE)
        assert (str(refused.value), refused.value.outcome) == (message, "refused")
    assert drawn(renderer.render(scan, 2, purpose=FOR_PANE)).size == (50, 60)


def test_a_tiffs_pages_are_read_by_the_model_one_by_one_and_kept_in_order(
    session, services, make_document, tmp_path, monkeypatch
):
    sizes = {(200, 100): 1, (50, 60): 2, (30, 40): 3}
    sent = []

    def ocr_page(image, media_type, usage=None):
        # Each page as a PNG of its own, which the model takes, not the TIFF.
        page = sizes[drawn(image).size]
        sent.append((page, media_type))
        return [OcrBlock(kind="paragraph", level=None, text=f"Page {page} of the fax")]

    monkeypatch.setattr(services.llm, "ocr_page", ocr_page)
    document = make_document(three_pages(tmp_path / "fax.tiff"))
    assert process_document(document.id, services) == []
    session.refresh(document)

    assert document.status == "ready"
    assert (document.mime_type, document.page_count) == ("image/tiff", 3)
    assert sorted(sent) == [(1, "image/png"), (2, "image/png"), (3, "image/png")]
    assert [(block.text, block.page) for block in rows(session, Block, document)] == [
        ("Page 1 of the fax", 1),
        ("Page 2 of the fax", 2),
        ("Page 3 of the fax", 3),
    ]
    assert document.usage["upload"]["pages"] == 3
    assert document.usage["ocr"]["pages"] == 3


def test_a_scan_is_shown_by_its_pages_and_downloaded_as_it_was_sent(
    client, session, services, tmp_path, monkeypatch
):
    monkeypatch.setattr(get_settings(), "prerender_pages", 2)
    project = create_project(client)
    result = upload(client, project["id"], three_pages(tmp_path / "fax.tiff"), PHOTO)
    scan, photo = result["documents"]
    assert (scan["mime_type"], photo["mime_type"]) == ("image/tiff", "image/heic")
    for document in (scan, photo):
        assert process_document(document["id"], services) == []
    # Its first pages are drawn ahead, as a PDF's are.
    assert [job.payload["pages"] for job in drawing_jobs(session)] == [[1, 2], [1]]

    page = client.get(f"/api/documents/{scan['id']}/pages/2.png")
    assert page.status_code == 200 and page.headers["content-type"] == "image/png"
    assert drawn(page.content).size == (50, 60)
    assert client.get(f"/api/documents/{scan['id']}/pages/4.png").status_code == 404
    assert drawn(client.get(f"/api/documents/{photo['id']}/pages/1.png").content).size == (96, 64)
    thumbnail = client.get(f"/api/documents/{photo['id']}/thumbnail.png")
    assert drawn(thumbnail.content).size == (96, 64)

    # A browser shows neither, so the original is a download, not an image to open.
    original = client.get(f"/api/documents/{photo['id']}/file")
    assert original.content == PHOTO.read_bytes()
    assert original.headers["content-type"] == "image/heic"
    assert original.headers["content-disposition"].startswith("attachment")
    assert session.get(Document, photo["id"]).page_count == 1
    content = client.get(f"/api/documents/{scan['id']}/content").json()
    assert content["page_sizes"] == [[200, 100], [50, 60], [30, 40]]
