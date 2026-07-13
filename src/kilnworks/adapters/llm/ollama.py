import json

import httpx

from kilnworks.core.errors import ProviderError, TransientProviderError
from kilnworks.core.models import Completion
from kilnworks.core.retry import retry_with_backoff


class OllamaChat:
    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "llama3.2",
        client: httpx.Client | None = None,
        num_ctx: int = 8192,
        timeout: float = 300.0,
    ):
        self._client = client or httpx.Client(
            base_url=base_url, timeout=httpx.Timeout(timeout, connect=10.0)
        )
        self._model = model
        self._num_ctx = num_ctx

    def complete(self, system: str, user: str) -> Completion:
        return retry_with_backoff(lambda: self._complete_once(system, user))

    def _complete_once(self, system: str, user: str) -> Completion:
        try:
            response = self._client.post(
                "/api/chat",
                json={
                    "model": self._model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "stream": False,
                    "options": {"num_ctx": self._num_ctx},
                },
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise translate_status(exc) from exc
        except httpx.TransportError as exc:
            raise TransientProviderError("ollama", str(exc)) from exc
        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError("ollama", f"invalid response: {exc}") from exc
        return Completion(
            text=data.get("message", {}).get("content", ""),
            model=data.get("model") or self._model,
            input_tokens=data.get("prompt_eval_count", 0),
            output_tokens=data.get("eval_count", 0),
        )

    def stream(self, system: str, user: str):
        # _stream_once buffers the full provider stream before anything is
        # emitted, so transient failures (including mid-stream) are safely
        # retried from scratch without double-emitting deltas.
        yield from retry_with_backoff(lambda: self._stream_once(system, user))

    def _stream_once(self, system: str, user: str) -> list[str | Completion]:
        try:
            response = self._client.post(
                "/api/chat",
                json={
                    "model": self._model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "stream": True,
                    "options": {"num_ctx": self._num_ctx},
                },
            )
            response.raise_for_status()
            events: list[str | Completion] = []
            parts: list[str] = []
            resolved_model = self._model
            input_tokens = output_tokens = 0
            for line in response.iter_lines():
                if not line:
                    continue
                line_data = json.loads(line)
                content = line_data.get("message", {}).get("content")
                if content:
                    events.append(content)
                    parts.append(content)
                if line_data.get("done"):
                    resolved_model = line_data.get("model") or resolved_model
                    input_tokens = line_data.get("prompt_eval_count", 0)
                    output_tokens = line_data.get("eval_count", 0)
        except httpx.HTTPStatusError as exc:
            raise translate_status(exc) from exc
        except httpx.TransportError as exc:
            raise TransientProviderError("ollama", str(exc)) from exc
        except ValueError as exc:  # malformed JSONL line from a proxy or crashed daemon
            raise ProviderError("ollama", f"invalid response: {exc}") from exc
        events.append(
            Completion(
                text="".join(parts),
                model=resolved_model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        )
        return events


def translate_status(exc: httpx.HTTPStatusError) -> ProviderError:
    status = exc.response.status_code
    detail = str(exc)
    if exc.response.text:  # Ollama puts the useful hint ("try pulling it") in the body
        detail = f"{detail}: {exc.response.text[:200]}"
    if status >= 500 or status == 429:
        return TransientProviderError("ollama", detail)
    return ProviderError("ollama", detail)
