import anthropic
from anthropic import Anthropic

from kilnworks.core.errors import ProviderError, TransientProviderError
from kilnworks.core.models import Completion
from kilnworks.core.retry import retry_with_backoff

_TRANSIENT = (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.InternalServerError)


class AnthropicChat:
    """LLMProvider backed by the Anthropic Messages API.

    Deliberately never sends a `thinking` parameter: the configured model is
    operator-chosen and may or may not support extended/adaptive thinking, so
    this adapter stays model-agnostic rather than special-casing model IDs.
    """

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

    def complete(self, system: str, user: str) -> Completion:
        return retry_with_backoff(lambda: self._complete_once(system, user))

    def _complete_once(self, system: str, user: str) -> Completion:
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
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

    def stream(self, system: str, user: str):
        # _stream_once buffers the full provider stream before anything is
        # emitted, so transient failures (including mid-stream) are safely
        # retried from scratch without double-emitting deltas.
        yield from retry_with_backoff(lambda: self._stream_once(system, user))

    def _stream_once(self, system: str, user: str) -> list[str | Completion]:
        try:
            events: list[str | Completion] = []
            with self._client.messages.stream(
                model=self._model,
                max_tokens=self._max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            ) as stream:
                for delta in stream.text_stream:
                    events.append(delta)
                final = stream.get_final_message()
        except _TRANSIENT as exc:
            raise TransientProviderError("anthropic", str(exc)) from exc
        except anthropic.APIError as exc:
            raise ProviderError("anthropic", str(exc)) from exc
        text = "".join(block.text for block in final.content if block.type == "text")
        usage = final.usage
        events.append(
            Completion(
                text=text,
                model=final.model or self._model,
                input_tokens=usage.input_tokens if usage else 0,
                output_tokens=usage.output_tokens if usage else 0,
            )
        )
        return events
