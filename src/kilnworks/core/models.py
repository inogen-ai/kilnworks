from uuid import UUID, uuid4

from pydantic import BaseModel, Field

DOC_STATUS_PENDING = "pending"
DOC_STATUS_READY = "ready"
DOC_STATUS_FAILED = "failed"


class Document(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    source_uri: str
    title: str
    text: str
    acl_tags: list[str] = ["public"]


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


class Completion(BaseModel):
    text: str
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
