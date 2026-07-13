from uuid import uuid4

from fastapi.testclient import TestClient

from kilnworks.adapters.pgvector_store import PgVectorStore
from kilnworks.core.models import Document
from kilnworks.db.connection import connect
from kilnworks.settings import Settings

from tests.api.test_ask_endpoints import _seed_doc
from tests.api.test_upload_endpoints import _headers


def _seed_ready_doc_with_file(api_settings: Settings, uploads_dir, filename, acl_tags):
    file_path = uploads_dir / filename
    file_path.write_text("body")
    conn = connect(api_settings.database_url)
    store = PgVectorStore(conn)
    doc_id = store.upsert_document(
        Document(
            source_uri=file_path.resolve().as_uri(),
            title=filename,
            text="body",
            acl_tags=list(acl_tags),
        )
    )
    store.mark_document(doc_id, "ready")
    conn.close()
    return doc_id, file_path


def test_delete_document_removes_it_and_its_file(client, api_settings, tmp_path, monkeypatch):
    monkeypatch.setattr(api_settings, "data_dir", str(tmp_path))
    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir(parents=True)
    doc_id, file_path = _seed_ready_doc_with_file(
        api_settings, uploads_dir, "keep.md", ["public"]
    )
    assert file_path.exists()

    headers = _headers(client, api_settings, email="del1@example.com")
    response = client.delete(f"/documents/{doc_id}", headers=headers)
    assert response.status_code == 204

    remaining = client.get("/documents", headers=headers).json()
    assert remaining == []
    assert not file_path.exists()


def test_delete_missing_returns_404(client, api_settings):
    headers = _headers(client, api_settings, email="del2@example.com")
    response = client.delete(f"/documents/{uuid4()}", headers=headers)
    assert response.status_code == 404


def test_delete_unauthorized_returns_404(client, api_settings):
    _seed_doc(api_settings, "salary bands 2026", ["hr"])
    headers = _headers(client, api_settings, principals=("public",), email="del3@example.com")
    conn = connect(api_settings.database_url)
    doc_id = conn.execute(
        "SELECT id FROM documents WHERE source_uri = 'file:///salary b.md'"
    ).fetchone()[0]
    conn.close()

    response = client.delete(f"/documents/{doc_id}", headers=headers)
    assert response.status_code == 404


def test_delete_is_transactional(client, api_settings, monkeypatch):
    _seed_doc(api_settings, "kiln maintenance log", ["public"])
    headers = _headers(client, api_settings, email="del4@example.com")
    conn = connect(api_settings.database_url)
    doc_id = conn.execute(
        "SELECT id FROM documents WHERE source_uri = 'file:///kiln mai.md'"
    ).fetchone()[0]
    conn.close()

    def _boom(self, document_id):
        raise RuntimeError("boom")

    monkeypatch.setattr(PgVectorStore, "delete_document_chunks", _boom)

    unsafe_client = TestClient(client.app, raise_server_exceptions=False)
    response = unsafe_client.delete(f"/documents/{doc_id}", headers=headers)
    assert response.status_code == 500

    conn = connect(api_settings.database_url)
    row = conn.execute("SELECT id FROM documents WHERE id = %s", (doc_id,)).fetchone()
    conn.close()
    assert row is not None
