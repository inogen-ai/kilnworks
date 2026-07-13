from uuid import uuid4

from kilnworks.adapters.embedders.fake import FakeEmbedder
from kilnworks.adapters.pgvector_store import PgVectorStore
from kilnworks.core.models import Chunk, Document


def _seed(store, embedder, text, acl_tags):
    doc = Document(source_uri=f"file:///{uuid4()}.md", title="doc", text=text, acl_tags=acl_tags)
    doc_id = store.upsert_document(doc)
    chunk = Chunk(document_id=doc_id, ordinal=0, text=text, acl_tags=acl_tags)
    store.upsert_chunks([chunk], embedder.embed([text]).vectors)
    store.mark_document(doc_id, "ready")
    return doc_id


def test_search_finds_exact_text_as_top_hit(conn):
    store, embedder = PgVectorStore(conn), FakeEmbedder()
    _seed(store, embedder, "the kiln fires at 1300 degrees", ["public"])
    _seed(store, embedder, "unrelated onboarding notes", ["public"])
    hits = store.search(embedder.embed(["the kiln fires at 1300 degrees"]).vectors[0], ["public"])
    assert hits[0].text == "the kiln fires at 1300 degrees"
    assert hits[0].score > hits[-1].score or len(hits) == 1


def test_search_filters_by_acl(conn):
    store, embedder = PgVectorStore(conn), FakeEmbedder()
    _seed(store, embedder, "salary bands for 2026", ["hr"])
    public_hits = store.search(embedder.embed(["salary bands for 2026"]).vectors[0], ["public"])
    hr_hits = store.search(embedder.embed(["salary bands for 2026"]).vectors[0], ["hr", "public"])
    assert all(hit.text != "salary bands for 2026" for hit in public_hits)
    assert any(hit.text == "salary bands for 2026" for hit in hr_hits)


def test_reingest_same_source_uri_replaces_chunks(conn):
    store, embedder = PgVectorStore(conn), FakeEmbedder()
    doc = Document(source_uri="file:///same.md", title="v1", text="old text")
    first_id = store.upsert_document(doc)
    store.upsert_chunks(
        [Chunk(document_id=first_id, ordinal=0, text="old text")],
        embedder.embed(["old text"]).vectors,
    )
    second_id = store.upsert_document(
        Document(source_uri="file:///same.md", title="v2", text="new text")
    )
    assert second_id == first_id
    count = conn.execute("SELECT count(*) FROM chunks WHERE document_id = %s", (first_id,))
    assert count.fetchone()[0] == 0


def test_mark_document_records_status_and_error(conn):
    store = PgVectorStore(conn)
    doc_id = store.upsert_document(Document(source_uri="file:///x.md", title="x", text="t"))
    store.mark_document(doc_id, "failed", error="corrupt file")
    row = conn.execute("SELECT status, error FROM documents WHERE id = %s", (doc_id,)).fetchone()
    assert row == ("failed", "corrupt file")


def test_search_excludes_chunks_of_non_ready_documents(conn):
    store, embedder = PgVectorStore(conn), FakeEmbedder()

    pending_doc = Document(
        source_uri=f"file:///{uuid4()}.md", title="pending", text="kiln firing schedule pending"
    )
    pending_id = store.upsert_document(pending_doc)
    store.upsert_chunks(
        [Chunk(document_id=pending_id, ordinal=0, text="kiln firing schedule pending")],
        embedder.embed(["kiln firing schedule pending"]).vectors,
    )
    # left as "pending" (the default) -- no mark_document call

    failed_doc = Document(
        source_uri=f"file:///{uuid4()}.md", title="failed", text="kiln firing schedule failed"
    )
    failed_id = store.upsert_document(failed_doc)
    store.upsert_chunks(
        [Chunk(document_id=failed_id, ordinal=0, text="kiln firing schedule failed")],
        embedder.embed(["kiln firing schedule failed"]).vectors,
    )
    store.mark_document(failed_id, "failed", error="boom")

    ready_id = _seed(store, embedder, "kiln firing schedule ready", ["public"])

    hits = store.search(embedder.embed(["kiln firing schedule"]).vectors[0], ["public"], limit=10)
    assert {hit.document_id for hit in hits} == {ready_id}


def test_search_result_carries_document_metadata(conn):
    store, embedder = PgVectorStore(conn), FakeEmbedder()
    _seed(store, embedder, "observability guide", ["public"])
    hit = store.search(embedder.embed(["observability guide"]).vectors[0], ["public"])[0]
    assert hit.source_uri.startswith("file:///")
    assert hit.title == "doc"


def test_delete_document_removes_row_and_returns_true(conn):
    store = PgVectorStore(conn)
    doc_id = store.upsert_document(
        Document(source_uri="file:///delete-me.md", title="d", text="t", acl_tags=["public"])
    )
    assert store.delete_document(doc_id, ["public"]) is True
    row = conn.execute("SELECT id FROM documents WHERE id = %s", (doc_id,)).fetchone()
    assert row is None


def test_delete_document_returns_false_when_principals_dont_match(conn):
    store = PgVectorStore(conn)
    doc_id = store.upsert_document(
        Document(source_uri="file:///keep-me.md", title="d", text="t", acl_tags=["sales"])
    )
    assert store.delete_document(doc_id, ["public"]) is False
    row = conn.execute("SELECT id FROM documents WHERE id = %s", (doc_id,)).fetchone()
    assert row is not None


def test_delete_document_chunks_removes_vectors(conn):
    store, embedder = PgVectorStore(conn), FakeEmbedder()
    doc_id = _seed(store, embedder, "chunks to be deleted", ["public"])
    store.delete_document_chunks(doc_id)
    hits = store.search(embedder.embed(["chunks to be deleted"]).vectors[0], ["public"])
    assert all(hit.document_id != doc_id for hit in hits)


def test_record_ingest_failure_covers_all_three_status_branches(conn):
    store = PgVectorStore(conn)

    store.record_ingest_failure("file:///new.md", "parse blew up")
    row = conn.execute(
        "SELECT title, status, error FROM documents WHERE source_uri = 'file:///new.md'"
    ).fetchone()
    assert row == ("new", "failed", "parse blew up")

    store.record_ingest_failure("file:///new.md", "failed again")
    row = conn.execute(
        "SELECT status, error FROM documents WHERE source_uri = 'file:///new.md'"
    ).fetchone()
    assert row == ("failed", "failed again")   # non-ready row: stays/becomes failed

    doc = Document(source_uri="file:///live.md", title="live", text="v1")
    doc_id = store.upsert_document(doc)
    store.mark_document(doc_id, "ready")
    store.record_ingest_failure("file:///live.md", "reingest exploded")
    row = conn.execute(
        "SELECT status, error FROM documents WHERE source_uri = 'file:///live.md'"
    ).fetchone()
    assert row == ("ready", "reingest exploded")   # ready row survives with error noted
