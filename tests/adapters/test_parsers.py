import pytest

from kilnworks.adapters.sources.parsers import SUPPORTED_SUFFIXES, parse_file


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


def test_parses_markdown_and_text_verbatim(tmp_path):
    (tmp_path / "a.md").write_text("# Title\n\nBody.")
    assert parse_file(tmp_path / "a.md") == "# Title\n\nBody."


def test_parses_pdf_text(tmp_path):
    _make_pdf(tmp_path / "doc.pdf", "Stoneware fires at 1300 degrees")
    text = parse_file(tmp_path / "doc.pdf")
    assert "Stoneware fires at 1300 degrees" in text


def test_parses_docx_paragraphs(tmp_path):
    _make_docx(tmp_path / "doc.docx", ["First paragraph.", "", "Second paragraph."])
    text = parse_file(tmp_path / "doc.docx")
    assert "First paragraph." in text and "Second paragraph." in text
    assert "\n\n" in text


def test_parses_html_and_strips_scripts(tmp_path):
    (tmp_path / "page.html").write_text(
        "<html><head><script>evil()</script><style>x{}</style></head>"
        "<body><h1>Guide</h1><p>Kilns are hot.</p></body></html>"
    )
    text = parse_file(tmp_path / "page.html")
    assert "Guide" in text and "Kilns are hot." in text
    assert "evil()" not in text and "x{}" not in text


def test_corrupt_pdf_raises(tmp_path):
    (tmp_path / "bad.pdf").write_bytes(b"%PDF-not really a pdf")
    with pytest.raises(Exception):
        parse_file(tmp_path / "bad.pdf")


def test_unsupported_suffix_raises_value_error(tmp_path):
    (tmp_path / "img.png").write_bytes(b"\x89PNG")
    with pytest.raises(ValueError, match="unsupported"):
        parse_file(tmp_path / "img.png")


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
