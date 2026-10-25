"""TIFF and HEIC: scans and photos that a browser does not show, read and drawn as pages by the
parse and render children alone.

A scanner's or a fax's TIFF holds a page a frame, and a phone's HEIC photo one image, so each is
a document of pages, as a scanned PDF is: the parse child counts its frames, which the pipeline
sends to the model as pages to read (`_with_ocr` in app/indexing/pipeline.py), and the render
child draws a frame as a PNG for the document pane, the thumbnail and the model, none of which
takes either format as it is. Decoding them stays in those children, never in the API or the
worker: libtiff and libheif read what anyone with an account uploads, inside limits only a
child process has. This module registers the HEIF opener with Pillow as it is imported, so the
children alone import it (`PARSER_MODULES`, app/parsing/render.py).
"""

from pathlib import Path

import pi_heif
from PIL import Image, ImageOps

from app.parsing.images import check_pixels, open_image
from app.parsing.types import FileTooLarge, ParsedDocument

pi_heif.register_heif_opener()


def parse_scan(path: Path, max_pages: int = 0) -> ParsedDocument:
    """The scan's pages, a frame each, every one to be read by the model, with their sizes in
    pixels as they stand upright, which the document pane lays them out by. One with more than
    `max_pages` pages (0: any number) is refused, as a PDF is, and so is one with a frame larger
    than an image may be, before the model is asked to read any."""
    sizes: list[tuple[float, float]] = []
    with open_image(path) as image:
        count = frames(image)
        if max_pages and count > max_pages:
            raise FileTooLarge(
                f"This file has {count:,} pages, more than the {max_pages:,} that can be "
                "indexed. Split it and upload the parts."
            )
        for index in range(count):
            image.seek(index)
            check_pixels(image)
            # Pillow gives a TIFF's size turned as its orientation tag says, and pi-heif a
            # photo's as its transformations do, as `upright` draws them.
            sizes.append((float(image.width), float(image.height)))
    pages = list(range(1, count + 1))
    return ParsedDocument(blocks=[], page_count=count, page_sizes=sizes, ocr_pages=pages)


def frames(image: Image.Image) -> int:
    """How many pages the open scan holds."""
    return getattr(image, "n_frames", 1)


def upright(image: Image.Image) -> Image.Image:
    """The frame the open scan is at, turned as its orientation tag says, in a mode a PNG holds
    and shrinking keeps legible: a fax's single bit and a scanner's sixteen as eight-bit grey,
    which a plain conversion would clip to white, and a print shop's CMYK as RGB."""
    frame = ImageOps.exif_transpose(image)
    if frame.mode == "1":
        return frame.convert("L")
    if frame.mode.startswith("I;16"):
        return frame.convert("I").point(lambda value: value / 256).convert("L")
    if frame.mode not in ("L", "LA", "RGB", "RGBA"):
        alpha = "A" in frame.mode or "transparency" in frame.info
        return frame.convert("RGBA" if alpha else "RGB")
    return frame
