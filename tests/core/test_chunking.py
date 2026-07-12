import pytest

from kilnworks.core.chunking import HeadingAwareChunker


def test_heading_path_is_tracked():
    text = "# Guide\n\nIntro para.\n\n## Setup\n\nInstall the thing."
    spans = HeadingAwareChunker().chunk(text)
    assert spans[0].heading_path == ("Guide",)
    assert spans[1].heading_path == ("Guide", "Setup")
    assert "Install the thing." in spans[1].text


def test_chunk_text_equals_verbatim_section_body():
    """The smoke gate relies on chunk text being an exact copy of the source section
    body (not merely containing it) so that fake-embedder exact-match retrieval and
    the smoke citation check are deterministic."""
    text = "# Guide\n\nIntro para.\n\n## Setup\n\nInstall the thing."
    spans = HeadingAwareChunker().chunk(text)
    assert spans[0].text == "Intro para."
    assert spans[1].text == "Install the thing."


def test_sibling_heading_replaces_previous_level():
    text = "## A\n\nalpha\n\n## B\n\nbeta"
    spans = HeadingAwareChunker().chunk(text)
    assert spans[0].heading_path == ("A",)
    assert spans[1].heading_path == ("B",)


def test_long_sections_split_by_paragraph():
    body = "\n\n".join(f"Paragraph {i}. " + "x" * 200 for i in range(12))
    spans = HeadingAwareChunker(max_chars=500, overlap_chars=50).chunk(body)
    assert len(spans) > 1
    assert all(len(span.text) < 600 for span in spans)


def test_plain_text_yields_single_chunk_with_empty_path():
    spans = HeadingAwareChunker().chunk("Just a short note.")
    assert len(spans) == 1
    assert spans[0].heading_path == ()


def test_empty_input_yields_no_chunks():
    assert HeadingAwareChunker().chunk("") == []
    assert HeadingAwareChunker().chunk("# Title only, no body") == []


def test_hash_lines_inside_code_fences_are_not_headings():
    text = (
        "# Real Heading\n\n"
        "Intro.\n\n"
        "```python\n"
        "# not a heading\n"
        "x = 1\n"
        "```\n\n"
        "Outro."
    )
    spans = HeadingAwareChunker().chunk(text)
    assert len(spans) == 1
    assert spans[0].heading_path == ("Real Heading",)
    assert "# not a heading" in spans[0].text


def test_single_oversize_paragraph_is_hard_split():
    body = "x" * 3000  # one paragraph, no blank lines
    chunker = HeadingAwareChunker(max_chars=500, overlap_chars=50)
    spans = chunker.chunk(body)
    assert len(spans) > 1
    assert all(len(span.text) <= 500 + 50 + 2 for span in spans)
    assert sum(len(s.text) for s in spans) >= 3000  # nothing lost (overlap adds duplication)


def test_zero_overlap_does_not_duplicate_cumulatively():
    paragraphs = [
        f"Paragraph number {i} filler text to pad out the length nicely." for i in range(6)
    ]
    body = "\n\n".join(paragraphs)
    chunker = HeadingAwareChunker(max_chars=100, overlap_chars=0)
    spans = chunker.chunk(body)
    assert len(spans) >= 3
    assert all(len(s.text) <= 102 for s in spans)


def test_invalid_chunker_params_are_rejected():
    with pytest.raises(ValueError):
        HeadingAwareChunker(max_chars=100, overlap_chars=100)
    with pytest.raises(ValueError):
        HeadingAwareChunker(max_chars=0)
    with pytest.raises(ValueError):
        HeadingAwareChunker(max_chars=100, overlap_chars=-1)
