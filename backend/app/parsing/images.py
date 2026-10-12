"""Opening an uploaded image: the pixel cap that bounds what decoding one costs in memory."""

from pathlib import Path

from PIL import Image

from app.parsing.types import FileTooLarge

# The most pixels an uploaded image may have. Decoding takes up to four bytes a pixel, in the
# API process for a thumbnail and in the worker for OCR, so the cap bounds that at 200 MB while
# taking a phone photo or an A3 page scanned at 500 dpi. It must stay under twice Pillow's own
# ceiling (`Image.MAX_IMAGE_PIXELS`), where Pillow refuses to open the file at all, or the
# message would name a limit that is not the one in force.
MAX_IMAGE_PIXELS = 50_000_000


def open_image(path: Path) -> Image.Image:
    """Open an image the process can afford to decode, or refuse it with `FileTooLarge`.

    `Image.open` reads only the header, so the size is checked before a pixel is decoded, and
    the image comes back lazy like `Image.open`'s: use it as a context manager.
    """
    try:
        image = Image.open(path)
    except Image.DecompressionBombError as error:
        # Pillow refused before the size was known; its ceiling is above the cap.
        raise FileTooLarge(
            f"This image is over {_megapixels(MAX_IMAGE_PIXELS)} megapixels, more than can be "
            "read. Scale it down and upload it again."
        ) from error
    pixels = image.width * image.height
    if pixels > MAX_IMAGE_PIXELS:
        image.close()
        raise FileTooLarge(
            f"This image is {_megapixels(pixels)} megapixels, and images over "
            f"{_megapixels(MAX_IMAGE_PIXELS)} megapixels cannot be read. Scale it down and "
            "upload it again."
        )
    return image


def _megapixels(pixels: int) -> str:
    return f"{pixels / 1_000_000:,.1f}".removesuffix(".0")
