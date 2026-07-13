from dataclasses import dataclass

import psycopg

from kilnworks.adapters.cost import LedgerCostRecorder
from kilnworks.adapters.embedders.fake import FakeEmbedder
from kilnworks.adapters.embedders.ollama import OllamaEmbedder
from kilnworks.adapters.embedders.openai import OpenAIEmbedder
from kilnworks.adapters.llm.anthropic import AnthropicChat
from kilnworks.adapters.llm.fake import FakeLLM
from kilnworks.adapters.llm.ollama import OllamaChat
from kilnworks.adapters.llm.openai import OpenAIChat
from kilnworks.adapters.media.fake import FakeTranscriber, FakeVisionExtractor
from kilnworks.adapters.media.transcribe_local import LocalWhisper
from kilnworks.adapters.media.transcribe_openai import OpenAIWhisper
from kilnworks.adapters.media.vision_anthropic import AnthropicVision
from kilnworks.adapters.media.vision_ollama import OllamaVision
from kilnworks.adapters.media.vision_openai import OpenAIVision
from kilnworks.adapters.pgvector_store import PgVectorStore
from kilnworks.core.chunking import HeadingAwareChunker
from kilnworks.core.ingestion import IngestionService
from kilnworks.core.ports import MediaExtractor, Transcriber, VisionExtractor
from kilnworks.core.query import QueryService
from kilnworks.costmeter import PgCostLedger
from kilnworks.db.connection import connect
from kilnworks.settings import Settings


@dataclass
class Services:
    ingestion: IngestionService
    query: QueryService
    media: MediaExtractor


MAX_EMBEDDING_DIMENSIONS = 2000
MIN_EMBEDDING_DIMENSIONS = 1


def embedding_dimensions_message(dimensions: int) -> str:
    """Actionable error for a `KILNWORKS_EMBEDDING_DIMENSIONS` value outside the range
    pgvector HNSW indexes support (covers both the floor and the ceiling). Shared by
    `validate_provider_settings` and the CLI's `init-db` pre-validation so both surfaces
    report the identical message."""
    if dimensions < MIN_EMBEDDING_DIMENSIONS:
        return (
            f"KILNWORKS_EMBEDDING_DIMENSIONS is {dimensions}, but "
            "it must be a positive integer"
        )
    return (
        f"KILNWORKS_EMBEDDING_DIMENSIONS is {dimensions}, but "
        f"pgvector HNSW indexes support at most {MAX_EMBEDDING_DIMENSIONS} dimensions; lower "
        "KILNWORKS_EMBEDDING_DIMENSIONS or use your embedding model's truncation "
        "option (e.g. OpenAI's `dimensions` parameter) to fit within the limit"
    )


def embedding_dimensions_out_of_range(dimensions: int) -> bool:
    return dimensions < MIN_EMBEDDING_DIMENSIONS or dimensions > MAX_EMBEDDING_DIMENSIONS


def validate_provider_settings(settings: Settings) -> None:
    if embedding_dimensions_out_of_range(settings.embedding_dimensions):
        raise ValueError(embedding_dimensions_message(settings.embedding_dimensions))
    if bool(settings.oidc_issuer) != bool(settings.oidc_client_id):
        raise ValueError(
            "KILNWORKS_OIDC_ISSUER and KILNWORKS_OIDC_CLIENT_ID must be set together"
        )
    if settings.fake_providers:
        return
    if settings.embedding_provider not in ("openai", "ollama"):
        raise ValueError(f"unknown embedding provider: {settings.embedding_provider!r}")
    if settings.chat_provider not in ("openai", "anthropic", "ollama"):
        raise ValueError(f"unknown chat provider: {settings.chat_provider!r}")
    if settings.vision_provider not in ("none", "openai", "anthropic", "ollama"):
        raise ValueError(f"unknown vision provider: {settings.vision_provider!r}")
    if settings.transcription_provider not in ("none", "openai", "local"):
        raise ValueError(
            f"unknown transcription provider: {settings.transcription_provider!r}"
        )
    uses_openai = (
        settings.chat_provider == "openai"
        or settings.embedding_provider == "openai"
        or settings.vision_provider == "openai"
        or settings.transcription_provider == "openai"
    )
    if uses_openai and not settings.openai_api_key:
        raise ValueError(
            "KILNWORKS_OPENAI_API_KEY is not set; set it or use KILNWORKS_FAKE_PROVIDERS=true"
        )
    uses_anthropic = (
        settings.chat_provider == "anthropic" or settings.vision_provider == "anthropic"
    )
    if uses_anthropic and not settings.anthropic_api_key:
        raise ValueError(
            "KILNWORKS_ANTHROPIC_API_KEY is not set; set it or use KILNWORKS_FAKE_PROVIDERS=true"
        )


def build_services(settings: Settings) -> Services:
    validate_provider_settings(settings)
    conn = connect(settings.database_url)
    return build_services_with_conn(settings, conn)


def prepare_database(conn, expected_dimensions: int | None = None) -> None:
    try:
        conn.execute("SELECT 1 FROM documents LIMIT 1")
    except psycopg.errors.UndefinedTable as exc:
        raise ValueError(
            "Database schema missing. Run: uv run kilnworks init-db"
        ) from exc
    PgCostLedger(conn).ensure_schema()
    if expected_dimensions is not None:
        row = conn.execute(
            """SELECT atttypmod FROM pg_attribute
               WHERE attrelid = 'chunks'::regclass AND attname = 'embedding'"""
        ).fetchone()
        actual_dimensions = row[0] if row else None
        if actual_dimensions != expected_dimensions:
            raise ValueError(
                f"chunks.embedding is dimension {actual_dimensions}, but "
                f"{expected_dimensions} was configured; re-run `kilnworks init-db` "
                "(and re-ingest) after changing embedding settings"
            )


def build_services_with_conn(settings: Settings, conn) -> Services:
    prepare_database(conn, expected_dimensions=settings.embedding_dimensions)
    return build_services_prepared(settings, conn)


def _build_embedder(settings: Settings):
    if settings.fake_providers:
        return FakeEmbedder(dimension=settings.embedding_dimensions)
    if settings.embedding_provider == "openai":
        return OpenAIEmbedder(
            api_key=settings.openai_api_key,
            model=settings.embedding_model,
            dimension=settings.embedding_dimensions,
        )
    if settings.embedding_provider == "ollama":
        return OllamaEmbedder(
            base_url=settings.ollama_base_url,
            model=settings.ollama_embedding_model,
            dimension=settings.embedding_dimensions,
            timeout=settings.ollama_timeout_seconds,
        )
    raise ValueError(f"unknown embedding provider: {settings.embedding_provider!r}")


def _build_llm(settings: Settings):
    if settings.fake_providers:
        return FakeLLM()
    if settings.chat_provider == "openai":
        return OpenAIChat(api_key=settings.openai_api_key, model=settings.chat_model)
    if settings.chat_provider == "anthropic":
        return AnthropicChat(
            api_key=settings.anthropic_api_key,
            model=settings.anthropic_model,
            max_tokens=settings.anthropic_max_tokens,
        )
    if settings.chat_provider == "ollama":
        return OllamaChat(
            base_url=settings.ollama_base_url,
            model=settings.ollama_chat_model,
            num_ctx=settings.ollama_num_ctx,
            timeout=settings.ollama_timeout_seconds,
        )
    raise ValueError(f"unknown chat provider: {settings.chat_provider!r}")


def _build_vision(settings: Settings) -> VisionExtractor | None:
    if settings.vision_provider == "none":
        return None
    # KILNWORKS_VISION_MODEL is the single shared model knob across all three
    # providers (there's no per-provider vision_model split the way chat has
    # chat_model/anthropic_model/ollama_chat_model); it defaults to an OpenAI
    # model, so operators picking anthropic/ollama for vision should also set
    # KILNWORKS_VISION_MODEL to a model that provider actually serves.
    if settings.vision_provider == "openai":
        return OpenAIVision(api_key=settings.openai_api_key, model=settings.vision_model)
    if settings.vision_provider == "anthropic":
        return AnthropicVision(api_key=settings.anthropic_api_key, model=settings.vision_model)
    if settings.vision_provider == "ollama":
        return OllamaVision(
            base_url=settings.ollama_base_url,
            model=settings.vision_model,
            timeout=settings.ollama_timeout_seconds,
        )
    raise ValueError(f"unknown vision provider: {settings.vision_provider!r}")


def _build_transcription(settings: Settings) -> Transcriber | None:
    if settings.transcription_provider == "none":
        return None
    if settings.transcription_provider == "openai":
        return OpenAIWhisper(
            api_key=settings.openai_api_key, model=settings.transcription_model
        )
    if settings.transcription_provider == "local":
        # Existence check only (see LocalWhisper._get_model for the real lazy import
        # + model construction) — surfaces a clear, actionable error at wiring time
        # rather than an ImportError traceback the first time a file gets ingested.
        try:
            import faster_whisper  # noqa: F401
        except ImportError as exc:
            raise ValueError(
                "KILNWORKS_TRANSCRIPTION_PROVIDER=local requires the faster-whisper "
                "package; install it with `pip install kilnworks[local-whisper]` (or "
                "`uv sync --extra local-whisper`), or set KILNWORKS_TRANSCRIPTION_PROVIDER "
                "to openai/none instead"
            ) from exc
        return LocalWhisper(model_size=settings.local_whisper_model)
    raise ValueError(
        f"unknown transcription provider: {settings.transcription_provider!r}"
    )


def build_media_extractor(settings: Settings) -> MediaExtractor:
    if settings.fake_providers:
        return MediaExtractor(
            vision=FakeVisionExtractor(),
            transcription=FakeTranscriber(),
            max_bytes=settings.max_media_bytes,
        )
    return MediaExtractor(
        vision=_build_vision(settings),
        transcription=_build_transcription(settings),
        max_bytes=settings.max_media_bytes,
    )


def build_judge(settings: Settings):
    """Build the LLM used to judge answer faithfulness for `kilnworks eval`."""
    if settings.fake_providers:
        return FakeLLM(reply="YES — the context fully supports the answer. [1]")
    return _build_llm(settings)


def build_services_prepared(settings: Settings, conn) -> Services:
    validate_provider_settings(settings)
    store = PgVectorStore(conn)
    ledger = PgCostLedger(conn)
    cost = LedgerCostRecorder(ledger)
    embedder = _build_embedder(settings)
    llm = _build_llm(settings)
    media = build_media_extractor(settings)
    return Services(
        ingestion=IngestionService(
            store=store, chunker=HeadingAwareChunker(), embedder=embedder, cost=cost
        ),
        query=QueryService(embedder, store, llm, cost=cost),
        media=media,
    )
