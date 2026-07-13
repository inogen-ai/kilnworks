import base64
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from openai import OpenAI

from kilnworks.adapters.media.vision_openai import VISION_PROMPT, OpenAIVision
from kilnworks.core.errors import ProviderError
from kilnworks.core.models import Completion

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
SAMPLE_PNG = (FIXTURES_DIR / "sample.png").read_bytes()


def _client(handler):
    return OpenAI(
        api_key="k",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def _chat_response(text="A vase on a wheel.", model="gpt-4o-mini-2024-07-18"):
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-1",
            "object": "chat.completion",
            "created": 0,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 300, "completion_tokens": 20, "total_tokens": 320},
        },
    )


def test_describe_sends_image_data_uri_and_prompt_returns_usage():
    captured = {}

    def handler(request):
        captured["body"] = json.loads(request.content)
        return _chat_response()

    vision = OpenAIVision(api_key="k", client=_client(handler))
    completion = vision.describe(SAMPLE_PNG, "image/png", "sample.png")

    assert isinstance(completion, Completion)
    assert completion.text == "A vase on a wheel."
    assert completion.model == "gpt-4o-mini-2024-07-18"
    assert completion.input_tokens == 300
    assert completion.output_tokens == 20

    body = captured["body"]
    assert body["model"] == "gpt-4o-mini"
    message = body["messages"][0]
    assert message["role"] == "user"
    text_block, image_block = message["content"]
    assert text_block == {"type": "text", "text": VISION_PROMPT}
    assert image_block["type"] == "image_url"
    url = image_block["image_url"]["url"]
    assert url.startswith("data:image/jpeg;base64,")
    # the sent bytes decode as a real (normalized/re-encoded) image
    raw = base64.b64decode(url.split(",", 1)[1])
    assert raw[:2] == b"\xff\xd8"  # JPEG SOI marker


def test_custom_model_is_sent():
    def handler(request):
        payload = json.loads(request.content)
        assert payload["model"] == "gpt-4o"
        return _chat_response()

    vision = OpenAIVision(api_key="k", model="gpt-4o", client=_client(handler))
    vision.describe(SAMPLE_PNG, "image/png", "sample.png")


def test_non_image_bytes_raise_before_any_request():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return _chat_response()

    vision = OpenAIVision(api_key="k", client=_client(handler))
    with pytest.raises(ValueError, match="not a valid image"):
        vision.describe(b"not an image", "image/png", "bad.png")
    assert calls["n"] == 0


def test_transient_error_is_retried_then_succeeds(monkeypatch):
    monkeypatch.setattr("kilnworks.core.retry.time", SimpleNamespace(sleep=lambda s: None))
    attempts = {"n": 0}

    def handler(request):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise httpx.ConnectError("refused", request=request)
        return _chat_response(text="recovered")

    vision = OpenAIVision(api_key="k", client=_client(handler))
    assert vision.describe(SAMPLE_PNG, "image/png", "sample.png").text == "recovered"
    assert attempts["n"] == 2


def test_exhausted_transient_errors_raise_provider_error(monkeypatch):
    monkeypatch.setattr("kilnworks.core.retry.time", SimpleNamespace(sleep=lambda s: None))

    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    vision = OpenAIVision(api_key="k", client=_client(handler))
    with pytest.raises(ProviderError) as excinfo:
        vision.describe(SAMPLE_PNG, "image/png", "sample.png")
    assert excinfo.value.provider == "openai"
