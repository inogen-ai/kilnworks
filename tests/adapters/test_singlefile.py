from pathlib import Path

from kilnworks.adapters.media.fake import FakeTranscriber, FakeVisionExtractor
from kilnworks.adapters.sources.singlefile import SingleFileSource
from kilnworks.core.models import Document, SourceFailure
from kilnworks.core.ports import MediaExtractor

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


def test_yields_a_single_document(tmp_path):
    (tmp_path / "note.md").write_text("hello")
    items = list(SingleFileSource(tmp_path / "note.md").documents())
    assert len(items) == 1
    assert isinstance(items[0], Document)
    assert items[0].text == "hello"
    assert items[0].extraction_usage == []


def test_image_ingests_via_fake_vision(tmp_path):
    dest = tmp_path / "upload.png"
    dest.write_bytes((FIXTURES_DIR / "sample.png").read_bytes())
    media = MediaExtractor(vision=FakeVisionExtractor(reply="a kiln photo"))
    items = list(SingleFileSource(dest, title="upload", media=media).documents())
    assert len(items) == 1
    assert isinstance(items[0], Document)
    assert items[0].text == "a kiln photo"
    assert len(items[0].extraction_usage) == 1
    assert items[0].extraction_usage[0].model == "fake"


def test_audio_ingests_via_fake_transcriber(tmp_path):
    dest = tmp_path / "upload.wav"
    dest.write_bytes((FIXTURES_DIR / "sample.wav").read_bytes())
    media = MediaExtractor(transcription=FakeTranscriber(reply="a kiln recording"))
    items = list(SingleFileSource(dest, title="upload", media=media).documents())
    assert len(items) == 1
    assert isinstance(items[0], Document)
    assert items[0].text == "a kiln recording"


def test_image_without_provider_yields_actionable_failure(tmp_path):
    dest = tmp_path / "upload.png"
    dest.write_bytes((FIXTURES_DIR / "sample.png").read_bytes())
    items = list(SingleFileSource(dest, title="upload").documents())  # no media
    assert len(items) == 1
    assert isinstance(items[0], SourceFailure)
    assert "KILNWORKS_VISION_PROVIDER" in items[0].error
