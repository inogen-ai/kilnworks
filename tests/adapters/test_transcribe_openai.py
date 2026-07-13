from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from openai import OpenAI

from kilnworks.adapters.media.transcribe_openai import OpenAIWhisper
from kilnworks.core.errors import ProviderError
from kilnworks.core.models import Completion

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
SAMPLE_WAV = (FIXTURES_DIR / "sample.wav").read_bytes()


def _client(handler):
    return OpenAI(
        api_key="k",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def _verbose_json_response(
    text="Hello from the kiln.",
    segments=None,
    duration=3.5,
):
    if segments is None:
        segments = [
            {
                "id": 0, "seek": 0, "start": 0.0, "end": 1.5, "text": " Hello from",
                "tokens": [], "temperature": 0.0, "avg_logprob": 0.0,
                "compression_ratio": 0.0, "no_speech_prob": 0.0,
            },
            {
                "id": 1, "seek": 0, "start": 1.5, "end": 3.5, "text": " the kiln.",
                "tokens": [], "temperature": 0.0, "avg_logprob": 0.0,
                "compression_ratio": 0.0, "no_speech_prob": 0.0,
            },
        ]
    return httpx.Response(
        200,
        json={
            "text": text,
            "language": "english",
            "duration": duration,
            "segments": segments,
        },
    )


def test_transcribe_sends_multipart_file_and_renders_timestamped_segments():
    captured = {}

    def handler(request):
        captured["request"] = request
        return _verbose_json_response()

    whisper = OpenAIWhisper(api_key="k", client=_client(handler))
    completion = whisper.transcribe(SAMPLE_WAV, "audio/wav", "sample.wav")

    assert isinstance(completion, Completion)
    assert completion.text == "[00:00] Hello from\n[00:01] the kiln."
    assert completion.model == "whisper-1"
    assert completion.output_tokens == 0
    assert completion.input_tokens == 4  # rounded duration (3.5s) — the usage signal

    request = captured["request"]
    assert request.method == "POST"
    assert request.url.path == "/v1/audio/transcriptions"
    content_type = request.headers["content-type"]
    assert content_type.startswith("multipart/form-data")
    body = request.content
    assert b'name="model"' in body and b"whisper-1" in body
    assert b'name="response_format"' in body and b"verbose_json" in body
    assert b'name="timestamp_granularities[]"' in body and b"segment" in body
    assert b'name="file"; filename="sample.wav"' in body
    assert b"Content-Type: audio/wav" in body


def test_custom_model_is_sent():
    def handler(request):
        assert b"whisper-large-v3" in request.content
        return _verbose_json_response()

    whisper = OpenAIWhisper(api_key="k", model="whisper-large-v3", client=_client(handler))
    completion = whisper.transcribe(SAMPLE_WAV, "audio/wav", "sample.wav")
    assert completion.model == "whisper-large-v3"


def test_no_segments_falls_back_to_plain_text():
    def handler(request):
        return _verbose_json_response(text="plain fallback text", segments=[])

    whisper = OpenAIWhisper(api_key="k", client=_client(handler))
    completion = whisper.transcribe(SAMPLE_WAV, "audio/wav", "sample.wav")
    assert completion.text == "plain fallback text"
    assert "[" not in completion.text  # no timestamp prefix in the fallback path


def test_zero_duration_reports_zero_input_tokens():
    def handler(request):
        return _verbose_json_response(text="silent", segments=[], duration=0.0)

    whisper = OpenAIWhisper(api_key="k", client=_client(handler))
    completion = whisper.transcribe(SAMPLE_WAV, "audio/wav", "sample.wav")
    assert completion.input_tokens == 0
    assert completion.output_tokens == 0


def test_transient_error_is_retried_then_succeeds(monkeypatch):
    monkeypatch.setattr("kilnworks.core.retry.time", SimpleNamespace(sleep=lambda s: None))
    attempts = {"n": 0}

    def handler(request):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise httpx.ConnectError("refused", request=request)
        return _verbose_json_response(text="recovered", segments=[])

    whisper = OpenAIWhisper(api_key="k", client=_client(handler))
    assert whisper.transcribe(SAMPLE_WAV, "audio/wav", "sample.wav").text == "recovered"
    assert attempts["n"] == 2


def test_exhausted_transient_errors_raise_provider_error(monkeypatch):
    monkeypatch.setattr("kilnworks.core.retry.time", SimpleNamespace(sleep=lambda s: None))

    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    whisper = OpenAIWhisper(api_key="k", client=_client(handler))
    with pytest.raises(ProviderError) as excinfo:
        whisper.transcribe(SAMPLE_WAV, "audio/wav", "sample.wav")
    assert excinfo.value.provider == "openai"
