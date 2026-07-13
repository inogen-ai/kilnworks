from kilnworks.settings import Settings


def test_auth_and_api_settings_defaults(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # ignore any developer .env
    settings = Settings()
    assert settings.secret_key == ""
    assert settings.token_ttl_minutes == 60
    assert settings.api_host == "127.0.0.1"
    assert settings.api_port == 8000


def test_settings_read_kilnworks_env_prefix(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KILNWORKS_SECRET_KEY", "s3cret")
    monkeypatch.setenv("KILNWORKS_TOKEN_TTL_MINUTES", "5")
    settings = Settings()
    assert settings.secret_key == "s3cret"
    assert settings.token_ttl_minutes == 5


def test_worker_and_upload_settings_defaults(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    settings = Settings()
    assert settings.data_dir == "./data"
    assert settings.max_upload_bytes == 26_214_400
    assert settings.worker_poll_seconds == 1.0
    assert settings.job_timeout_seconds == 300
    assert settings.db_pool_size == 10


def test_provider_settings_defaults(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    settings = Settings()
    assert settings.chat_provider == "openai"
    assert settings.embedding_provider == "openai"
    assert settings.anthropic_api_key == ""
    assert settings.anthropic_model == "claude-opus-4-8"
    assert settings.anthropic_max_tokens == 2048
    assert settings.ollama_base_url == "http://localhost:11434"
    assert settings.ollama_chat_model == "llama3.2"
    assert settings.ollama_embedding_model == "nomic-embed-text"
    assert settings.embedding_dimensions == 1536
    assert settings.ollama_num_ctx == 8192
    assert settings.ollama_timeout_seconds == 300.0


def test_oidc_settings_defaults(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    settings = Settings()
    assert settings.oidc_issuer == ""
    assert settings.oidc_client_id == ""
    assert settings.oidc_client_secret == ""
    assert settings.oidc_groups_claim == "groups"
    assert settings.oidc_scopes == "openid email profile"
    assert settings.oidc_enabled is False


def test_media_settings_defaults(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    settings = Settings()
    assert settings.vision_provider == "none"
    assert settings.vision_model == "gpt-4o-mini"
    assert settings.transcription_provider == "none"
    assert settings.transcription_model == "whisper-1"
    assert settings.local_whisper_model == "base"
    assert settings.max_media_bytes == 104_857_600


def test_media_settings_read_kilnworks_env_prefix(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KILNWORKS_VISION_PROVIDER", "openai")
    monkeypatch.setenv("KILNWORKS_MAX_MEDIA_BYTES", "1000")
    settings = Settings()
    assert settings.vision_provider == "openai"
    assert settings.max_media_bytes == 1000


def test_oidc_enabled_requires_both_issuer_and_client_id(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert Settings(oidc_issuer="https://idp.test").oidc_enabled is False
    assert Settings(oidc_client_id="abc").oidc_enabled is False
    assert (
        Settings(oidc_issuer="https://idp.test", oidc_client_id="abc").oidc_enabled is True
    )
