from collections.abc import Sequence

import openai
from openai import OpenAI

from kilnworks.core.errors import ProviderError, TransientProviderError
from kilnworks.core.models import EmbeddingBatch
from kilnworks.core.retry import retry_with_backoff

_TRANSIENT = (openai.APIConnectionError, openai.RateLimitError, openai.InternalServerError)


class OpenAIEmbedder:
    def __init__(
        self,
        api_key: str,
        model: str = "text-embedding-3-small",
        client=None,
        dimension: int = 1536,
    ):
        self._client = client or OpenAI(api_key=api_key, max_retries=0)
        self._model = model
        self.model_name = model
        self.dimension = dimension

    def embed(self, texts: Sequence[str]) -> EmbeddingBatch:
        return retry_with_backoff(lambda: self._embed_once(texts))

    def _embed_once(self, texts: Sequence[str]) -> EmbeddingBatch:
        kwargs: dict = {"model": self._model, "input": list(texts)}
        if self.dimension != 1536:
            kwargs["dimensions"] = self.dimension
        try:
            response = self._client.embeddings.create(**kwargs)
        except _TRANSIENT as exc:
            raise TransientProviderError("openai", str(exc)) from exc
        except openai.OpenAIError as exc:
            raise ProviderError("openai", str(exc)) from exc
        vectors = [item.embedding for item in response.data]
        if vectors and len(vectors[0]) != self.dimension:
            raise ValueError(
                f"openai embedder returned vectors of dimension {len(vectors[0])}, "
                f"but {self.dimension} was configured; set KILNWORKS_EMBEDDING_DIMENSIONS "
                f"to {len(vectors[0])} and re-run init-db (then re-ingest)"
            )
        return EmbeddingBatch(
            vectors=vectors,
            total_tokens=response.usage.total_tokens if getattr(response, "usage", None) else 0,
        )
