import re
from collections.abc import Sequence

from pydantic import BaseModel

from kilnworks.core.ports import LLMProvider
from kilnworks.core.query import QueryService, format_context
from kilnworks.evals.dataset import EvalCase

JUDGE_SYSTEM = "You are a strict fact-checker."


def judge_prompt(context: str, answer: str) -> str:
    return (
        "Given the context below, does the answer make only claims supported by it?\n"
        "Reply starting with YES if every factual claim in the answer is supported by "
        "the context, otherwise reply starting with NO. Follow with one sentence of "
        "reasoning. Bracketed citation markers in the answer such as [1] are "
        "formatting, not claims — ignore them when judging support.\n\n"
        f"Context:\n\n{context}\n\nAnswer:\n\n{answer}"
    )


def parse_verdict(text: str) -> bool:
    """Parse a judge reply into a faithful/unfaithful verdict.

    The judge prompt demands a reply starting with YES or NO. We take the first
    alphabetic token (stripping leading markdown/punctuation/whitespace) and
    compare it case-insensitively; an unparseable reply is conservatively
    treated as unfaithful.
    """
    match = re.search(r"[a-zA-Z]+", text)
    if not match:
        return False
    return match.group(0).lower() == "yes"


class CaseResult(BaseModel):
    question: str
    hit: bool
    cited: bool
    faithful: bool
    answer_text: str


class EvalSummary(BaseModel):
    results: list[CaseResult]

    @property
    def cases(self) -> int:
        return len(self.results)

    @property
    def hit_rate(self) -> float:
        return self._rate(lambda r: r.hit)

    @property
    def citation_rate(self) -> float:
        return self._rate(lambda r: r.cited)

    @property
    def faithfulness_rate(self) -> float:
        return self._rate(lambda r: r.faithful)

    def _rate(self, predicate) -> float:
        if not self.results:
            return 0.0
        return sum(1 for r in self.results if predicate(r)) / len(self.results)


class EvalRunner:
    def __init__(self, query: QueryService, judge: LLMProvider):
        self._query = query
        self._judge = judge

    def run(
        self,
        cases: Sequence[EvalCase],
        principals: Sequence[str] = ("public",),
        limit: int = 8,
    ) -> EvalSummary:
        results = [self._run_one(case, principals, limit) for case in cases]
        return EvalSummary(results=results)

    def _run_one(self, case: EvalCase, principals: Sequence[str], limit: int) -> CaseResult:
        retrieved = self._query.retrieve(case.question, principals, limit)
        expected = set(case.expected_sources)
        hit = bool({r.title for r in retrieved} & expected)

        answer = self._query.ask(case.question, principals, limit)
        cited = bool({c.title for c in answer.citations} & expected)

        faithful = False
        if retrieved and answer.text.strip():
            context = format_context(retrieved)
            completion = self._judge.complete(JUDGE_SYSTEM, judge_prompt(context, answer.text))
            faithful = parse_verdict(completion.text)

        return CaseResult(
            question=case.question,
            hit=hit,
            cited=cited,
            faithful=faithful,
            answer_text=answer.text,
        )
