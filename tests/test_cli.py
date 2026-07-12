from pathlib import Path

import psycopg
import pytest
from typer.testing import CliRunner

from kilnworks.cli import app

runner = CliRunner()
REPO_ROOT = Path(__file__).parents[1]


@pytest.fixture(autouse=True)
def _clean_tables(pg_url):
    yield
    from kilnworks.db.connection import connect, init_db

    conn = connect(pg_url)
    init_db(conn)  # ensure tables exist even if a test dropped nothing
    conn.execute("TRUNCATE documents CASCADE")
    conn.execute("TRUNCATE cost_events")
    conn.execute("TRUNCATE users")
    conn.close()


def test_cli_end_to_end_with_fake_providers(pg_url, tmp_path, monkeypatch):
    monkeypatch.setenv("KILNWORKS_DATABASE_URL", pg_url)
    monkeypatch.setenv("KILNWORKS_FAKE_PROVIDERS", "true")
    (tmp_path / "note.md").write_text("# Onboarding\n\nNew hires get a kiln on day one.")

    assert runner.invoke(app, ["init-db"]).exit_code == 0

    result = runner.invoke(app, ["ingest", str(tmp_path)])
    assert result.exit_code == 0
    assert "Ingested 1 document(s); 0 failed." in result.output

    result = runner.invoke(app, ["ask", "What do new hires get?"])
    assert result.exit_code == 0
    assert "[1]" in result.output          # FakeLLM's canned reply cites block 1
    assert "Sources:" in result.output
    assert "note" in result.output          # citation title


def test_ingest_reports_failures_without_crashing(pg_url, tmp_path, monkeypatch):
    monkeypatch.setenv("KILNWORKS_DATABASE_URL", pg_url)
    monkeypatch.setenv("KILNWORKS_FAKE_PROVIDERS", "true")
    runner.invoke(app, ["init-db"])
    (tmp_path / "bad.md").write_bytes(b"\xff\xfe invalid utf8 \xff")
    result = runner.invoke(app, ["ingest", str(tmp_path)])
    assert result.exit_code == 1
    assert "1 failed" in result.output


def test_ingest_exits_zero_when_some_documents_succeed(pg_url, tmp_path, monkeypatch):
    monkeypatch.setenv("KILNWORKS_DATABASE_URL", pg_url)
    monkeypatch.setenv("KILNWORKS_FAKE_PROVIDERS", "true")
    runner.invoke(app, ["init-db"])
    (tmp_path / "good.md").write_text("# Title\n\nSome good content.")
    (tmp_path / "zzz-bad.md").write_bytes(b"\xff\xfe invalid utf8 \xff")
    result = runner.invoke(app, ["ingest", str(tmp_path)])
    assert result.exit_code == 0
    assert "1 failed" in result.output
    assert "Ingested 1 document(s); 1 failed." in result.output


def test_ingest_nonexistent_path_fails(pg_url, tmp_path, monkeypatch):
    monkeypatch.setenv("KILNWORKS_DATABASE_URL", pg_url)
    monkeypatch.setenv("KILNWORKS_FAKE_PROVIDERS", "true")
    result = runner.invoke(app, ["ingest", str(tmp_path / "does-not-exist")])
    assert result.exit_code != 0


def test_ask_fails_actionably_when_api_key_missing(pg_url, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # ensure no developer .env is read
    monkeypatch.setenv("KILNWORKS_DATABASE_URL", pg_url)
    monkeypatch.delenv("KILNWORKS_FAKE_PROVIDERS", raising=False)
    monkeypatch.setenv("KILNWORKS_OPENAI_API_KEY", "")

    result = runner.invoke(app, ["ask", "What do new hires get?"])

    assert result.exit_code == 1
    assert "KILNWORKS_OPENAI_API_KEY" in result.output


def test_ask_settings_validation_error_is_friendly(pg_url, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # ensure no developer .env is read
    monkeypatch.setenv("KILNWORKS_DATABASE_URL", pg_url)
    monkeypatch.setenv("KILNWORKS_FAKE_PROVIDERS", "notabool")

    result = runner.invoke(app, ["ask", "What do new hires get?"])

    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert result.output.strip()
    assert "fake_providers" in result.output.lower()


def test_missing_schema_prints_init_db_hint(pg_url, tmp_path, monkeypatch):
    from kilnworks.db.connection import connect

    admin_conn = connect(pg_url)
    try:
        admin_conn.execute("CREATE DATABASE schemaless")
    except psycopg.errors.DuplicateDatabase:
        pass
    admin_conn.close()

    schemaless_url = pg_url.rsplit("/", 1)[0] + "/schemaless"
    monkeypatch.chdir(tmp_path)  # ensure no developer .env is read
    monkeypatch.setenv("KILNWORKS_DATABASE_URL", schemaless_url)
    monkeypatch.setenv("KILNWORKS_FAKE_PROVIDERS", "true")

    result = runner.invoke(app, ["ask", "anything"])

    assert result.exit_code == 1
    assert "init-db" in result.output


def test_init_db_rejects_out_of_range_dimensions_before_connecting(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # ensure no developer .env is read
    # Unroutable host: if init-db attempted a connection, it would hang/fail differently
    # (a psycopg.OperationalError) rather than exiting with the dimensions message below.
    monkeypatch.setenv("KILNWORKS_DATABASE_URL", "postgresql://kw@10.255.255.1:5432/kw")
    monkeypatch.setenv("KILNWORKS_EMBEDDING_DIMENSIONS", "3072")

    result = runner.invoke(app, ["init-db"])

    assert result.exit_code == 1
    assert "KILNWORKS_EMBEDDING_DIMENSIONS is 3072" in result.output
    assert "at most 2000 dimensions" in result.output


def test_init_db_rejects_non_positive_dimensions_before_connecting(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # ensure no developer .env is read
    # Unroutable host: if init-db attempted a connection, it would hang/fail differently
    # (a psycopg.OperationalError) rather than exiting with the dimensions message below.
    monkeypatch.setenv("KILNWORKS_DATABASE_URL", "postgresql://kw@10.255.255.1:5432/kw")
    monkeypatch.setenv("KILNWORKS_EMBEDDING_DIMENSIONS", "0")

    result = runner.invoke(app, ["init-db"])

    assert result.exit_code == 1
    assert "KILNWORKS_EMBEDDING_DIMENSIONS is 0" in result.output
    assert "must be a positive integer" in result.output
    assert "at most 2000 dimensions" not in result.output


def test_db_unreachable_prints_friendly_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # ensure no developer .env is read
    monkeypatch.setenv("KILNWORKS_DATABASE_URL", "postgresql://kw@127.0.0.1:59999/kw")
    monkeypatch.setenv("KILNWORKS_FAKE_PROVIDERS", "true")
    result = runner.invoke(app, ["ask", "anything"])
    assert result.exit_code == 1
    assert "database" in result.output.lower()
    assert "docker compose up -d db" in result.output


def test_create_user_closes_connection(pg_url, tmp_path, monkeypatch):
    import kilnworks.cli as cli_module
    from kilnworks.db.connection import connect as real_connect

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KILNWORKS_DATABASE_URL", pg_url)
    runner.invoke(app, ["init-db"])

    captured = {}

    def _spy_connect(url):
        conn = real_connect(url)
        captured["conn"] = conn
        return conn

    monkeypatch.setattr(cli_module, "connect", _spy_connect)
    result = runner.invoke(
        app, ["create-user", "closes@example.com", "--password", "hunter2"]
    )
    assert result.exit_code == 0
    assert captured["conn"].closed


def test_create_user_and_duplicate(pg_url, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KILNWORKS_DATABASE_URL", pg_url)
    runner.invoke(app, ["init-db"])
    result = runner.invoke(
        app,
        ["create-user", "mike@example.com", "--password", "hunter2", "--principal", "hr"],
    )
    assert result.exit_code == 0
    assert "mike@example.com" in result.output and "hr" in result.output
    duplicate = runner.invoke(app, ["create-user", "mike@example.com", "--password", "x"])
    assert duplicate.exit_code == 1
    assert "already exists" in duplicate.output


def test_eval_command_meets_thresholds_on_smoke_dataset(pg_url, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KILNWORKS_DATABASE_URL", pg_url)
    monkeypatch.setenv("KILNWORKS_FAKE_PROVIDERS", "true")

    assert runner.invoke(app, ["init-db"]).exit_code == 0

    smoke_corpus = REPO_ROOT / "evals" / "smoke-corpus"
    ingest_result = runner.invoke(app, ["ingest", str(smoke_corpus)])
    assert ingest_result.exit_code == 0

    smoke_dataset = REPO_ROOT / "evals" / "smoke.jsonl"
    result = runner.invoke(
        app,
        [
            "eval",
            str(smoke_dataset),
            "--min-hit-rate",
            "1.0",
            "--min-citation-rate",
            "1.0",
            "--min-faithfulness",
            "1.0",
        ],
    )

    assert result.exit_code == 0
    assert "100%" in result.output


def test_eval_command_fails_when_threshold_not_met(pg_url, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KILNWORKS_DATABASE_URL", pg_url)
    monkeypatch.setenv("KILNWORKS_FAKE_PROVIDERS", "true")

    assert runner.invoke(app, ["init-db"]).exit_code == 0

    smoke_corpus = REPO_ROOT / "evals" / "smoke-corpus"
    ingest_result = runner.invoke(app, ["ingest", str(smoke_corpus)])
    assert ingest_result.exit_code == 0

    bad_dataset = tmp_path / "bad.jsonl"
    bad_dataset.write_text(
        '{"question": "What color is the sky on Mars?", '
        '"expected_sources": ["nonexistent-source"]}\n'
    )

    result = runner.invoke(app, ["eval", str(bad_dataset), "--min-hit-rate", "1.0"])

    assert result.exit_code == 1
    assert "hit_rate" in result.output
