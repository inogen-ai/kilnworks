import pytest
from fastapi.testclient import TestClient

from kilnworks.api.app import create_app
from kilnworks.db.connection import connect, init_db
from kilnworks.settings import Settings


def _settings(pg_url, web_dist_dir: str) -> Settings:
    return Settings(
        database_url=pg_url,
        fake_providers=True,
        secret_key="test-secret-0123456789abcdef-0123456789abcdef",
        openai_api_key="",
        web_dist_dir=web_dist_dir,
    )


@pytest.fixture()
def db(pg_url):
    conn = connect(pg_url)
    init_db(conn)
    yield conn
    conn.execute("TRUNCATE documents CASCADE")
    conn.execute("TRUNCATE cost_events")
    conn.execute("TRUNCATE users")
    conn.execute("TRUNCATE jobs")
    conn.close()


def test_serves_ui_when_dist_dir_present(pg_url, db, tmp_path):
    (tmp_path / "index.html").write_text("<h1>ui</h1>")
    settings = _settings(pg_url, str(tmp_path))
    with TestClient(create_app(settings)) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert "<h1>ui</h1>" in response.text

        health = client.get("/health")
        assert health.status_code == 200
        assert health.json() == {"status": "ok"}


def test_api_only_when_dist_dir_missing(pg_url, db, tmp_path):
    missing = tmp_path / "does-not-exist"
    settings = _settings(pg_url, str(missing))
    with TestClient(create_app(settings)) as client:
        response = client.get("/")
        assert response.status_code == 404

        health = client.get("/health")
        assert health.status_code == 200
        assert health.json() == {"status": "ok"}
