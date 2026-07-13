"""Shared transcript-formatting helpers for the OpenAI and local Whisper adapters.

Both providers hand back a list of `(start_seconds, text)` segments; rendering each
segment as its own `[MM:SS] text` (or `[HH:MM:SS] text` past the hour mark) line lets
retrieval citations point a reader at roughly where in the recording an answer came
from, without needing a separate timestamp index.
"""

from collections.abc import Iterable


def format_timestamp(seconds: float) -> str:
    """Render `seconds` as `MM:SS`, or `HH:MM:SS` once the recording passes an hour."""
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def render_segments(segments: Iterable[tuple[float, str]], fallback_text: str) -> str:
    """Render `(start_seconds, text)` segments as `[MM:SS] text` lines, one per segment.

    Segments with only whitespace are dropped. If no segments survive (the provider
    returned none, or all were empty), falls back to `fallback_text` verbatim — plain,
    un-timestamped text — rather than raising or returning an empty transcript.
    """
    lines = []
    for start, text in segments:
        stripped = text.strip()
        if not stripped:
            continue
        lines.append(f"[{format_timestamp(start)}] {stripped}")
    return "\n".join(lines) if lines else fallback_text.strip()
