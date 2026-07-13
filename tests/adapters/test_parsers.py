from pathlib import Path

import pytest

from kilnworks.adapters.media.fake import FakeTranscriber, FakeVisionExtractor
from kilnworks.adapters.sources.parsers import (
    IMAGE_SUFFIXES,
    MEDIA_SUFFIXES,
    SUPPORTED_SUFFIXES,
    MediaProviderRequired,
    parse_file,
)
from kilnworks.core.models import Completion
from kilnworks.core.ports import MediaExtractor

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


def _make_pdf(path, text):
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    pdf = canvas.Canvas(str(path), pagesize=letter)
    pdf.drawString(72, 720, text)
    pdf.showPage()
    pdf.save()


def _make_docx(path, paragraphs):
    import docx

    document = docx.Document()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    document.save(str(path))


def test_supported_suffixes_cover_the_m3_formats():
    assert {".md", ".txt", ".pdf", ".docx", ".html", ".htm"} <= SUPPORTED_SUFFIXES


def test_supported_suffixes_cover_the_m6_table_formats():
    assert {".csv", ".tsv", ".xlsx"} <= SUPPORTED_SUFFIXES


def test_parses_markdown_and_text_verbatim(tmp_path):
    (tmp_path / "a.md").write_text("# Title\n\nBody.")
    assert parse_file(tmp_path / "a.md").text == "# Title\n\nBody."


def test_text_parse_has_empty_extraction_usage(tmp_path):
    (tmp_path / "a.md").write_text("no media here")
    assert parse_file(tmp_path / "a.md").usage == []


def test_parses_pdf_text(tmp_path):
    _make_pdf(tmp_path / "doc.pdf", "Stoneware fires at 1300 degrees")
    text = parse_file(tmp_path / "doc.pdf").text
    assert "Stoneware fires at 1300 degrees" in text


def test_parses_docx_paragraphs(tmp_path):
    _make_docx(tmp_path / "doc.docx", ["First paragraph.", "", "Second paragraph."])
    text = parse_file(tmp_path / "doc.docx").text
    assert "First paragraph." in text and "Second paragraph." in text
    assert "\n\n" in text


def test_parses_html_and_strips_scripts(tmp_path):
    (tmp_path / "page.html").write_text(
        "<html><head><script>evil()</script><style>x{}</style></head>"
        "<body><h1>Guide</h1><p>Kilns are hot.</p></body></html>"
    )
    text = parse_file(tmp_path / "page.html").text
    assert "Guide" in text and "Kilns are hot." in text
    assert "evil()" not in text and "x{}" not in text


def test_corrupt_pdf_raises(tmp_path):
    (tmp_path / "bad.pdf").write_bytes(b"%PDF-not really a pdf")
    with pytest.raises(Exception):
        parse_file(tmp_path / "bad.pdf")


def test_unsupported_suffix_raises_value_error(tmp_path):
    (tmp_path / "notes.xyz").write_bytes(b"whatever")
    with pytest.raises(ValueError, match="unsupported"):
        parse_file(tmp_path / "notes.xyz")


def test_oversize_extracted_text_is_rejected(tmp_path):
    (tmp_path / "huge.txt").write_text("x" * (10_000_001))
    with pytest.raises(ValueError, match="too large"):
        parse_file(tmp_path / "huge.txt")


def test_docx_over_cap_is_rejected(tmp_path, monkeypatch):
    from kilnworks.adapters.sources import parsers

    monkeypatch.setattr(parsers, "MAX_TEXT_CHARS", 100)
    _make_docx(tmp_path / "big.docx", ["paragraph " * 30] * 3)
    with pytest.raises(ValueError, match="too large"):
        parse_file(tmp_path / "big.docx")


def test_parses_csv_with_header_labels(tmp_path):
    text = parse_file(FIXTURES_DIR / "sample.csv").text
    assert "name: Ada" in text and "age: 30" in text and "city: London" in text
    assert "name: Grace" in text and "city: New York" in text


def test_csv_skips_blank_cells(tmp_path):
    text = parse_file(FIXTURES_DIR / "sample.csv").text
    # Alan's city cell is blank in the fixture, so no "city:" is emitted for that row.
    alan_line = next(line for line in text.splitlines() if "name: Alan" in line)
    assert "city" not in alan_line


def test_parses_tsv_with_tab_delimiter(tmp_path):
    text = parse_file(FIXTURES_DIR / "sample.tsv").text
    assert "name: Mug" in text and "color: Blue" in text
    assert "name: Bowl" in text and "color: Green" in text


def test_parses_xlsx_both_sheets_with_formula_value(tmp_path):
    text = parse_file(FIXTURES_DIR / "sample.xlsx").text
    assert "# Sheet: Orders" in text
    assert "# Sheet: Notes" in text
    # Total is a formula cell (=B2*C2); data_only reads its cached value, not "=B2*C2".
    assert "Total: 36" in text
    assert "=B2*C2" not in text
    assert "Field: Kiln" in text and "Value: Cone 6" in text


def test_csv_empty_rows_are_skipped(tmp_path):
    (tmp_path / "gaps.csv").write_text("a,b\n1,2\n\n,\n3,4\n", encoding="utf-8")
    text = parse_file(tmp_path / "gaps.csv").text
    lines = text.splitlines()
    assert len(lines) == 2
    assert lines == ["a: 1 | b: 2", "a: 3 | b: 4"]


def test_csv_falls_back_to_positional_labels_for_blank_header_cells(tmp_path):
    (tmp_path / "blank_header.csv").write_text(",b\nx,y\n", encoding="utf-8")
    text = parse_file(tmp_path / "blank_header.csv").text
    assert "col1: x" in text
    assert "b: y" in text


def test_xlsx_over_cap_is_rejected(tmp_path, monkeypatch):
    from kilnworks.adapters.sources import parsers

    monkeypatch.setattr(parsers, "MAX_TEXT_CHARS", 5)
    with pytest.raises(ValueError, match="too large"):
        parse_file(FIXTURES_DIR / "sample.xlsx")


def test_csv_with_unsniffable_content_falls_back_to_comma(tmp_path):
    """A single-column or prose-like CSV makes csv.Sniffer raise; the parser must fall
    back to comma rather than crash (regression — this path had no coverage)."""
    single_col = tmp_path / "one.csv"
    single_col.write_text("Item\nMug\nBowl\n")
    text = parse_file(single_col).text
    assert "Mug" in text and "Bowl" in text  # parsed, no exception


# --- M6 Task 2: media extraction ---------------------------------------------------


def test_supported_suffixes_cover_the_m6_media_formats():
    assert {".png", ".jpg", ".jpeg", ".gif", ".webp"} <= SUPPORTED_SUFFIXES
    assert {".mp3", ".wav", ".m4a", ".mp4", ".mov"} <= SUPPORTED_SUFFIXES
    assert IMAGE_SUFFIXES <= SUPPORTED_SUFFIXES
    assert MEDIA_SUFFIXES <= SUPPORTED_SUFFIXES


def test_image_with_fake_vision_returns_description():
    vision = FakeVisionExtractor(reply="A tiny gray square.")
    media = MediaExtractor(vision=vision)
    parsed = parse_file(FIXTURES_DIR / "sample.png", media=media)
    assert parsed.text == "A tiny gray square."
    png_size = (FIXTURES_DIR / "sample.png").stat().st_size
    assert vision.calls == [("image/png", "sample.png", png_size)]
    assert parsed.usage == [Completion(text="A tiny gray square.", model="fake",
                                        input_tokens=1, output_tokens=4, context="vision")]


def test_audio_with_fake_transcriber_returns_transcript():
    transcriber = FakeTranscriber(reply="Hello from the kiln.")
    media = MediaExtractor(transcription=transcriber)
    parsed = parse_file(FIXTURES_DIR / "sample.wav", media=media)
    assert parsed.text == "Hello from the kiln."
    assert transcriber.calls == [
        ("audio/wav", "sample.wav", (FIXTURES_DIR / "sample.wav").stat().st_size)
    ]
    assert len(parsed.usage) == 1 and parsed.usage[0].model == "fake"
    assert parsed.usage[0].context == "transcription"  # not "vision" — M6 Task 4 fix


def test_video_with_fake_transcriber_extracts_audio_first(tmp_path, monkeypatch):
    """Video suffixes must run through `extract_audio` before hitting the
    transcriber — the transcriber should only ever see audio bytes/mime."""
    from kilnworks.adapters.sources import parsers

    calls = {}

    def fake_extract_audio(media_bytes, suffix):
        calls["args"] = (media_bytes, suffix)
        return b"FAKE-WAV-BYTES"

    monkeypatch.setattr(parsers, "extract_audio", fake_extract_audio)

    transcriber = FakeTranscriber(reply="Video transcript.")
    media = MediaExtractor(transcription=transcriber)
    video_path = tmp_path / "clip.mp4"
    video_bytes = b"not really an mp4 but bytes"
    video_path.write_bytes(video_bytes)

    parsed = parse_file(video_path, media=media)

    assert parsed.text == "Video transcript."
    assert calls["args"] == (video_bytes, ".mp4")
    # The name handed to the transcriber must be .wav (matching the ffmpeg-extracted
    # WAV bytes), NOT clip.mp4 — OpenAI's transcription endpoint 400s a WAV body sent
    # under a video filename whose extension isn't in its allowed set (e.g. .mov).
    assert transcriber.calls == [("audio/wav", "clip.wav", len(b"FAKE-WAV-BYTES"))]
    assert parsed.usage[0].context == "transcription"


def test_image_without_media_raises_media_provider_required():
    with pytest.raises(MediaProviderRequired, match="KILNWORKS_VISION_PROVIDER"):
        parse_file(FIXTURES_DIR / "sample.png")


def test_image_with_media_but_no_vision_raises_media_provider_required():
    with pytest.raises(MediaProviderRequired, match="KILNWORKS_VISION_PROVIDER"):
        parse_file(FIXTURES_DIR / "sample.png", media=MediaExtractor())


def test_audio_without_media_raises_media_provider_required():
    with pytest.raises(MediaProviderRequired, match="KILNWORKS_TRANSCRIPTION_PROVIDER"):
        parse_file(FIXTURES_DIR / "sample.wav")


def test_media_provider_required_message_names_the_suffix():
    with pytest.raises(MediaProviderRequired, match=r"\.png files requires"):
        parse_file(FIXTURES_DIR / "sample.png")


def test_media_over_max_bytes_is_rejected(tmp_path):
    (tmp_path / "big.png").write_bytes(b"\x89PNG" + b"x" * 20)
    media = MediaExtractor(vision=FakeVisionExtractor(), max_bytes=10)
    with pytest.raises(ValueError, match="KILNWORKS_MAX_MEDIA_BYTES"):
        parse_file(tmp_path / "big.png", media=media)


def test_media_within_max_bytes_is_accepted(tmp_path):
    small = tmp_path / "small.png"
    small.write_bytes((FIXTURES_DIR / "sample.png").read_bytes())
    media = MediaExtractor(vision=FakeVisionExtractor(), max_bytes=small.stat().st_size)
    assert parse_file(small, media=media).text  # exactly at the cap: not rejected
