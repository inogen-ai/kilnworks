from kilnworks.adapters.sources.localfolder import LocalFolderSource
from kilnworks.core.models import Document, SourceFailure


def test_yields_supported_types_recursively_sorted(tmp_path):
    (tmp_path / "b.md").write_text("# B\n\nbeta")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.txt").write_text("alpha")
    (tmp_path / "page.html").write_text("<p>hyper text</p>")
    (tmp_path / "skip.png").write_bytes(b"\x89PNG")
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
