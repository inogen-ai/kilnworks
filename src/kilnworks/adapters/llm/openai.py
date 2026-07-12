import openai
from openai import OpenAI

from kilnworks.core.errors import ProviderError, TransientProviderError
from kilnworks.core.models import Completion
from kilnworks.core.retry import retry_with_backoff

_TRANSIENT = (openai.APIConnectionError, openai.RateLimitError, openai.InternalServerError)


class OpenAIChat:
    def __init__(self, api_key: str, model: str = "gpt-4o-mini", client=None):
        self._client = client or OpenAI(api_key=api_key, max_retries=0)
        self._model = model

    def complete(self, system: str, user: str) -> Completion:
        return retry_with_backoff(lambda: self._complete_once(system, user))

    def _complete_once(self, system: str, user: str) -> Completion:
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
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

    def stream(self, system: str, user: str):
        # _stream_once buffers the full provider stream before anything is
        # emitted, so transient failures (including mid-stream) are safely
        # retried from scratch without double-emitting deltas.
        yield from retry_with_backoff(lambda: self._stream_once(system, user))

    def _stream_once(self, system: str, user: str) -> list[str | Completion]:
        try:
            chunks = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                stream=True,
                stream_options={"include_usage": True},
            )
            events: list[str | Completion] = []
            parts: list[str] = []
            resolved_model = self._model
            input_tokens = output_tokens = 0
            for chunk in chunks:
                if getattr(chunk, "model", None):
                    resolved_model = chunk.model
                if chunk.choices and chunk.choices[0].delta.content:
                    events.append(chunk.choices[0].delta.content)
                    parts.append(chunk.choices[0].delta.content)
                if getattr(chunk, "usage", None):
                    input_tokens = chunk.usage.prompt_tokens
                    output_tokens = chunk.usage.completion_tokens
        except _TRANSIENT as exc:
            raise TransientProviderError("openai", str(exc)) from exc
        except openai.OpenAIError as exc:
            raise ProviderError("openai", str(exc)) from exc
        events.append(
            Completion(
                text="".join(parts),
                model=resolved_model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        )
        return events
