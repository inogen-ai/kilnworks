import openai
from openai import OpenAI

from kilnworks.adapters.media.transcript import render_segments
from kilnworks.core.errors import ProviderError, TransientProviderError
from kilnworks.core.models import Completion
from kilnworks.core.retry import retry_with_backoff

_TRANSIENT = (openai.APIConnectionError, openai.RateLimitError, openai.InternalServerError)


class OpenAIWhisper:
    """Transcriber backed by the OpenAI audio transcription API
    (`POST /audio/transcriptions`, `whisper-1` by default)."""

    def __init__(self, api_key: str, model: str = "whisper-1", client=None):
        self._client = client or OpenAI(api_key=api_key, max_retries=0)
        self._model = model

    def transcribe(self, media: bytes, mime: str, name: str) -> Completion:
        return retry_with_backoff(lambda: self._transcribe_once(media, mime, name))

    def _transcribe_once(self, media: bytes, mime: str, name: str) -> Completion:
        try:
            response = self._client.audio.transcriptions.create(
                model=self._model,
                file=(name, media, mime),
                response_format="verbose_json",
                timestamp_granularities=["segment"],
            )
        except _TRANSIENT as exc:
            raise TransientProviderError("openai", str(exc)) from exc
        except openai.OpenAIError as exc:
            raise ProviderError("openai", str(exc)) from exc

        segments = [(segment.start, segment.text) for segment in (response.segments or [])]
        text = render_segments(segments, fallback_text=response.text or "")

        # Whisper bills by audio duration, not input/output tokens, so there's no
        # honest token count to report. When the API reports `duration`, record the
        # rounded seconds as `input_tokens` (output_tokens stays 0) so a usage signal
        # still reaches the cost ledger — grouped under kind/context "transcription",
        # so it never gets summed alongside genuine token counts from other kinds.
        duration_seconds = getattr(response, "duration", None)
        return Completion(
            text=text,
            model=self._model,
            input_tokens=round(duration_seconds) if duration_seconds else 0,
            output_tokens=0,
        )
