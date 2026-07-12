import pytest

from kilnworks.api.app import create_app
from kilnworks.auth.users import PgUserStore
from kilnworks.db.connection import connect
from kilnworks.settings import Settings


def _register(api_settings, email="mike@example.com", password="hunter2", principals=("public",)):
    conn = connect(api_settings.database_url)
    user = PgUserStore(conn).create_user(email, password, principals=principals)
    conn.close()
    return user


def test_health_needs_no_auth(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_token_roundtrip(client, api_settings):
    _register(api_settings)
    response = client.post(
        "/auth/token", json={"email": "mike@example.com", "password": "hunter2"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]


def test_bad_credentials_rejected(client, api_settings):
    _register(api_settings)
    response = client.post(
        "/auth/token", json={"email": "mike@example.com", "password": "wrong"}
    )
    assert response.status_code == 401
    assert response.json() == {"detail": "invalid credentials"}


def test_app_refuses_empty_secret(pg_url):
    with pytest.raises(ValueError, match="KILNWORKS_SECRET_KEY"):
        create_app(Settings(database_url=pg_url, fake_providers=True, secret_key=""))


def test_app_refuses_short_secret(pg_url):
    with pytest.raises(ValueError, match="KILNWORKS_SECRET_KEY"):
        create_app(Settings(database_url=pg_url, fake_providers=True, secret_key="short"))


def test_startup_fails_actionably_on_missing_schema(pg_url):
    import psycopg
    from fastapi.testclient import TestClient

    admin = connect(pg_url)
    try:
        admin.execute("CREATE DATABASE lifespanless")
    except psycopg.errors.DuplicateDatabase:
        pass
    finally:
        admin.close()
    settings = Settings(
        database_url=pg_url.rsplit("/", 1)[0] + "/lifespanless",
        fake_providers=True,
        openai_api_key="",
        secret_key="test-secret-0123456789abcdef-0123456789abcdef",
    )
    with pytest.raises(ValueError, match="init-db"):
        with TestClient(create_app(settings)):
            pass
