from collections.abc import Sequence

import openai
from openai import OpenAI

from kilnworks.core.errors import ProviderError, TransientProviderError
from kilnworks.core.models import EmbeddingBatch
from kilnworks.core.retry import retry_with_backoff

_TRANSIENT = (openai.APIConnectionError, openai.RateLimitError, openai.InternalServerError)

# OpenAI's embeddings endpoint rejects (400) any single request whose inputs
# sum to more than 300k tokens, and separately caps a request at 2048 inputs.
# A large document chunks into enough spans to blow past the token ceiling, so
# we split the inputs across sub-requests. We have no tokenizer on hand — adding
# tiktoken would pull a dependency that fetches vocab files over the network on
# first use, breaking Kilnworks' offline/Docker story — so we bound the token
# count by UTF-8 byte length instead: every BPE token encodes at least one byte,
# so a text of N bytes is at most N tokens. Budgeting by byte count is therefore
# a provably-safe over-estimate for ANY script (ASCII, code, CJK, emoji) — a
# character-count estimate is NOT: cl100k_base spends 1-2+ tokens per CJK
# character and up to ~4 per emoji, so counting chars would under-count and a
# large non-Latin document would still 400. Byte counting just yields somewhat
# smaller batches for ASCII text, which is harmless for ingestion. Upstream
# chunking caps a span at ~1200 chars, so no single input approaches the 8k
# per-input limit; only the aggregate matters here.
_MAX_TOKENS_PER_REQUEST = 250_000  # margin under OpenAI's 300k hard limit
_MAX_INPUTS_PER_REQUEST = 2048


def _token_estimate(text: str) -> int:
    """Upper bound on the token count of `text`: its UTF-8 byte length (tokens
    are >= 1 byte each). At least 1 so empty strings still consume a slot."""
    return max(1, len(text.encode("utf-8")))


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
        # Split into sub-requests so no single call exceeds the provider's
        # per-request token/input ceilings, then stitch the results back into
        # one batch. Each sub-request retries transient errors independently.
        vectors: list[list[float]] = []
        total_tokens = 0
        for group in _sub_batches(list(texts)):
            result = retry_with_backoff(lambda g=group: self._embed_once(g))
            vectors.extend(result.vectors)
            total_tokens += result.total_tokens
        return EmbeddingBatch(vectors=vectors, total_tokens=total_tokens)

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
        # Vectors are stitched across sub-requests into chunk-aligned positions,
        # so a mis-ordered response would silently bind embeddings to the wrong
        # chunks. The API returns a per-item `index`; sort by it defensively
        # rather than trusting arrival order (missing index -> treat as 0/stable).
        ordered = sorted(response.data, key=lambda item: getattr(item, "index", 0))
        vectors = [item.embedding for item in ordered]
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


def _sub_batches(texts: list[str]) -> list[list[str]]:
    """Greedily group `texts` so each group stays under both the per-request
    input-count cap and the estimated-token budget. A group always holds at
    least one text, so a single oversized input still ships (and fails loudly
    at the provider) rather than being silently dropped."""
    groups: list[list[str]] = []
    current: list[str] = []
    current_tokens = 0
    for text in texts:
        est = _token_estimate(text)
        if current and (
            len(current) >= _MAX_INPUTS_PER_REQUEST
            or current_tokens + est > _MAX_TOKENS_PER_REQUEST
        ):
            groups.append(current)
            current = []
            current_tokens = 0
        current.append(text)
        current_tokens += est
    if current:
        groups.append(current)
    return groups
