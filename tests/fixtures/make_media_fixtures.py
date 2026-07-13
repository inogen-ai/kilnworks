"""Generate the tiny image/audio fixtures used by media-extraction tests.

Run once with `uv run python tests/fixtures/make_media_fixtures.py`; the outputs
(sample.png, sample.wav) are committed so tests don't need to regenerate them on
every run. Both are built from the standard library only (no Pillow/audio-lib
dependency) since M6 Task 2 doesn't add any media-decoding dependency yet.
"""

import struct
import wave
import zlib
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent


def make_png() -> None:
    """A minimal valid 1x1 grayscale PNG."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0)  # 1x1, 8-bit grayscale
    raw_scanline = b"\x00\x80"  # filter byte + single gray pixel
    idat = zlib.compress(raw_scanline)
    png = signature + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")
    (FIXTURES_DIR / "sample.png").write_bytes(png)


def make_wav() -> None:
    """A minimal valid mono 16-bit WAV with a single silent sample."""
    path = FIXTURES_DIR / "sample.wav"
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\x00\x00")


if __name__ == "__main__":
    make_png()
    make_wav()
    print("wrote sample.png, sample.wav")
