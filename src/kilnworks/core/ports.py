from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
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

    def delete_document(self, document_id: UUID, principals: Sequence[str]) -> bool: ...


class VectorIndex(Protocol):
    def upsert_chunks(
        self, chunks: Sequence[Chunk], embeddings: Sequence[Sequence[float]]
    ) -> None: ...

    def search(
        self,
        embedding: Sequence[float],
        principals: Sequence[str],
        limit: int = 8,
        source_ids: Sequence[UUID] | None = None,
    ) -> list[RetrievedChunk]: ...

    def delete_document_chunks(self, document_id: UUID) -> None: ...


class LLMProvider(Protocol):
    def complete(self, system: str, user: str) -> Completion: ...

    def stream(self, system: str, user: str) -> Iterator[str | Completion]: ...


class VisionExtractor(Protocol):
    def describe(self, image: bytes, mime: str, name: str) -> Completion: ...


class Transcriber(Protocol):
    def transcribe(self, media: bytes, mime: str, name: str) -> Completion: ...


DEFAULT_MAX_MEDIA_BYTES = 104_857_600


@dataclass
class MediaExtractor:
    """Injection seam for image/audio/video extraction, threaded from wiring through
    the sources into `parse_file`. `vision`/`transcription` are None until a real
    provider is configured (M6 Tasks 3/4) or `KILNWORKS_FAKE_PROVIDERS` supplies fakes;
    `parse_file` turns an absent provider into an actionable `MediaProviderRequired`."""

    vision: VisionExtractor | None = None
    transcription: Transcriber | None = None
    max_bytes: int = DEFAULT_MAX_MEDIA_BYTES


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
