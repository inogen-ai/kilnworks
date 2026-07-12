import pytest

from kilnworks.db.connection import connect, init_db
from kilnworks.evals.runner import parse_verdict
from kilnworks.settings import Settings
from kilnworks.wiring import build_judge, build_services, build_services_with_conn


def test_build_services_raises_actionable_error_when_api_key_missing():
    settings = Settings(
        fake_providers=False,
        openai_api_key="",
        database_url="postgresql://nobody@localhost:1/none",
    )
    with pytest.raises(ValueError) as excinfo:
        build_services(settings)
    message = str(excinfo.value)
    assert "KILNWORKS_OPENAI_API_KEY" in message
    assert "KILNWORKS_FAKE_PROVIDERS" in message


def test_build_services_with_conn_also_guards_missing_api_key(pg_url):
    conn = connect(pg_url)
    init_db(conn)
    settings = Settings(database_url=pg_url, fake_providers=False, openai_api_key="")
    with pytest.raises(ValueError, match="KILNWORKS_OPENAI_API_KEY"):
        build_services_with_conn(settings, conn)
    conn.close()


def test_prepare_database_raises_actionable_error_on_missing_schema(pg_url):
    import psycopg

    from kilnworks.db.connection import connect
    from kilnworks.wiring import prepare_database

    admin = connect(pg_url)
    try:
        admin.execute("CREATE DATABASE prepcheck")
    except psycopg.errors.DuplicateDatabase:
        pass
    bare = connect(pg_url.rsplit("/", 1)[0] + "/prepcheck")
    try:
        with pytest.raises(ValueError, match="init-db"):
            prepare_database(bare)
    finally:
        bare.close()
        admin.close()


def test_build_services_prepared_skips_probe(pg_url):
    from kilnworks.db.connection import connect, init_db
    from kilnworks.wiring import build_services_prepared

    conn = connect(pg_url)
    init_db(conn)
    services = build_services_prepared(
        Settings(database_url=pg_url, fake_providers=True, openai_api_key=""), conn
    )
    assert services.query is not None and services.ingestion is not None
    conn.close()


def test_prepare_database_rejects_dimension_mismatch(pg_url):
    from kilnworks.db.connection import connect, init_db
    from kilnworks.wiring import prepare_database

    conn = connect(pg_url)
    init_db(conn)
    try:
        with pytest.raises(ValueError, match="dimension"):
            prepare_database(conn, expected_dimensions=768)
        prepare_database(conn, expected_dimensions=1536)  # matching passes
    finally:
        conn.close()


def test_validate_provider_settings_matrix(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from kilnworks.wiring import validate_provider_settings

    ok = Settings(fake_providers=True)
    validate_provider_settings(ok)  # fakes need nothing

    with pytest.raises(ValueError, match="KILNWORKS_OPENAI_API_KEY"):
        validate_provider_settings(Settings(openai_api_key=""))

    with pytest.raises(ValueError, match="KILNWORKS_ANTHROPIC_API_KEY"):
        validate_provider_settings(
            Settings(
                chat_provider="anthropic", anthropic_api_key="", embedding_provider="ollama"
            )
        )

    validate_provider_settings(
        Settings(chat_provider="ollama", embedding_provider="ollama")
    )  # ollama: no credentials required

    with pytest.raises(ValueError, match="unknown chat provider"):
        validate_provider_settings(Settings(chat_provider="cohere"))
    with pytest.raises(ValueError, match="unknown embedding provider"):
        validate_provider_settings(Settings(embedding_provider="cohere"))


def test_build_judge_fake_reply_satisfies_runner_verdict_predicate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    judge = build_judge(Settings(fake_providers=True))
    completion = judge.complete("system", "user")
    assert parse_verdict(completion.text) is True


def test_validate_provider_settings_rejects_dims_over_hnsw_ceiling(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from kilnworks.wiring import validate_provider_settings

    with pytest.raises(ValueError, match="2000") as excinfo:
        validate_provider_settings(Settings(fake_providers=True, embedding_dimensions=3072))
    message = str(excinfo.value)
    assert "HNSW" in message
    assert "truncat" in message.lower()

    validate_provider_settings(
        Settings(fake_providers=True, embedding_dimensions=2000)
    )  # exactly at the ceiling passes


def test_validate_provider_settings_requires_paired_oidc_issuer_and_client_id(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    from kilnworks.wiring import validate_provider_settings

    validate_provider_settings(Settings(fake_providers=True))  # neither set: fine
    validate_provider_settings(
        Settings(fake_providers=True, oidc_issuer="https://idp.test", oidc_client_id="abc")
    )  # both set: fine

    with pytest.raises(ValueError, match="KILNWORKS_OIDC_ISSUER"):
        validate_provider_settings(
            Settings(fake_providers=True, oidc_issuer="https://idp.test")
        )
    with pytest.raises(ValueError, match="KILNWORKS_OIDC_CLIENT_ID"):
        validate_provider_settings(Settings(fake_providers=True, oidc_client_id="abc"))
