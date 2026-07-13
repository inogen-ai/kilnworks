import base64

import openai
from openai import OpenAI

from kilnworks.adapters.media.imaging import normalize_image
from kilnworks.core.errors import ProviderError, TransientProviderError
from kilnworks.core.models import Completion
from kilnworks.core.retry import retry_with_backoff

_TRANSIENT = (openai.APIConnectionError, openai.RateLimitError, openai.InternalServerError)

VISION_PROMPT = "Describe this image in detail. Transcribe any visible text verbatim."


class OpenAIVision:
    """VisionExtractor backed by the OpenAI chat completions API (gpt-4o family)."""

    def __init__(self, api_key: str, model: str = "gpt-4o-mini", client=None):
        self._client = client or OpenAI(api_key=api_key, max_retries=0)
        self._model = model

    def describe(self, image: bytes, mime: str, name: str) -> Completion:
        encoded, out_mime = normalize_image(image, name)
        data_uri = f"data:{out_mime};base64,{base64.b64encode(encoded).decode()}"
        return retry_with_backoff(lambda: self._describe_once(data_uri))

    def _describe_once(self, data_uri: str) -> Completion:
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": VISION_PROMPT},
                            {"type": "image_url", "image_url": {"url": data_uri}},
                        ],
                    }
                ],
            )
        except _TRANSIENT as exc:
            raise TransientProviderError("openai", str(exc)) from exc
        except openai.OpenAIError as exc:
            raise ProviderError("openai", str(exc)) from exc
        usage = response.usage
        return Completion(
            text=response.choices[0].message.content or "",
            model=getattr(response, "model", None) or self._model,
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
        )
