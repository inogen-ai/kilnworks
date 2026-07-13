import io

import pytest
from PIL import Image

from kilnworks.adapters.media.imaging import MAX_DIMENSION, InvalidImageError, normalize_image


def _png_bytes(width: int, height: int) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color=(120, 60, 200)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_small_image_is_normalized_to_jpeg_unchanged_dimensions():
    encoded, mime = normalize_image(_png_bytes(1, 1), "tiny.png")
    assert mime == "image/jpeg"
    with Image.open(io.BytesIO(encoded)) as img:
        assert img.format == "JPEG"
        assert img.size == (1, 1)
        assert img.mode == "RGB"


def test_oversized_image_is_downscaled_to_max_dimension():
    encoded, _ = normalize_image(_png_bytes(3000, 1000), "wide.png")
    with Image.open(io.BytesIO(encoded)) as img:
        assert max(img.size) == MAX_DIMENSION
        assert img.size[0] == MAX_DIMENSION  # width was the longer side
        assert img.size[1] < 1000  # aspect ratio preserved, height shrank too


def test_image_at_exactly_the_cap_is_not_shrunk_further():
    encoded, _ = normalize_image(_png_bytes(MAX_DIMENSION, MAX_DIMENSION), "square.png")
    with Image.open(io.BytesIO(encoded)) as img:
        assert img.size == (MAX_DIMENSION, MAX_DIMENSION)


def test_non_image_bytes_raise_invalid_image_error():
    with pytest.raises(InvalidImageError, match="not a valid image"):
        normalize_image(b"this is not an image, just text bytes", "notes.txt")


def test_invalid_image_error_names_the_file():
    with pytest.raises(InvalidImageError, match="corrupt.png"):
        normalize_image(b"\x89PNGnope", "corrupt.png")
