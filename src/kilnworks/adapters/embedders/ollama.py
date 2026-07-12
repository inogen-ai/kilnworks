from collections.abc import Sequence

import httpx

from kilnworks.core.errors import ProviderError, TransientProviderError
from kilnworks.core.models import EmbeddingBatch
from kilnworks.core.retry import retry_with_backoff


class OllamaEmbedder:
    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "nomic-embed-text",
        dimension: int = 768,
        client: httpx.Client | None = None,
        timeout: float = 300.0,
    ):
        self._client = client or httpx.Client(
            base_url=base_url, timeout=httpx.Timeout(timeout, connect=10.0)
        )
        self._model = model
        self.model_name = model
        self.dimension = dimension

    def embed(self, texts: Sequence[str]) -> EmbeddingBatch:
        return retry_with_backoff(lambda: self._embed_once(texts))

    def _embed_once(self, texts: Sequence[str]) -> EmbeddingBatch:
        try:
            response = self._client.post(
                "/api/embed",
                json={"model": self._model, "input": list(texts)},
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise _translate_status(exc) from exc
        except httpx.TransportError as exc:
            raise TransientProviderError("ollama", str(exc)) from exc
        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError("ollama", f"invalid response: {exc}") from exc
        vectors = data["embeddings"]
        if vectors and len(vectors[0]) != self.dimension:
            raise ValueError(
                f"ollama embedder returned vectors of dimension {len(vectors[0])}, "
                f"but {self.dimension} was configured; set KILNWORKS_EMBEDDING_DIMENSIONS "
                f"to {len(vectors[0])} and re-run init-db"
            )
        return EmbeddingBatch(vectors=vectors, total_tokens=data.get("prompt_eval_count", 0))


def _translate_status(exc: httpx.HTTPStatusError) -> ProviderError:
    status = exc.response.status_code
    detail = str(exc)
    if exc.response.text:  # Ollama puts the useful hint ("try pulling it") in the body
        detail = f"{detail}: {exc.response.text[:200]}"
    if status >= 500 or status == 429:
        return TransientProviderError("ollama", detail)
    return ProviderError("ollama", detail)
