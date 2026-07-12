from kilnworks.adapters.embedders.fake import FakeEmbedder
from kilnworks.adapters.pgvector_store import PgVectorStore
from kilnworks.adapters.sources.localfolder import LocalFolderSource
from kilnworks.core.chunking import HeadingAwareChunker
from kilnworks.core.ingestion import IngestionService


class ExplodingEmbedder(FakeEmbedder):
    def embed(self, texts):
        if any("KABOOM" in t for t in texts):
            raise RuntimeError("embedder blew up")
        return super().embed(texts)


class FailingUpsertStore:
    """Delegates to a real store but explodes on a specific document title."""

    def __init__(self, inner, poison_title):
        self._inner = inner
        self._poison_title = poison_title

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def upsert_document(self, doc):
        if doc.title == self._poison_title:
            raise RuntimeError("upsert exploded")
        return self._inner.upsert_document(doc)


class FailingChunksStore:
    """Delegates to a real store but explodes when chunks are written."""

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def upsert_chunks(self, chunks, embeddings):
        raise RuntimeError("chunk write exploded")


def _service(conn, embedder=None):
    return IngestionService(
        store=PgVectorStore(conn),
        chunker=HeadingAwareChunker(),
        embedder=embedder or FakeEmbedder(),
    )


def test_ingests_folder_and_marks_documents_ready(conn, tmp_path):
    (tmp_path / "guide.md").write_text("# Guide\n\nKilns fire at 1300 degrees.")
    report = _service(conn).ingest(LocalFolderSource(tmp_path))
    assert report.succeeded == 1
    assert report.failed == []
    row = conn.execute("SELECT status FROM documents").fetchone()
    assert row[0] == "ready"
    assert conn.execute("SELECT count(*) FROM chunks").fetchone()[0] == 1


def test_one_bad_document_does_not_fail_the_batch(conn, tmp_path):
    (tmp_path / "good.md").write_text("All fine here.")
    (tmp_path / "bad.md").write_text("KABOOM")
    report = _service(conn, ExplodingEmbedder()).ingest(LocalFolderSource(tmp_path))
    assert report.succeeded == 1
    assert len(report.failed) == 1
    assert "embedder blew up" in report.failed[0][1]
    statuses = dict(conn.execute("SELECT title, status FROM documents").fetchall())
    assert statuses == {"good": "ready", "bad": "failed"}
    error = conn.execute("SELECT error FROM documents WHERE title = 'bad'").fetchone()[0]
    assert "embedder blew up" in error


def test_document_with_no_chunkable_text_is_ready_with_zero_chunks(conn, tmp_path):
    (tmp_path / "empty.md").write_text("# Title only")
    report = _service(conn).ingest(LocalFolderSource(tmp_path))
    assert report.succeeded == 1
    assert conn.execute("SELECT count(*) FROM chunks").fetchone()[0] == 0


def test_upsert_document_failure_does_not_fail_the_batch(conn, tmp_path):
    (tmp_path / "a-poison.md").write_text("first file")
    (tmp_path / "b-good.md").write_text("second file")
    store = FailingUpsertStore(PgVectorStore(conn), poison_title="a-poison")
    service = IngestionService(store=store, chunker=HeadingAwareChunker(), embedder=FakeEmbedder())
    report = service.ingest(LocalFolderSource(tmp_path))
    assert report.succeeded == 1
    assert len(report.failed) == 1
    assert "upsert exploded" in report.failed[0][1]
    statuses = dict(conn.execute("SELECT title, status FROM documents").fetchall())
    assert statuses == {"a-poison": "failed", "b-good": "ready"}


class RecordingCost:
    def __init__(self):
        self.events = []

    def record_cost(self, kind, model, input_tokens, output_tokens, context, user_id=None):
        self.events.append((kind, model, input_tokens, output_tokens, context, user_id))


def test_ingest_records_embedding_cost_per_document(conn, tmp_path):
    (tmp_path / "a.md").write_text("alpha doc")
    (tmp_path / "b.md").write_text("beta doc")
    cost = RecordingCost()
    service = IngestionService(
        store=PgVectorStore(conn),
        chunker=HeadingAwareChunker(),
        embedder=FakeEmbedder(),
        cost=cost,
    )
    service.ingest(LocalFolderSource(tmp_path))
    assert len(cost.events) == 2
    assert all(event[0] == "embedding" and event[4] == "ingest" for event in cost.events)


def test_source_failures_land_in_report_and_batch_continues(conn, tmp_path):
    (tmp_path / "a-bad.pdf").write_bytes(b"%PDF-not really")
    (tmp_path / "b-good.md").write_text("good content")
    report = _service(conn).ingest(LocalFolderSource(tmp_path))
    assert report.succeeded == 1
    assert len(report.failed) == 1
    assert report.failed[0][0].endswith("a-bad.pdf")


def test_failed_reingest_preserves_previous_ready_version(conn, tmp_path):
    (tmp_path / "doc.md").write_text("version one content")
    service = _service(conn)
    assert service.ingest(LocalFolderSource(tmp_path)).succeeded == 1

    (tmp_path / "doc.md").write_text("KABOOM version two")
    report = _service(conn, ExplodingEmbedder()).ingest(LocalFolderSource(tmp_path))
    assert report.succeeded == 0 and len(report.failed) == 1

    status, error = conn.execute("SELECT status, error FROM documents").fetchone()
    assert status == "ready"                      # old version still live
    assert "embedder blew up" in error            # failure recorded
    chunk_text = conn.execute("SELECT text FROM chunks").fetchone()[0]
    assert chunk_text == "version one content"    # old chunks intact


def test_store_failure_mid_transaction_rolls_back_to_previous_version(conn, tmp_path):
    (tmp_path / "doc.md").write_text("version one content")
    service = _service(conn)
    assert service.ingest(LocalFolderSource(tmp_path)).succeeded == 1

    (tmp_path / "doc.md").write_text("version two content")
    store = FailingChunksStore(PgVectorStore(conn))
    failing = IngestionService(
        store=store, chunker=HeadingAwareChunker(), embedder=FakeEmbedder()
    )
    report = failing.ingest(LocalFolderSource(tmp_path))
    assert report.succeeded == 0 and len(report.failed) == 1
    chunk_text = conn.execute("SELECT text FROM chunks").fetchone()[0]
    assert chunk_text == "version one content"    # rollback kept v1 chunks
    assert conn.execute("SELECT status FROM documents").fetchone()[0] == "ready"


def test_brand_new_document_failure_creates_failed_row(conn, tmp_path):
    (tmp_path / "fresh.md").write_text("KABOOM")
    report = _service(conn, ExplodingEmbedder()).ingest(LocalFolderSource(tmp_path))
    assert report.succeeded == 0
    row = conn.execute("SELECT status, error FROM documents").fetchone()
    assert row[0] == "failed" and "embedder blew up" in row[1]


def test_embedding_cost_recorded_even_when_persistence_fails(conn, tmp_path):
    (tmp_path / "doc.md").write_text("content that gets embedded")
    cost = RecordingCost()
    service = IngestionService(
        store=FailingChunksStore(PgVectorStore(conn)),
        chunker=HeadingAwareChunker(),
        embedder=FakeEmbedder(),
        cost=cost,
    )
    report = service.ingest(LocalFolderSource(tmp_path))
    assert report.succeeded == 0 and len(report.failed) == 1
    assert [event[0] for event in cost.events] == ["embedding"]  # spend still recorded


class ExplodingFailureRecorder:
    """Delegates to a real store but explodes when recording failures."""

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def record_ingest_failure(self, source_uri, error):
        raise RuntimeError("failure recorder is down")


def test_failure_recording_failure_does_not_abort_the_batch(conn, tmp_path):
    (tmp_path / "a-bad.md").write_text("KABOOM one")
    (tmp_path / "b-good.md").write_text("healthy doc")
    service = IngestionService(
        store=ExplodingFailureRecorder(PgVectorStore(conn)),
        chunker=HeadingAwareChunker(),
        embedder=ExplodingEmbedder(),
    )
    report = service.ingest(LocalFolderSource(tmp_path))
    assert report.succeeded == 1                      # healthy doc still lands
    assert len(report.failed) == 1
    assert "embedder blew up" in report.failed[0][1]  # original error, not recorder error
