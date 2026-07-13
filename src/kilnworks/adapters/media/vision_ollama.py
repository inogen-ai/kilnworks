import base64

import httpx

from kilnworks.adapters.llm.ollama import translate_status
from kilnworks.adapters.media.imaging import normalize_image
from kilnworks.core.errors import ProviderError, TransientProviderError
from kilnworks.core.models import Completion
from kilnworks.core.retry import retry_with_backoff

VISION_PROMPT = "Describe this image in detail. Transcribe any visible text verbatim."


class OllamaVision:
    """VisionExtractor backed by a local Ollama server's `/api/chat` with an
    `images` field (llava family), same raw-httpx style as `OllamaChat`."""

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "llava",
        client: httpx.Client | None = None,
        timeout: float = 300.0,
    ):
        self._client = client or httpx.Client(
            base_url=base_url, timeout=httpx.Timeout(timeout, connect=10.0)
        )
        self._model = model

    def describe(self, image: bytes, mime: str, name: str) -> Completion:
        encoded, _ = normalize_image(image, name)
        data = base64.b64encode(encoded).decode()
        return retry_with_backoff(lambda: self._describe_once(data))

    def _describe_once(self, data: str) -> Completion:
        try:
            response = self._client.post(
                "/api/chat",
                json={
                    "model": self._model,
                    "messages": [
                        {"role": "user", "content": VISION_PROMPT, "images": [data]},
                    ],
                    "stream": False,
                },
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise translate_status(exc) from exc
        except httpx.TransportError as exc:
            raise TransientProviderError("ollama", str(exc)) from exc
        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderError("ollama", f"invalid response: {exc}") from exc
        return Completion(
            text=body.get("message", {}).get("content", ""),
            model=body.get("model") or self._model,
            input_tokens=body.get("prompt_eval_count", 0),
            output_tokens=body.get("eval_count", 0),
        )
