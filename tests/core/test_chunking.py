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


def test_page_markers_tag_chunks_with_their_page():
    text = "[[page:1]]\nFirst page body.\n\n[[page:2]]\nSecond page body."
    spans = HeadingAwareChunker().chunk(text)
    assert [(s.text, s.page) for s in spans] == [
        ("First page body.", 1),
        ("Second page body.", 2),
    ]


def test_page_marker_text_never_leaks_into_chunks():
    text = "[[page:1]]\nAlpha.\n\n[[page:2]]\nBeta."
    spans = HeadingAwareChunker().chunk(text)
    assert all("[[page:" not in s.text for s in spans)


def test_long_body_within_one_page_keeps_page_on_all_pieces():
    body = "\n\n".join(f"Paragraph {i}. " + "x" * 200 for i in range(12))
    text = f"[[page:4]]\n{body}"
    spans = HeadingAwareChunker(max_chars=500, overlap_chars=50).chunk(text)
    assert len(spans) > 1
    assert all(s.page == 4 for s in spans)


def test_body_crossing_a_page_marker_splits_at_the_boundary():
    text = "[[page:1]]\nText that stays on page one.\n[[page:2]]\nText that is on page two."
    spans = HeadingAwareChunker().chunk(text)
    assert [(s.text, s.page) for s in spans] == [
        ("Text that stays on page one.", 1),
        ("Text that is on page two.", 2),
    ]


def test_page_marker_flushes_across_headings():
    text = (
        "[[page:1]]\n# Guide\n\nIntro on page one.\n\n"
        "## Setup\n\nStill page one.\n\n"
        "[[page:2]]\nSpills onto page two."
    )
    spans = HeadingAwareChunker().chunk(text)
    assert [(s.heading_path, s.text, s.page) for s in spans] == [
        (("Guide",), "Intro on page one.", 1),
        (("Guide", "Setup"), "Still page one.", 1),
        (("Guide", "Setup"), "Spills onto page two.", 2),
    ]


def test_no_page_markers_yields_page_none_and_byte_identical_chunks():
    """Regression guard: text with no page markers (every non-PDF format) must chunk
    exactly as before this feature — every span's page is None and both the text and
    heading_path are unchanged for a representative heading+paragraph sample."""
    text = "# Guide\n\nIntro para.\n\n## Setup\n\nInstall the thing."
    spans = HeadingAwareChunker().chunk(text)
    assert [(s.text, s.heading_path, s.page) for s in spans] == [
        ("Intro para.", ("Guide",), None),
        ("Install the thing.", ("Guide", "Setup"), None),
    ]


def test_invalid_chunker_params_are_rejected():
    with pytest.raises(ValueError):
        HeadingAwareChunker(max_chars=100, overlap_chars=100)
    with pytest.raises(ValueError):
        HeadingAwareChunker(max_chars=0)
    with pytest.raises(ValueError):
        HeadingAwareChunker(max_chars=100, overlap_chars=-1)
