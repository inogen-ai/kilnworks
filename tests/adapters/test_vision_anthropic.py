import base64
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from anthropic import Anthropic

from kilnworks.adapters.media.vision_anthropic import VISION_PROMPT, AnthropicVision
from kilnworks.core.errors import ProviderError
from kilnworks.core.models import Completion

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
SAMPLE_PNG = (FIXTURES_DIR / "sample.png").read_bytes()


def _client(handler):
    return Anthropic(
        api_key="k",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def _messages_response(text="A vase on a wheel.", model="claude-opus-4-8-resolved"):
    return httpx.Response(
        200,
        json={
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 300, "output_tokens": 20},
        },
    )


def test_describe_sends_base64_image_block_and_returns_usage():
    captured = {}

    def handler(request):
        captured["body"] = json.loads(request.content)
        return _messages_response()

    vision = AnthropicVision(api_key="k", client=_client(handler))
    completion = vision.describe(SAMPLE_PNG, "image/png", "sample.png")

    assert isinstance(completion, Completion)
    assert completion.text == "A vase on a wheel."
    assert completion.model == "claude-opus-4-8-resolved"
    assert completion.input_tokens == 300
    assert completion.output_tokens == 20

    body = captured["body"]
    assert body["model"] == "claude-opus-4-8"
    message = body["messages"][0]
    assert message["role"] == "user"
    image_block, text_block = message["content"]
    assert text_block == {"type": "text", "text": VISION_PROMPT}
    assert image_block["type"] == "image"
    assert image_block["source"]["type"] == "base64"
    assert image_block["source"]["media_type"] == "image/jpeg"
    raw = base64.b64decode(image_block["source"]["data"])
    assert raw[:2] == b"\xff\xd8"  # JPEG SOI marker: re-encoded by normalize_image


def test_custom_model_and_max_tokens_are_sent():
    def handler(request):
        payload = json.loads(request.content)
        assert payload["model"] == "claude-sonnet-4"
        assert payload["max_tokens"] == 512
        return _messages_response()

    vision = AnthropicVision(
        api_key="k", model="claude-sonnet-4", max_tokens=512, client=_client(handler)
    )
    vision.describe(SAMPLE_PNG, "image/png", "sample.png")


def test_non_image_bytes_raise_before_any_request():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return _messages_response()

    vision = AnthropicVision(api_key="k", client=_client(handler))
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
        return _messages_response(text="recovered")

    vision = AnthropicVision(api_key="k", client=_client(handler))
    assert vision.describe(SAMPLE_PNG, "image/png", "sample.png").text == "recovered"
    assert attempts["n"] == 2


def test_exhausted_transient_errors_raise_provider_error(monkeypatch):
    monkeypatch.setattr("kilnworks.core.retry.time", SimpleNamespace(sleep=lambda s: None))

    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    vision = AnthropicVision(api_key="k", client=_client(handler))
    with pytest.raises(ProviderError) as excinfo:
        vision.describe(SAMPLE_PNG, "image/png", "sample.png")
    assert excinfo.value.provider == "anthropic"


def test_normalize_image_applies_exif_orientation():
    """A phone photo with an Orientation tag must be physically rotated before encode —
    EXIF is dropped on JPEG re-encode, so the pixels have to carry the orientation
    (regression: sideways document scans degrade the model's verbatim transcription)."""
    from PIL import Image
    import io as _io
    from kilnworks.adapters.media.imaging import normalize_image

    # a 40x20 landscape image tagged Orientation=6 (rotate 270° CW on display → portrait)
    src = Image.new("RGB", (40, 20), "white")
    buf = _io.BytesIO()
    exif = Image.Exif()
    exif[0x0112] = 6  # Orientation
    src.save(buf, format="JPEG", exif=exif)

    out_bytes, mime = normalize_image(buf.getvalue(), "photo.jpg")
    out = Image.open(_io.BytesIO(out_bytes))
    assert mime == "image/jpeg"
    assert out.size == (20, 40)  # transposed to portrait, not the original 40x20


def test_normalize_image_rejects_decompression_bomb():
    """A decompression-bomb image raises the friendly InvalidImageError, not a raw
    Pillow DecompressionBombError."""
    from PIL import Image
    import io as _io
    from kilnworks.adapters.media.imaging import normalize_image, InvalidImageError

    original = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = 100  # force a tiny normal image over the "bomb" threshold
    try:
        buf = _io.BytesIO()
        Image.new("RGB", (50, 50), "white").save(buf, format="PNG")
        with pytest.raises(InvalidImageError):
            normalize_image(buf.getvalue(), "bomb.png")
    finally:
        Image.MAX_IMAGE_PIXELS = original
