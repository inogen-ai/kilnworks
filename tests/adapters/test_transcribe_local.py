import sys
from types import ModuleType, SimpleNamespace

import pytest

from kilnworks.adapters.media.transcribe_local import LocalWhisper
from kilnworks.core.errors import ProviderError


class _FakeSegment(SimpleNamespace):
    """Stands in for faster-whisper's Segment namedtuple (has .start/.text)."""


def _install_fake_faster_whisper(monkeypatch, segments, capture=None):
    """Injects a fake `faster_whisper` module into sys.modules so `LocalWhisper`'s
    lazy `from faster_whisper import WhisperModel` resolves without the real
    (optional, potentially-uninstalled) package."""

    class FakeWhisperModel:
        def __init__(self, model_size, device="cpu", compute_type="int8"):
            if capture is not None:
                capture["init_args"] = (model_size, device, compute_type)

        def transcribe(self, audio):
            if capture is not None:
                capture["transcribe_called"] = True
            info = SimpleNamespace(duration=5.0, language="en")
            return iter(segments), info

    fake_module = ModuleType("faster_whisper")
    fake_module.WhisperModel = FakeWhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_module)


def test_transcribe_invokes_faster_whisper_and_renders_timestamped_segments(monkeypatch):
    segments = [
        _FakeSegment(start=0.0, text=" Hello from"),
        _FakeSegment(start=2.0, text=" the kiln."),
    ]
    capture = {}
    _install_fake_faster_whisper(monkeypatch, segments, capture)

    whisper = LocalWhisper(model_size="base")
    completion = whisper.transcribe(b"fake wav bytes", "audio/wav", "sample.wav")

    assert capture["transcribe_called"] is True
    assert capture["init_args"] == ("base", "cpu", "int8")
    assert completion.text == "[00:00] Hello from\n[00:02] the kiln."
    assert completion.model == "faster-whisper/base"
    assert completion.input_tokens == 0  # local compute: no metered spend
    assert completion.output_tokens == 0


def test_model_is_constructed_once_and_reused_across_calls(monkeypatch):
    capture = {"init_count": 0}
    segments = [_FakeSegment(start=0.0, text="hi")]

    class CountingWhisperModel:
        def __init__(self, model_size, device="cpu", compute_type="int8"):
            capture["init_count"] += 1

        def transcribe(self, audio):
            return iter(segments), SimpleNamespace(duration=1.0, language="en")

    fake_module = ModuleType("faster_whisper")
    fake_module.WhisperModel = CountingWhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_module)

    whisper = LocalWhisper()
    whisper.transcribe(b"a", "audio/wav", "a.wav")
    whisper.transcribe(b"b", "audio/wav", "b.wav")
    assert capture["init_count"] == 1


def test_empty_segments_fall_back_to_empty_text_without_crashing(monkeypatch):
    _install_fake_faster_whisper(monkeypatch, segments=[])
    whisper = LocalWhisper()
    completion = whisper.transcribe(b"silence", "audio/wav", "silence.wav")
    assert completion.text == ""


def test_missing_faster_whisper_raises_clear_provider_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    whisper = LocalWhisper()
    with pytest.raises(ProviderError, match="local-whisper"):
        whisper.transcribe(b"bytes", "audio/wav", "sample.wav")
