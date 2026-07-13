from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KILNWORKS_", env_file=".env")

    database_url: str = "postgresql://kilnworks:kilnworks@localhost:5432/kilnworks"
    openai_api_key: str = ""
    chat_model: str = "gpt-4o-mini"
    embedding_model: str = "text-embedding-3-small"
    fake_providers: bool = False
    chat_provider: str = "openai"
    embedding_provider: str = "openai"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-4-8"
    anthropic_max_tokens: int = 2048
    ollama_base_url: str = "http://localhost:11434"
    ollama_chat_model: str = "llama3.2"
    ollama_embedding_model: str = "nomic-embed-text"
    ollama_num_ctx: int = 8192
    ollama_timeout_seconds: float = 300.0
    embedding_dimensions: int = 1536
    secret_key: str = ""
    token_ttl_minutes: int = 60
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    data_dir: str = "./data"
    max_upload_bytes: int = 26_214_400
    worker_poll_seconds: float = 1.0
    job_timeout_seconds: int = 300
    # must exceed job_timeout_seconds; 300s timeout + margin. Enforced at worker startup
    # (run_worker raises ValueError otherwise) since a lease <= timeout lets a job's own
    # in-flight timeout race the reclaim pass, reclaiming a job that hasn't actually stalled.
    job_lease_seconds: int = 420
    db_pool_size: int = 10
    web_dist_dir: str = ""
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_groups_claim: str = "groups"
    oidc_scopes: str = "openid email profile"
    vision_provider: str = "none"
    vision_model: str = "gpt-4o-mini"
    transcription_provider: str = "none"
    transcription_model: str = "whisper-1"
    local_whisper_model: str = "base"
    max_media_bytes: int = 104_857_600

    @property
    def oidc_enabled(self) -> bool:
        return bool(self.oidc_issuer and self.oidc_client_id)
