from pathlib import Path

import pytest

from kilnworks.evals.dataset import EvalCase, load_cases


def _write(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "cases.jsonl"
    path.write_text(content)
    return path


def test_load_cases_parses_valid_jsonl(tmp_path):
    path = _write(
        tmp_path,
        '{"question": "How hot do kilns fire?", "expected_sources": ["firing"]}\n'
        '{"question": "What clay is used?", "expected_sources": ["clay", "materials"]}\n',
    )
    cases = load_cases(path)
    assert cases == [
        EvalCase(question="How hot do kilns fire?", expected_sources=["firing"]),
        EvalCase(question="What clay is used?", expected_sources=["clay", "materials"]),
    ]


def test_load_cases_skips_blank_lines(tmp_path):
    path = _write(
        tmp_path,
        '{"question": "q1", "expected_sources": ["a"]}\n'
        "\n"
        "   \n"
        '{"question": "q2", "expected_sources": ["b"]}\n',
    )
    cases = load_cases(path)
    assert len(cases) == 2
    assert [c.question for c in cases] == ["q1", "q2"]


def test_load_cases_malformed_json_names_line_number(tmp_path):
    path = _write(
        tmp_path,
        '{"question": "q1", "expected_sources": ["a"]}\n'
        "not json at all\n",
    )
    with pytest.raises(ValueError) as exc_info:
        load_cases(path)
    message = str(exc_info.value)
    assert str(path) in message
    assert ":2:" in message


def test_load_cases_invalid_schema_names_line_number(tmp_path):
    path = _write(
        tmp_path,
        '{"question": "q1", "expected_sources": ["a"]}\n'
        '{"question": "", "expected_sources": []}\n',
    )
    with pytest.raises(ValueError) as exc_info:
        load_cases(path)
    message = str(exc_info.value)
    assert str(path) in message
    assert ":2:" in message


def test_load_cases_empty_file_raises(tmp_path):
    path = _write(tmp_path, "")
    with pytest.raises(ValueError, match=r"no eval cases in"):
        load_cases(path)


def test_load_cases_only_blank_lines_raises(tmp_path):
    path = _write(tmp_path, "\n   \n\n")
    with pytest.raises(ValueError, match=r"no eval cases in"):
        load_cases(path)
