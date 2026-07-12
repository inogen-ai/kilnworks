import psycopg_pool
import pytest

from tests.api.test_auth_endpoints import _register


class _DeadPool:
    def connection(self):
        raise psycopg_pool.PoolTimeout("pool exhausted: connections unavailable")


@pytest.fixture()
def auth_headers(client, api_settings):
    _register(api_settings)
    response = client.post(
        "/auth/token", json={"email": "mike@example.com", "password": "hunter2"}
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture()
def dead_pool(client):
    real_pool = client.app.state.pool
    client.app.state.pool = _DeadPool()
    try:
        yield
    finally:
        client.app.state.pool = real_pool


def test_documents_returns_503_when_db_down(client, auth_headers, dead_pool):
    response = client.get("/documents", headers=auth_headers)
    assert response.status_code == 503
    assert "database unavailable" in response.json()["detail"]


def test_ask_returns_503_when_db_down(client, auth_headers, dead_pool):
    response = client.post(
        "/ask", json={"question": "q"}, headers=auth_headers
    )
    assert response.status_code == 503
    assert "database unavailable" in response.json()["detail"]
