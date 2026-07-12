from pathlib import Path

from kilnworks.adapters.jobqueue import PgJobQueue
from kilnworks.db.connection import connect

from tests.api.test_ask_endpoints import _token
from tests.api.test_auth_endpoints import _register


def _headers(client, api_settings, principals=("public",), email="up@example.com"):
    _register(api_settings, email=email, principals=principals)
    return {"Authorization": f"Bearer {_token(client, email=email)}"}


def _upload(client, headers, filename="guide.md", content=b"# G\n\nbody", acl=None):
    data = {"acl_tags": list(acl)} if acl else {}
    return client.post(
        "/documents",
        headers=headers,
        files={"file": (filename, content, "text/markdown")},
        data=data,
    )


def test_upload_requires_auth(client):
    response = client.post("/documents", files={"file": ("a.md", b"x", "text/markdown")})
    assert response.status_code == 401


def test_upload_enqueues_job(client, api_settings, tmp_path, monkeypatch):
    monkeypatch.setattr(api_settings, "data_dir", str(tmp_path))
    headers = _headers(client, api_settings)
    response = _upload(client, headers)
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    conn = connect(api_settings.database_url)
    job = PgJobQueue(conn).get(body["job_id"])
    conn.close()
    assert job.kind == "ingest_upload"
    assert job.payload["title"] == "guide"
    assert job.payload["acl_tags"] == ["public"]
    assert job.payload["path"].startswith(str(tmp_path))
    assert Path(job.payload["path"]).is_absolute()


def test_upload_defaults_acl_to_own_principals(client, api_settings, tmp_path, monkeypatch):
    monkeypatch.setattr(api_settings, "data_dir", str(tmp_path))
    headers = _headers(client, api_settings, principals=("hr",), email="hr@example.com")
    response = _upload(client, headers)
    assert response.status_code == 202
    body = response.json()
    conn = connect(api_settings.database_url)
    job = PgJobQueue(conn).get(body["job_id"])
    conn.close()
    assert job.payload["acl_tags"] == ["hr"]


def test_upload_rejects_acl_escalation(client, api_settings, tmp_path, monkeypatch):
    monkeypatch.setattr(api_settings, "data_dir", str(tmp_path))
    headers = _headers(client, api_settings, principals=("public",), email="pub2@example.com")
    response = _upload(client, headers, acl=("hr",))
    assert response.status_code == 403


def test_upload_rejects_unsupported_type_and_oversize(client, api_settings, tmp_path,
                                                      monkeypatch):
    monkeypatch.setattr(api_settings, "data_dir", str(tmp_path))
    monkeypatch.setattr(api_settings, "max_upload_bytes", 10)
    headers = _headers(client, api_settings, email="up3@example.com")
    assert _upload(client, headers, filename="x.exe").status_code == 415
    assert _upload(client, headers, content=b"x" * 11).status_code == 413


def test_upload_accepts_uppercase_suffix(client, api_settings, tmp_path, monkeypatch):
    monkeypatch.setattr(api_settings, "data_dir", str(tmp_path))
    headers = _headers(client, api_settings, email="up5@example.com")
    response = _upload(client, headers, filename="Report.PDF", content=b"%PDF-1.4\n")
    assert response.status_code == 202


def test_job_status_endpoint(client, api_settings, tmp_path, monkeypatch):
    monkeypatch.setattr(api_settings, "data_dir", str(tmp_path))
    headers = _headers(client, api_settings, email="up4@example.com")
    job_id = _upload(client, headers).json()["job_id"]
    response = client.get(f"/jobs/{job_id}", headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "queued"
    assert client.get("/jobs/999999", headers=headers).status_code == 404


def test_job_status_scoped_to_creator(client, api_settings, tmp_path, monkeypatch):
    monkeypatch.setattr(api_settings, "data_dir", str(tmp_path))
    owner_headers = _headers(client, api_settings, email="owner@example.com")
    other_headers = _headers(client, api_settings, email="other@example.com")
    job_id = _upload(client, owner_headers).json()["job_id"]

    other_response = client.get(f"/jobs/{job_id}", headers=other_headers)
    assert other_response.status_code == 404

    owner_response = client.get(f"/jobs/{job_id}", headers=owner_headers)
    assert owner_response.status_code == 200
