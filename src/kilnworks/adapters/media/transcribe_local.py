import io

from kilnworks.adapters.media.transcript import render_segments
from kilnworks.core.errors import ProviderError
from kilnworks.core.models import Completion

LOCAL_WHISPER_INSTALL_HINT = (
    "faster-whisper is not installed; install it with `pip install kilnworks[local-whisper]` "
    "(or `uv sync --extra local-whisper`) to use KILNWORKS_TRANSCRIPTION_PROVIDER=local, or "
    "set KILNWORKS_TRANSCRIPTION_PROVIDER=openai/none instead"
)


class LocalWhisper:
    """Transcriber backed by a local `faster-whisper` model — CPU-capable, fully
    offline, no per-call API cost.

    `faster-whisper` is an OPTIONAL dependency (the `kilnworks[local-whisper]` extra)
    so the base install stays light. It's imported lazily, inside `_get_model`, so
    merely constructing a `LocalWhisper` (e.g. during wiring) never requires the
    package to be installed until a transcription is actually attempted.
    """

    def __init__(self, model_size: str = "base", device: str = "cpu", compute_type: str = "int8"):
        self._model_size = model_size
        self._device = device
        self._compute_type = compute_type
        self._model = None

    def _get_model(self):
        if self._model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise ProviderError("local-whisper", LOCAL_WHISPER_INSTALL_HINT) from exc
            self._model = WhisperModel(
                self._model_size, device=self._device, compute_type=self._compute_type
            )
        return self._model

    def transcribe(self, media: bytes, mime: str, name: str) -> Completion:
        model = self._get_model()
        segments_iter, _info = model.transcribe(io.BytesIO(media))
        segments = [(segment.start, segment.text) for segment in segments_iter]
        # faster-whisper has no separate plain-text field to fall back to (unlike the
        # OpenAI API's `.text`) — an empty segment list means an empty transcript,
        # which is the correct (non-crashing) outcome for e.g. silent audio.
        text = render_segments(segments, fallback_text="")
        return Completion(
            text=text,
            model=f"faster-whisper/{self._model_size}",
            input_tokens=0,  # local compute: no metered per-call spend to report
            output_tokens=0,
        )
