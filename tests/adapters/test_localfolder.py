from pathlib import Path

from kilnworks.adapters.media.fake import FakeTranscriber, FakeVisionExtractor
from kilnworks.adapters.sources.localfolder import LocalFolderSource
from kilnworks.core.models import Document, SourceFailure
from kilnworks.core.ports import MediaExtractor

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


def test_yields_supported_types_recursively_sorted(tmp_path):
    (tmp_path / "b.md").write_text("# B\n\nbeta")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.txt").write_text("alpha")
    (tmp_path / "page.html").write_text("<p>hyper text</p>")
    # .exe is genuinely unsupported (unlike .png, which M6 now attempts and surfaces
    # as a friendly SourceFailure rather than silently skipping — see the media tests
    # below) so it's the right fixture for "walk ignores what it doesn't recognize".
    (tmp_path / "skip.exe").write_bytes(b"MZ")
    items = list(LocalFolderSource(tmp_path).documents())
    assert all(isinstance(item, Document) for item in items)
    assert [item.title for item in items] == ["b", "page", "a"]
    assert "hyper text" in items[1].text


def test_bad_file_yields_failure_and_iteration_continues(tmp_path):
    (tmp_path / "a-bad.pdf").write_bytes(b"%PDF-not really")
    (tmp_path / "b-good.md").write_text("still here")
    items = list(LocalFolderSource(tmp_path).documents())
    assert isinstance(items[0], SourceFailure)
    assert items[0].source_uri.endswith("a-bad.pdf")
    assert isinstance(items[1], Document)
    assert items[1].text == "still here"


def test_non_utf8_text_file_yields_failure_not_crash(tmp_path):
    (tmp_path / "bad.md").write_bytes(b"\xff\xfe broken")
    (tmp_path / "good.md").write_text("fine")
    items = list(LocalFolderSource(tmp_path).documents())
    kinds = [type(item).__name__ for item in items]
    assert kinds == ["SourceFailure", "Document"]


def test_acl_tags_are_applied(tmp_path):
    (tmp_path / "secret.md").write_text("classified")
    docs = list(LocalFolderSource(tmp_path, acl_tags=("hr",)).documents())
    assert docs[0].acl_tags == ["hr"]


# --- M6 Task 2: media extraction ---------------------------------------------------


def test_image_and_audio_ingest_via_fake_providers(tmp_path):
    (tmp_path / "photo.png").write_bytes((FIXTURES_DIR / "sample.png").read_bytes())
    (tmp_path / "clip.wav").write_bytes((FIXTURES_DIR / "sample.wav").read_bytes())
    media = MediaExtractor(vision=FakeVisionExtractor(), transcription=FakeTranscriber())
    items = {item.title: item for item in LocalFolderSource(tmp_path, media=media).documents()}
    assert all(isinstance(item, Document) for item in items.values())
    assert items["photo"].text == "A fake image description."
    assert items["clip"].text == "This is a fake transcript."
    assert len(items["photo"].extraction_usage) == 1


def test_image_without_provider_fails_but_sibling_still_succeeds(tmp_path):
    """Per-file isolation: an unconfigured media file becomes a friendly SourceFailure
    while the rest of the batch keeps ingesting."""
    (tmp_path / "photo.png").write_bytes((FIXTURES_DIR / "sample.png").read_bytes())
    (tmp_path / "notes.md").write_text("still here")
    items = list(LocalFolderSource(tmp_path).documents())  # no media configured
    by_kind = {type(item).__name__: item for item in items}
    assert isinstance(by_kind["SourceFailure"], SourceFailure)
    assert "KILNWORKS_VISION_PROVIDER" in by_kind["SourceFailure"].error
    assert by_kind["SourceFailure"].source_uri.endswith("photo.png")
    assert isinstance(by_kind["Document"], Document)
    assert by_kind["Document"].text == "still here"


def test_oversize_media_fails_but_sibling_still_succeeds(tmp_path):
    (tmp_path / "photo.png").write_bytes((FIXTURES_DIR / "sample.png").read_bytes())
    (tmp_path / "notes.md").write_text("still here")
    media = MediaExtractor(vision=FakeVisionExtractor(), max_bytes=1)
    items = list(LocalFolderSource(tmp_path, media=media).documents())
    by_kind = {type(item).__name__: item for item in items}
    assert "KILNWORKS_MAX_MEDIA_BYTES" in by_kind["SourceFailure"].error
    assert by_kind["Document"].text == "still here"
