import pytest

from kilnworks.adapters.media.fake import FakeTranscriber, FakeVisionExtractor
from kilnworks.adapters.media.transcribe_local import LocalWhisper
from kilnworks.adapters.media.transcribe_openai import OpenAIWhisper
from kilnworks.adapters.media.vision_anthropic import AnthropicVision
from kilnworks.adapters.media.vision_ollama import OllamaVision
from kilnworks.adapters.media.vision_openai import OpenAIVision
from kilnworks.db.connection import connect, init_db
from kilnworks.evals.runner import parse_verdict
from kilnworks.settings import Settings
from kilnworks.wiring import (
    build_judge,
    build_media_extractor,
    build_services,
    build_services_with_conn,
)


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


def test_build_media_extractor_fake_branch_returns_fake_vision_and_transcription(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    media = build_media_extractor(Settings(fake_providers=True))
    assert isinstance(media.vision, FakeVisionExtractor)
    assert isinstance(media.transcription, FakeTranscriber)


def test_build_media_extractor_default_providers_are_none(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    media = build_media_extractor(Settings(fake_providers=False, openai_api_key="sk-test"))
    assert media.vision is None
    assert media.transcription is None


def test_build_media_extractor_threads_max_media_bytes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    media = build_media_extractor(Settings(fake_providers=True, max_media_bytes=42))
    assert media.max_bytes == 42


def test_build_media_extractor_builds_openai_vision(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    media = build_media_extractor(
        Settings(fake_providers=False, openai_api_key="sk-test", vision_provider="openai")
    )
    assert isinstance(media.vision, OpenAIVision)


def test_build_media_extractor_builds_anthropic_vision(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    media = build_media_extractor(
        Settings(
            fake_providers=False,
            openai_api_key="sk-test",
            anthropic_api_key="sk-ant-test",
            vision_provider="anthropic",
        )
    )
    assert isinstance(media.vision, AnthropicVision)


def test_build_media_extractor_builds_ollama_vision(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    media = build_media_extractor(
        Settings(fake_providers=False, openai_api_key="sk-test", vision_provider="ollama")
    )
    assert isinstance(media.vision, OllamaVision)


def test_build_media_extractor_rejects_unsupported_vision_provider(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="unknown vision provider"):
        build_media_extractor(
            Settings(fake_providers=False, openai_api_key="sk-test", vision_provider="cohere")
        )


def test_validate_provider_settings_requires_openai_key_for_openai_vision(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    from kilnworks.wiring import validate_provider_settings

    with pytest.raises(ValueError, match="KILNWORKS_OPENAI_API_KEY"):
        validate_provider_settings(
            Settings(
                openai_api_key="",
                vision_provider="openai",
                chat_provider="ollama",
                embedding_provider="ollama",
            )
        )


def test_validate_provider_settings_requires_anthropic_key_for_anthropic_vision(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    from kilnworks.wiring import validate_provider_settings

    with pytest.raises(ValueError, match="KILNWORKS_ANTHROPIC_API_KEY"):
        validate_provider_settings(
            Settings(
                anthropic_api_key="",
                vision_provider="anthropic",
                chat_provider="ollama",
                embedding_provider="ollama",
            )
        )


def test_validate_provider_settings_ollama_vision_needs_no_credentials(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from kilnworks.wiring import validate_provider_settings

    validate_provider_settings(
        Settings(chat_provider="ollama", embedding_provider="ollama", vision_provider="ollama")
    )


def test_validate_provider_settings_rejects_unknown_vision_provider(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from kilnworks.wiring import validate_provider_settings

    with pytest.raises(ValueError, match="unknown vision provider"):
        validate_provider_settings(Settings(vision_provider="cohere"))


def test_build_media_extractor_builds_openai_whisper(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    media = build_media_extractor(
        Settings(
            fake_providers=False,
            openai_api_key="sk-test",
            transcription_provider="openai",
        )
    )
    assert isinstance(media.transcription, OpenAIWhisper)


def test_build_media_extractor_builds_local_whisper_when_installed(tmp_path, monkeypatch):
    import sys
    from types import ModuleType

    monkeypatch.chdir(tmp_path)
    fake_module = ModuleType("faster_whisper")
    fake_module.WhisperModel = object  # presence check only; never constructed here
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_module)

    media = build_media_extractor(
        Settings(fake_providers=False, openai_api_key="sk-test", transcription_provider="local")
    )
    assert isinstance(media.transcription, LocalWhisper)


def test_build_media_extractor_local_whisper_missing_package_raises_clear_error(
    tmp_path, monkeypatch
):
    import sys

    monkeypatch.chdir(tmp_path)
    # A `None` entry in sys.modules makes `import faster_whisper` raise ImportError,
    # the standard way to simulate "package not installed" without real absence
    # (faster-whisper genuinely isn't installed in this environment anyway, but this
    # keeps the test robust regardless of the base install's optional extras).
    monkeypatch.setitem(sys.modules, "faster_whisper", None)

    with pytest.raises(ValueError, match="local-whisper"):
        build_media_extractor(
            Settings(
                fake_providers=False, openai_api_key="sk-test", transcription_provider="local"
            )
        )


def test_build_media_extractor_rejects_unknown_transcription_provider(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="unknown transcription provider"):
        build_media_extractor(
            Settings(
                fake_providers=False,
                openai_api_key="sk-test",
                transcription_provider="cohere",
            )
        )


def test_validate_provider_settings_requires_openai_key_for_openai_transcription(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    from kilnworks.wiring import validate_provider_settings

    with pytest.raises(ValueError, match="KILNWORKS_OPENAI_API_KEY"):
        validate_provider_settings(
            Settings(
                openai_api_key="",
                transcription_provider="openai",
                chat_provider="ollama",
                embedding_provider="ollama",
            )
        )


def test_build_services_prepared_wires_media_extractor(pg_url):
    conn = connect(pg_url)
    init_db(conn)
    from kilnworks.wiring import build_services_prepared

    services = build_services_prepared(
        Settings(database_url=pg_url, fake_providers=True, openai_api_key=""), conn
    )
    assert isinstance(services.media.vision, FakeVisionExtractor)
    assert isinstance(services.media.transcription, FakeTranscriber)
    conn.close()


def test_no_connectors_config_yields_empty_registry_and_no_mcp_import(pg_url, monkeypatch):
    import sys

    # Other test modules (tests/adapters/test_mcp_stdio.py) legitimately import `mcp`
    # directly to exercise the real MCPStdioConnector, which leaves it cached in
    # sys.modules for the rest of the pytest session regardless of run order. Scrub any
    # already-cached mcp modules first (monkeypatch restores them after the test) so this
    # assertion faithfully checks "did *this* build import mcp", not "has anything, ever,
    # in this session imported mcp".
    for mod_name in list(sys.modules):
        if mod_name == "mcp" or mod_name.startswith("mcp."):
            monkeypatch.delitem(sys.modules, mod_name, raising=False)

    conn = connect(pg_url)
    init_db(conn)
    from kilnworks.wiring import build_services_prepared

    services = build_services_prepared(
        Settings(
            database_url=pg_url, fake_providers=True, openai_api_key="", connectors_config=""
        ),
        conn,
    )
    conn.close()
    assert services.connectors.allowed_for(["public"]) == []
    assert "mcp" not in sys.modules  # sacred constraint: base path never loads mcp


def test_malformed_connectors_config_does_not_crash(tmp_path, monkeypatch, caplog):
    monkeypatch.chdir(tmp_path)
    from kilnworks.wiring import build_connector_registry

    bad_config = tmp_path / "connectors.json"
    bad_config.write_text("{not valid json")

    with caplog.at_level("WARNING"):
        registry = build_connector_registry(
            Settings(fake_providers=True, connectors_config=str(bad_config))
        )
    assert registry.allowed_for(["public"]) == []
    assert any("connectors config" in record.message for record in caplog.records)


def test_missing_connectors_config_does_not_crash(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from kilnworks.wiring import build_connector_registry

    registry = build_connector_registry(
        Settings(fake_providers=True, connectors_config=str(tmp_path / "nope.json"))
    )
    assert registry.allowed_for(["public"]) == []


def test_connectors_config_builds_registry_via_mcp_factory(tmp_path, monkeypatch):
    pytest.importorskip("mcp")
    monkeypatch.chdir(tmp_path)
    import json

    from kilnworks.adapters.connectors.mcp_stdio import MCPStdioConnector
    from kilnworks.wiring import build_connector_registry

    config_path = tmp_path / "connectors.json"
    config_path.write_text(
        json.dumps(
            {
                "connectors": [
                    {
                        "name": "docs",
                        "command": ["docs-mcp"],
                        "allowed_groups": ["public"],
                        "env": {"TOKEN": "abc"},
                        "search_limit": 3,
                        "search_tool": "lookup",
                        "query_arg": "q",
                        "extra_args": {"mode": "fast"},
                    }
                ]
            }
        )
    )

    registry = build_connector_registry(
        Settings(
            fake_providers=True,
            connectors_config=str(config_path),
            connector_timeout=12.0,
        )
    )
    connectors = registry.allowed_for(["public"])
    assert len(connectors) == 1
    connector = connectors[0]
    assert isinstance(connector, MCPStdioConnector)
    assert connector.name == "docs"
    assert connector._command == ["docs-mcp"]
    assert connector._env == {"TOKEN": "abc"}
    assert connector._search_limit == 3
    assert connector._search_tool == "lookup"
    assert connector._query_arg == "q"
    assert connector._extra_args == {"mode": "fast"}
    assert connector._timeout == 12.0
