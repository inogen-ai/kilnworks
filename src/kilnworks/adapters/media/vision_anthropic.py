import base64

import anthropic
from anthropic import Anthropic

from kilnworks.adapters.media.imaging import normalize_image
from kilnworks.core.errors import ProviderError, TransientProviderError
from kilnworks.core.models import Completion
from kilnworks.core.retry import retry_with_backoff

_TRANSIENT = (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.InternalServerError)

VISION_PROMPT = "Describe this image in detail. Transcribe any visible text verbatim."


class AnthropicVision:
    """VisionExtractor backed by the Anthropic Messages API image content block."""

    def __init__(
        self,
        api_key: str,
        model: str = "claude-opus-4-8",
        max_tokens: int = 2048,
        client=None,
    ):
        self._client = client or Anthropic(api_key=api_key, max_retries=0)
        self._model = model
        self._max_tokens = max_tokens

    def describe(self, image: bytes, mime: str, name: str) -> Completion:
        encoded, out_mime = normalize_image(image, name)
        data = base64.b64encode(encoded).decode()
        return retry_with_backoff(lambda: self._describe_once(data, out_mime))

    def _describe_once(self, data: str, mime: str) -> Completion:
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": mime,
                                    "data": data,
                                },
                            },
                            {"type": "text", "text": VISION_PROMPT},
                        ],
                    }
                ],
            )
        except _TRANSIENT as exc:
            raise TransientProviderError("anthropic", str(exc)) from exc
        except anthropic.APIError as exc:
            raise ProviderError("anthropic", str(exc)) from exc
        text = "".join(block.text for block in response.content if block.type == "text")
        usage = response.usage
        return Completion(
            text=text,
            model=response.model or self._model,
            input_tokens=usage.input_tokens if usage else 0,
            output_tokens=usage.output_tokens if usage else 0,
        )
