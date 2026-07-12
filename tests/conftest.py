import pytest
from testcontainers.postgres import PostgresContainer

from tests.auth._fake_idp import FakeIdp


@pytest.fixture(scope="session")
def pg_url():
    with PostgresContainer("pgvector/pgvector:pg16") as pg:
        yield pg.get_connection_url(driver=None)


@pytest.fixture()
def fake_idp():
    """A fake OIDC IdP (discovery/jwks/token endpoints) for tests/auth/test_oidc.py
    and tests/api/test_oidc_endpoints.py."""
    return FakeIdp()


@pytest.fixture()
def conn(pg_url):
    from kilnworks.db.connection import connect, init_db

    conn = connect(pg_url)
    init_db(conn)
    yield conn
    conn.execute("TRUNCATE documents CASCADE")
    conn.execute("TRUNCATE cost_events")
    conn.execute("TRUNCATE users")
    conn.execute("TRUNCATE jobs")
    conn.close()
