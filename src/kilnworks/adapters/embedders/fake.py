import hashlib
import math
from collections.abc import Sequence

from kilnworks.core.models import EmbeddingBatch


class FakeEmbedder:
    """Deterministic embedder for tests: same text always maps to the same unit vector."""

    model_name = "fake-embedder"

    def __init__(self, dimension: int = 1536):
        self.dimension = dimension

    def embed(self, texts: Sequence[str]) -> EmbeddingBatch:
        return EmbeddingBatch(
            vectors=[self._one(text) for text in texts],
            total_tokens=sum(max(1, len(text.split())) for text in texts),
        )

    def _one(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        raw = [digest[i % len(digest)] / 255.0 for i in range(self.dimension)]
        # sha256 gives 32 bytes; vary the repetition so the vector isn't purely periodic
        raw = [v * ((i // len(digest)) % 7 + 1) for i, v in enumerate(raw)]
        norm = math.sqrt(sum(v * v for v in raw)) or 1.0
        return [v / norm for v in raw]
