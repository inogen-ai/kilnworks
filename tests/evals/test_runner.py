import pytest

from kilnworks.adapters.embedders.fake import FakeEmbedder
from kilnworks.adapters.llm.fake import FakeLLM
from kilnworks.core.query import QueryService
from kilnworks.evals.dataset import EvalCase
from kilnworks.evals.runner import (
    CaseResult,
    EvalRunner,
    EvalSummary,
    judge_prompt,
    parse_verdict,
)
from tests.core.test_query import StubIndex, _hit


def _service(results, reply="Based on the context, yes. [1]"):
    return QueryService(FakeEmbedder(), StubIndex(results), FakeLLM(reply=reply))


def test_runner_happy_path_all_true():
    service = _service([_hit("kilns fire at 1300 degrees", title="doc")])
    judge = FakeLLM(reply="Yes, every claim is supported by the context.")
    case = EvalCase(question="How hot?", expected_sources=["doc"])

    summary = EvalRunner(service, judge).run([case])

    assert len(summary.results) == 1
    result = summary.results[0]
    assert result.hit is True
    assert result.cited is True
    assert result.faithful is True
    assert result.answer_text == "Based on the context, yes. [1]"
    assert len(judge.calls) == 1


def test_runner_miss_path_when_expected_source_absent():
    service = _service([_hit("kilns fire at 1300 degrees", title="doc")])
    judge = FakeLLM(reply="Yes, supported.")
    case = EvalCase(question="How hot?", expected_sources=["other"])

    summary = EvalRunner(service, judge).run([case])

    result = summary.results[0]
    assert result.hit is False
    assert result.cited is False


def test_runner_judge_no_path_marks_unfaithful():
    service = _service([_hit("kilns fire at 1300 degrees", title="doc")])
    judge = FakeLLM(reply="NO — the answer invents numbers.")
    case = EvalCase(question="How hot?", expected_sources=["doc"])

    summary = EvalRunner(service, judge).run([case])

    assert summary.results[0].faithful is False


def test_runner_empty_retrieval_short_circuits_judge():
    service = _service([])
    judge = FakeLLM(reply="Yes, supported.")
    case = EvalCase(question="How hot?", expected_sources=["doc"])

    summary = EvalRunner(service, judge).run([case])

    result = summary.results[0]
    assert result.hit is False
    assert result.cited is False
    assert result.faithful is False
    assert judge.calls == []


def test_runner_passes_principals_and_limit_through():
    service = _service([_hit("kilns fire hot", title="doc")])
    judge = FakeLLM(reply="Yes, supported.")
    case = EvalCase(question="How hot?", expected_sources=["doc"])

    EvalRunner(service, judge).run([case], principals=("hr", "public"), limit=3)

    assert service._index.searches[0] == (("hr", "public"), 3, None)


def test_summary_rates_computed_over_mixed_results():
    results = [
        CaseResult(question="q1", hit=True, cited=True, faithful=True, answer_text="a1"),
        CaseResult(question="q2", hit=True, cited=False, faithful=True, answer_text="a2"),
        CaseResult(question="q3", hit=False, cited=False, faithful=False, answer_text="a3"),
        CaseResult(question="q4", hit=True, cited=True, faithful=False, answer_text="a4"),
    ]
    summary = EvalSummary(results=results)

    assert summary.cases == 4
    assert summary.hit_rate == 0.75
    assert summary.citation_rate == 0.5
    assert summary.faithfulness_rate == 0.5


def test_summary_rates_are_zero_when_no_cases():
    summary = EvalSummary(results=[])

    assert summary.cases == 0
    assert summary.hit_rate == 0.0
    assert summary.citation_rate == 0.0
    assert summary.faithfulness_rate == 0.0


def test_judge_prompt_instructs_ignoring_citation_markers():
    prompt = judge_prompt("some context", "The kiln fires hot. [1]")
    assert "[1]" in prompt  # answer text with a citation marker is included verbatim
    assert "citation" in prompt.lower()
    assert "not claims" in prompt.lower() or "ignore them" in prompt.lower()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Yes, every claim is supported by the context.", True),
        ("**Yes** — every claim is supported.", True),
        ("yes.", True),
        ("Yes and no, technically.", True),
        ("No — yesterday's answer said 40 litres", False),
        ("NO — the answer invents numbers.", False),
        ("", False),
        ("The answer is supported", False),
    ],
)
def test_parse_verdict(text, expected):
    assert parse_verdict(text) is expected
