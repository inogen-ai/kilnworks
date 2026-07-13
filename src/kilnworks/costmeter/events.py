from dataclasses import dataclass


@dataclass(frozen=True)
class CostEvent:
    kind: str  # "chat" | "embedding" | "vision" | "transcription"
    model: str
    input_tokens: int
    output_tokens: int = 0
    context: str = ""
    user_id: str | None = None
