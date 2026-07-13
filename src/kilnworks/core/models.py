from uuid import UUID, uuid4

from pydantic import BaseModel, Field

DOC_STATUS_PENDING = "pending"
DOC_STATUS_READY = "ready"
DOC_STATUS_FAILED = "failed"

CONNECTOR_STATUS_READY = "ready"
CONNECTOR_STATUS_NEEDS_LOGIN = "needs_login"
CONNECTOR_STATUS_DOWN = "down"


class Completion(BaseModel):
    text: str
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    # Set by `parse_file` on vision/transcription extraction Completions ("vision" or
    # "transcription") so `IngestionService.ingest` can record accurate cost context
    # without threading extra state through `Document.extraction_usage`. Empty ("") for
    # ordinary chat/embedding Completions, which never travel through extraction_usage.
    context: str = ""


class Document(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    source_uri: str
    title: str
    text: str
    acl_tags: list[str] = ["public"]
    # Completions spent extracting `text` from non-text media (vision/transcription),
    # attached here so `IngestionService.ingest` — the one place that knows the
    # ingesting `user_id` — can record their cost. Empty for text/tables/pdf/etc.
    extraction_usage: list[Completion] = []


class Chunk(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    document_id: UUID
    ordinal: int
    text: str
    heading_path: list[str] = []
    acl_tags: list[str] = ["public"]


class RetrievedChunk(Chunk):
    score: float
    source_uri: str
    title: str


class Citation(BaseModel):
    index: int
    chunk_id: UUID
    source_uri: str
    title: str


class Answer(BaseModel):
    text: str
    citations: list[Citation]
    model: str = ""


class SourceFailure(BaseModel):
    source_uri: str
    error: str


class IngestReport(BaseModel):
    succeeded: int = 0
    failed: list[tuple[str, str]] = []


class EmbeddingBatch(BaseModel):
    vectors: list[list[float]]
    total_tokens: int = 0


class ConnectorResult(BaseModel):
    title: str
    text: str
    link: str | None = None
    connector: str
