from uuid import uuid4

from kilnworks.adapters.embedders.fake import FakeEmbedder
from kilnworks.adapters.pgvector_store import PgVectorStore
from kilnworks.core.models import Chunk, Document
from kilnworks.db.connection import connect

from tests.api.test_auth_endpoints import _register


def _token(client, email="mike@example.com", password="hunter2"):
    return client.post(
        "/auth/token", json={"email": email, "password": password}
    ).json()["access_token"]


def _seed_doc(api_settings, text, acl_tags):
    conn = connect(api_settings.database_url)
    store, embedder = PgVectorStore(conn), FakeEmbedder()
    doc = Document(source_uri=f"file:///{text[:8]}.md", title=text[:8], text=text,
                   acl_tags=acl_tags)
    doc_id = store.upsert_document(doc)
    chunk = Chunk(document_id=doc_id, ordinal=0, text=text, acl_tags=acl_tags)
    store.upsert_chunks([chunk], embedder.embed([text]).vectors)
    store.mark_document(doc_id, "ready")
    conn.close()
    return doc_id


def test_endpoints_require_auth(client):
    assert client.get("/documents").status_code == 401
    assert client.post("/ask", json={"question": "q"}).status_code == 401
    bad = {"Authorization": "Bearer garbage"}
    assert client.get("/documents", headers=bad).status_code == 401


def test_documents_lists_status(client, api_settings):
    _register(api_settings)
    _seed_doc(api_settings, "public knowledge", ["public"])
    headers = {"Authorization": f"Bearer {_token(client)}"}
    response = client.get("/documents", headers=headers)
    assert response.status_code == 200
    docs = response.json()
    assert len(docs) == 1 and docs[0]["status"] == "ready"


def test_documents_filters_by_acl(client, api_settings):
    _register(api_settings, email="pub@example.com", principals=("public",))
    _register(api_settings, email="hr@example.com", principals=("public", "hr"))
    _seed_doc(api_settings, "public knowledge", ["public"])
    _seed_doc(api_settings, "salary bands 2026", ["hr"])
    pub_headers = {"Authorization": f"Bearer {_token(client, email='pub@example.com')}"}
    hr_headers = {"Authorization": f"Bearer {_token(client, email='hr@example.com')}"}
    pub_docs = client.get("/documents", headers=pub_headers).json()
    hr_docs = client.get("/documents", headers=hr_headers).json()
    assert [d["title"] for d in pub_docs] == ["public k"]
    assert {d["title"] for d in hr_docs} == {"public k", "salary b"}


def test_ask_enforces_principals_from_token(client, api_settings):
    _register(api_settings, email="pub@example.com", principals=("public",))
    _register(api_settings, email="hr@example.com", principals=("public", "hr"))
    _seed_doc(api_settings, "salary bands 2026", ["hr"])
    pub_headers = {"Authorization": f"Bearer {_token(client, email='pub@example.com')}"}
    hr_headers = {"Authorization": f"Bearer {_token(client, email='hr@example.com')}"}
    body = {"question": "salary bands 2026"}
    pub_answer = client.post("/ask", json=body, headers=pub_headers).json()
    hr_answer = client.post("/ask", json=body, headers=hr_headers).json()
    assert pub_answer["citations"] == []          # NO_ANSWER path: nothing visible
    assert len(hr_answer["citations"]) == 1        # FakeLLM cites [1]


def test_ask_rejects_invalid_limit(client, api_settings):
    _register(api_settings)
    headers = {"Authorization": f"Bearer {_token(client)}"}
    assert client.post(
        "/ask", json={"question": "q", "limit": -1}, headers=headers
    ).status_code == 422
    assert client.post(
        "/ask", json={"question": "q", "limit": 51}, headers=headers
    ).status_code == 422


def test_ask_attributes_cost_to_user(client, api_settings):
    user = _register(api_settings)
    _seed_doc(api_settings, "public knowledge", ["public"])
    headers = {"Authorization": f"Bearer {_token(client)}"}
    client.post("/ask", json={"question": "public knowledge"}, headers=headers)
    conn = connect(api_settings.database_url)
    rows = conn.execute(
        "SELECT DISTINCT user_id FROM cost_events WHERE context = 'query'"
    ).fetchall()
    conn.close()
    assert rows == [(str(user.id),)]


def test_ask_forwards_source_ids(client, api_settings):
    # Without scoping, "tell me about beta" ranks doc B's chunk first (closer
    # embedding match), so the FakeLLM's "[1]" citation would resolve to B.
    # Passing source_ids=[doc_a] must exclude B from retrieval entirely, so
    # the citation resolves to A instead -- proving source_ids narrows the
    # candidate set rather than just being ignored.
    _register(api_settings)
    doc_a = _seed_doc(api_settings, "alpha document text", ["public"])
    _seed_doc(api_settings, "beta document text", ["public"])
    headers = {"Authorization": f"Bearer {_token(client)}"}
    response = client.post(
        "/ask",
        json={"question": "tell me about beta", "source_ids": [str(doc_a)]},
        headers=headers,
    )
    assert response.status_code == 200
    answer = response.json()
    assert len(answer["citations"]) == 1
    assert answer["citations"][0]["title"] == "alpha do"
    assert answer["citations"][0]["source_uri"] == "file:///alpha do.md"


def test_ask_without_source_ids_is_unchanged(client, api_settings):
    _register(api_settings)
    _seed_doc(api_settings, "public knowledge", ["public"])
    headers = {"Authorization": f"Bearer {_token(client)}"}
    response = client.post(
        "/ask", json={"question": "public knowledge"}, headers=headers
    )
    assert response.status_code == 200
    assert len(response.json()["citations"]) == 1


def test_ask_unknown_source_id_yields_no_local_hit(client, api_settings):
    _register(api_settings)
    _seed_doc(api_settings, "public knowledge", ["public"])
    headers = {"Authorization": f"Bearer {_token(client)}"}
    response = client.post(
        "/ask",
        json={"question": "public knowledge", "source_ids": [str(uuid4())]},
        headers=headers,
    )
    assert response.status_code == 200
    answer = response.json()
    assert answer["citations"] == []
    assert "couldn't find" in answer["text"]


def test_ask_empty_source_ids_returns_no_answer(client, api_settings):
    _register(api_settings)
    _seed_doc(api_settings, "public knowledge", ["public"])
    headers = {"Authorization": f"Bearer {_token(client)}"}
    response = client.post(
        "/ask",
        json={"question": "public knowledge", "source_ids": []},
        headers=headers,
    )
    assert response.status_code == 200
    answer = response.json()
    assert answer["citations"] == []
    assert "couldn't find" in answer["text"]
