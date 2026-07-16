from uuid import uuid4

from kilnworks.core.chunking import HeadingAwareChunker
from kilnworks.core.models import (
    DOC_STATUS_READY,
    Chunk,
    IngestReport,
    SourceFailure,
)
from kilnworks.core.ports import CostRecorder, DocumentSource, DocumentStore, Embedder, VectorIndex


class IngestionService:
    def __init__(
        self,
        store: DocumentStore | VectorIndex,
        chunker: HeadingAwareChunker,
        embedder: Embedder,
        cost: CostRecorder | None = None,
    ):
        self._store = store
        self._chunker = chunker
        self._embedder = embedder
        self._cost = cost

    def ingest(self, source: DocumentSource, user_id: str | None = None) -> IngestReport:
        report = IngestReport()
        for item in self._safe_documents(source, report):
            if isinstance(item, SourceFailure):
                report.failed.append((item.source_uri, item.error))
                continue
            doc = item
            try:
                if self._cost:
                    for completion in doc.extraction_usage:
                        # Vision/transcription usage was already spent inside the
                        # source's parse_file call, before this document ever reached
                        # the service; recorded here (the one place with `user_id`)
                        # regardless of whether persistence below later fails.
                        # Extraction is not cached across runs: re-ingesting the same
                        # media file re-invokes the provider and bills again (see
                        # docs/limitations.md) — the store is only reached below, after
                        # the cost has already been incurred upstream.
                        # `completion.context` was tagged by parse_file ("vision" or
                        # "transcription") so the two extraction kinds land in the
                        # ledger under their own kind/context rather than both being
                        # mislabeled "vision".
                        context = completion.context or "vision"
                        self._cost.record_cost(
                            context, completion.model,
                            completion.input_tokens, completion.output_tokens,
                            context, user_id=user_id,
                        )
                spans = self._chunker.chunk(doc.text)
                doc.metadata = {**doc.metadata, "chunk_count": len(spans)}
                batch = None
                if spans:
                    batch = self._embedder.embed([span.text for span in spans])
                    if self._cost:
                        self._cost.record_cost(
                            "embedding", self._embedder.model_name,
                            batch.total_tokens, 0, "ingest", user_id=user_id,
                        )
                with self._store.transaction():
                    doc_id = self._store.upsert_document(doc)
                    if spans:
                        chunks = [
                            Chunk(
                                id=uuid4(),
                                document_id=doc_id,
                                ordinal=ordinal,
                                text=span.text,
                                heading_path=list(span.heading_path),
                                acl_tags=doc.acl_tags,
                                page=span.page,
                            )
                            for ordinal, span in enumerate(spans)
                        ]
                        self._store.upsert_chunks(chunks, batch.vectors)
                    self._store.mark_document(doc_id, DOC_STATUS_READY)
                report.succeeded += 1
            except Exception as exc:  # rollback preserved the previous version, if any
                try:
                    self._store.record_ingest_failure(doc.source_uri, str(exc))
                except Exception:  # noqa: BLE001 - recording must never kill the batch
                    pass
                report.failed.append((doc.source_uri, str(exc)))
        return report

    @staticmethod
    def _safe_documents(source: DocumentSource, report: IngestReport):
        """Pull from the source one item at a time so a read error skips only that document."""
        iterator = source.documents()
        while True:
            try:
                yield next(iterator)
            except StopIteration:
                return
            except Exception as exc:
                report.failed.append(("<unreadable source item>", str(exc)))
