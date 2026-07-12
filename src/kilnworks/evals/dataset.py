import json
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError


class EvalCase(BaseModel):
    question: str = Field(..., min_length=1)
    expected_sources: list[str] = Field(..., min_length=1)


def load_cases(path: Path) -> list[EvalCase]:
    """Load JSONL eval cases, one per non-blank line, 1-based line numbers in errors."""
    cases: list[EvalCase] = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
            cases.append(EvalCase.model_validate(data))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise ValueError(f"{path}:{n}: {exc}") from exc
    if not cases:
        raise ValueError(f"no eval cases in {path}")
    return cases
