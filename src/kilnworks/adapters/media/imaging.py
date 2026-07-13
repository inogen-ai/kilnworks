import io

from PIL import Image, ImageOps, UnidentifiedImageError

# Longest-side cap in pixels. Vision providers bill roughly by image tile count,
# so downscaling oversized images before sending them bounds per-call cost without
# materially hurting description/OCR quality for typical document scans/photos.
MAX_DIMENSION = 1536


class InvalidImageError(ValueError):
    """Raised when bytes handed to a vision adapter don't decode as an image.

    A `ValueError` subclass so it propagates unchanged through `parse_file` and
    is caught by the sources' generic `except Exception` handlers, becoming a
    per-file `SourceFailure` rather than aborting the whole ingest batch.
    """


def normalize_image(image: bytes, name: str) -> tuple[bytes, str]:
    """Verify `image` decodes as a real image, convert to RGB, downscale so the
    longest side is at most `MAX_DIMENSION`, and re-encode as JPEG.

    Returns `(encoded_bytes, mime_type)`. Raises `InvalidImageError` for bytes
    that don't decode as an image (corrupt file, wrong extension, etc.).
    """
    # Image.DecompressionBombError is an Exception, not OSError/ValueError, so it must
    # be named explicitly or a bomb-sized image escapes as a raw Pillow error instead
    # of our friendly InvalidImageError.
    try:
        with Image.open(io.BytesIO(image)) as probe:
            probe.verify()  # cheap structural check; the image object is unusable after this
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise InvalidImageError(f"{name!r} is not a valid image: {exc}") from exc

    try:
        with Image.open(io.BytesIO(image)) as img:
            # Apply the EXIF orientation to the pixels: phone photos of documents very
            # commonly carry a non-default Orientation tag, and re-encoding as JPEG drops
            # EXIF — without this the vision model would see the page sideways and its
            # verbatim text transcription would suffer.
            oriented = ImageOps.exif_transpose(img)
            rgb = oriented.convert("RGB")
            rgb.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.LANCZOS)
            buffer = io.BytesIO()
            rgb.save(buffer, format="JPEG", quality=90)
            return buffer.getvalue(), "image/jpeg"
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise InvalidImageError(f"{name!r} could not be processed as an image: {exc}") from exc
