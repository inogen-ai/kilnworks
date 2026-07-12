from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager
from typing import Protocol
from uuid import UUID

from kilnworks.core.models import (
    Chunk,
    Completion,
    Document,
    EmbeddingBatch,
    RetrievedChunk,
    SourceFailure,
)


class Embedder(Protocol):
    dimension: int
    model_name: str

    def embed(self, texts: Sequence[str]) -> EmbeddingBatch: ...


class DocumentSource(Protocol):
    def documents(self) -> Iterator[Document | SourceFailure]: ...


class DocumentStore(Protocol):
    def upsert_document(self, doc: Document) -> UUID: ...

    def mark_document(self, document_id: UUID, status: str, error: str | None = None) -> None: ...

    def transaction(self) -> AbstractContextManager: ...

    def record_ingest_failure(self, source_uri: str, error: str) -> None: ...


class VectorIndex(Protocol):
    def upsert_chunks(
        self, chunks: Sequence[Chunk], embeddings: Sequence[Sequence[float]]
    ) -> None: ...

    def search(
        self, embedding: Sequence[float], principals: Sequence[str], limit: int = 8
    ) -> list[RetrievedChunk]: ...


class LLMProvider(Protocol):
    def complete(self, system: str, user: str) -> Completion: ...

    def stream(self, system: str, user: str) -> Iterator[str | Completion]: ...


class CostRecorder(Protocol):
    def record_cost(
        self,
        kind: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        context: str,
        user_id: str | None = None,
    ) -> None: ...
