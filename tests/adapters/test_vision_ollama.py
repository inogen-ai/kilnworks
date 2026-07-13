import base64
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from kilnworks.adapters.media.vision_ollama import VISION_PROMPT, OllamaVision
from kilnworks.core.errors import ProviderError
from kilnworks.core.models import Completion

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
SAMPLE_PNG = (FIXTURES_DIR / "sample.png").read_bytes()


def _client(handler):
    return httpx.Client(base_url="http://ollama.test", transport=httpx.MockTransport(handler))


def test_describe_sends_images_field_and_returns_usage():
    captured = {}

    def handler(request):
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "message": {"content": "A vase on a wheel."},
                "model": "llava",
                "prompt_eval_count": 300,
                "eval_count": 20,
            },
        )

    vision = OllamaVision(client=_client(handler))
    completion = vision.describe(SAMPLE_PNG, "image/png", "sample.png")

    assert isinstance(completion, Completion)
    assert completion.text == "A vase on a wheel."
    assert completion.model == "llava"
    assert completion.input_tokens == 300
    assert completion.output_tokens == 20

    body = captured["body"]
    assert body["model"] == "llava"
    assert body["stream"] is False
    message = body["messages"][0]
    assert message["role"] == "user"
    assert message["content"] == VISION_PROMPT
    assert len(message["images"]) == 1
    raw = base64.b64decode(message["images"][0])
    assert raw[:2] == b"\xff\xd8"  # JPEG SOI marker: re-encoded by normalize_image


def test_custom_model_is_sent():
    def handler(request):
        payload = json.loads(request.content)
        assert payload["model"] == "bakllava"
        return httpx.Response(200, json={"message": {"content": "ok"}, "model": "bakllava"})

    vision = OllamaVision(model="bakllava", client=_client(handler))
    vision.describe(SAMPLE_PNG, "image/png", "sample.png")


def test_non_image_bytes_raise_before_any_request():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, json={"message": {"content": "ok"}})

    vision = OllamaVision(client=_client(handler))
    with pytest.raises(ValueError, match="not a valid image"):
        vision.describe(b"not an image", "image/png", "bad.png")
    assert calls["n"] == 0


def test_500_is_retried_then_succeeds(monkeypatch):
    monkeypatch.setattr("kilnworks.core.retry.time", SimpleNamespace(sleep=lambda s: None))
    attempts = {"n": 0}

    def handler(request):
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(500, text="server error")
        return httpx.Response(
            200,
            json={"message": {"content": "recovered"}, "model": "llava",
                  "prompt_eval_count": 1, "eval_count": 1},
        )

    vision = OllamaVision(client=_client(handler))
    assert vision.describe(SAMPLE_PNG, "image/png", "sample.png").text == "recovered"
    assert attempts["n"] == 2


def test_404_is_immediate_provider_error_no_retry():
    attempts = {"n": 0}

    def handler(request):
        attempts["n"] += 1
        return httpx.Response(404, text="not found")

    vision = OllamaVision(client=_client(handler))
    with pytest.raises(ProviderError) as excinfo:
        vision.describe(SAMPLE_PNG, "image/png", "sample.png")
    assert excinfo.value.provider == "ollama"
    assert attempts["n"] == 1
