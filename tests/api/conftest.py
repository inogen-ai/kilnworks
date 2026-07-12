import pytest
from fastapi.testclient import TestClient

from kilnworks.api.app import create_app
from kilnworks.db.connection import connect, init_db
from kilnworks.settings import Settings


@pytest.fixture()
def api_settings(pg_url, tmp_path):
    return Settings(
        database_url=pg_url,
        fake_providers=True,
        secret_key="test-secret-0123456789abcdef-0123456789abcdef",
        openai_api_key="",
        web_dist_dir=str(tmp_path / "no-dist"),
    )


@pytest.fixture()
def client(api_settings):
    conn = connect(api_settings.database_url)
    init_db(conn)
    with TestClient(create_app(api_settings)) as client:
        yield client
    conn.execute("TRUNCATE documents CASCADE")
    conn.execute("TRUNCATE cost_events")
    conn.execute("TRUNCATE users")
    conn.execute("TRUNCATE jobs")
    conn.close()
