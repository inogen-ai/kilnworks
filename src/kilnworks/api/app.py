import json
import logging
from collections.abc import Iterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from urllib.parse import quote, unquote, urlparse
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pgvector.psycopg import register_vector
from psycopg_pool import ConnectionPool

from kilnworks.adapters.jobqueue import PgJobQueue
from kilnworks.adapters.pgvector_store import PgVectorStore
from kilnworks.adapters.sources.parsers import SUPPORTED_SUFFIXES
from kilnworks.api.deps import current_claims, get_conn, get_services, get_settings
from kilnworks.api.schemas import (
    AskRequest,
    ConnectorInfo,
    DocumentInfo,
    JobInfo,
    TokenRequest,
    TokenResponse,
)
from kilnworks.auth.oidc import OidcClient, OidcError, decode_state, encode_state
from kilnworks.auth.tokens import TokenClaims, issue_token
from kilnworks.auth.users import PgUserStore
from kilnworks.core.errors import ProviderError
from kilnworks.core.models import Answer
from kilnworks.core.query import QueryService
from kilnworks.db.connection import connect
from kilnworks.settings import Settings
from kilnworks.wiring import prepare_database, validate_provider_settings

logger = logging.getLogger(__name__)


def _ask_stream_events(
    query_service: QueryService,
    question: str,
    principals: Sequence[str],
    limit: int,
    user_id: str,
    source_ids: Sequence[UUID] | None = None,
    connectors: Sequence[str] | None = None,
) -> Iterator[str]:
    events = query_service.ask_stream(
        question,
        principals=principals,
        limit=limit,
        user_id=user_id,
        source_ids=source_ids,
        connectors=connectors,
    )
    try:
        for event in events:
            if isinstance(event, Answer):
                yield f"event: answer\ndata: {event.model_dump_json()}\n\n"
            else:
                payload = json.dumps({"text": event})
                yield f"event: delta\ndata: {payload}\n\n"
        yield "event: done\ndata: {}\n\n"
    except ProviderError as exc:
        payload = json.dumps({"detail": str(exc)})
        yield f"event: error\ndata: {payload}\n\n"
    except Exception:
        logger.exception("ask_stream failed mid-stream")
        yield 'event: error\ndata: {"detail": "internal error"}\n\n'
    finally:
        for _ in events:  # drain on disconnect so cost recording still runs
            pass


def create_app(settings: Settings) -> FastAPI:
    if not settings.secret_key:
        raise ValueError(
            "KILNWORKS_SECRET_KEY is not set; the API requires it for authentication"
        )
    if len(settings.secret_key) < 32:
        raise ValueError(
            "KILNWORKS_SECRET_KEY must be at least 32 characters; generate one with: "
            "openssl rand -hex 32"
        )
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        validate_provider_settings(settings)  # fail fast on provider misconfiguration
        conn = connect(settings.database_url)  # ensures extension + registers types
        try:
            prepare_database(conn, expected_dimensions=settings.embedding_dimensions)
        finally:
            conn.close()
        app.state.pool = ConnectionPool(
            settings.database_url,
            min_size=1,
            max_size=settings.db_pool_size,
            kwargs={"autocommit": True},
            configure=register_vector,
            open=True,
        )
        try:
            yield
        finally:
            app.state.pool.close()

    app = FastAPI(title="Kilnworks", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    if settings.oidc_enabled:
        app.state.oidc = OidcClient(
            settings.oidc_issuer,
            settings.oidc_client_id,
            settings.oidc_client_secret,
            settings.oidc_scopes,
            settings.oidc_groups_claim,
        )

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/auth/config")
    def auth_config() -> dict:
        return {"sso_enabled": settings.oidc_enabled}

    if settings.oidc_enabled:

        @app.get("/auth/oidc/login")
        def oidc_login(request: Request) -> RedirectResponse:
            try:
                url, bundle = request.app.state.oidc.authorization_request(
                    str(request.url_for("oidc_callback"))
                )
            except OidcError:
                response = RedirectResponse(
                    "/#sso_error=" + quote("identity provider unreachable; try again later"),
                    status_code=302,
                )
                response.delete_cookie("kilnworks_oidc")
                return response
            response = RedirectResponse(url, status_code=302)
            response.set_cookie(
                "kilnworks_oidc",
                encode_state(bundle, settings.secret_key),
                max_age=600,
                httponly=True,
                samesite="lax",
                secure=request.url.scheme == "https",
            )
            return response

        @app.get("/auth/oidc/callback", name="oidc_callback")
        def oidc_callback(
            request: Request, code: str = "", state: str = "", conn=Depends(get_conn)
        ) -> RedirectResponse:
            def fail(message: str) -> RedirectResponse:
                response = RedirectResponse(
                    "/#sso_error=" + quote(message), status_code=302
                )
                response.delete_cookie("kilnworks_oidc")
                return response

            bundle = decode_state(
                request.cookies.get("kilnworks_oidc", ""), settings.secret_key
            )
            if bundle is None or not state or state != bundle.state:
                return fail("sign-in session expired or invalid; try again")
            if not code:
                return fail("identity provider returned no code")
            try:
                identity = request.app.state.oidc.complete(
                    code,
                    bundle.code_verifier,
                    bundle.nonce,
                    str(request.url_for("oidc_callback")),
                )
            except OidcError as exc:
                return fail(str(exc))
            principals = list(dict.fromkeys(["public", *identity.groups]))
            user = PgUserStore(conn).upsert_sso_user(
                identity.email, identity.display_name, principals
            )
            token = issue_token(user, settings.secret_key, settings.token_ttl_minutes)
            response = RedirectResponse("/#token=" + quote(token), status_code=302)
            response.delete_cookie("kilnworks_oidc")
            return response

    @app.post("/auth/token", response_model=TokenResponse)
    def token(
        body: TokenRequest,
        settings: Settings = Depends(get_settings),
        conn=Depends(get_conn),
    ) -> TokenResponse:
        user = PgUserStore(conn).authenticate(body.email, body.password)
        if user is None:
            raise HTTPException(status_code=401, detail="invalid credentials")
        return TokenResponse(
            access_token=issue_token(user, settings.secret_key, settings.token_ttl_minutes)
        )

    @app.get("/documents", response_model=list[DocumentInfo])
    def documents(
        claims: TokenClaims = Depends(current_claims),
        conn=Depends(get_conn),
    ) -> list[DocumentInfo]:
        rows = conn.execute(
            "SELECT id, source_uri, title, status, error FROM documents"
            " WHERE acl_tags && %s::text[] ORDER BY created_at",
            (list(claims.principals),),
        ).fetchall()
        return [
            DocumentInfo(id=str(row[0]), source_uri=row[1], title=row[2],
                         status=row[3], error=row[4])
            for row in rows
        ]

    @app.get("/connectors", response_model=list[ConnectorInfo])
    def connectors(
        claims: TokenClaims = Depends(current_claims),
        services=Depends(get_services),
    ) -> list[ConnectorInfo]:
        return [
            ConnectorInfo(name=n, status=s, needs_login=nl)
            for (n, s, nl) in services.connectors.visible(claims.principals)
        ]

    @app.post("/documents", status_code=202)
    def upload_document(
        file: UploadFile,
        acl_tags: Annotated[list[str] | None, Form()] = None,
        claims: TokenClaims = Depends(current_claims),
        settings: Settings = Depends(get_settings),
        conn=Depends(get_conn),
    ) -> dict:
        tags = acl_tags or list(claims.principals)
        if not set(tags) <= set(claims.principals):
            raise HTTPException(
                status_code=403, detail="acl_tags may not exceed your own principals"
            )
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            raise HTTPException(status_code=415, detail=f"unsupported file type: {suffix}")
        content = file.file.read(settings.max_upload_bytes + 1)
        if len(content) > settings.max_upload_bytes:
            raise HTTPException(status_code=413, detail="file exceeds max upload size")
        uploads_dir = Path(settings.data_dir).resolve() / "uploads"
        uploads_dir.mkdir(parents=True, exist_ok=True)
        dest = uploads_dir / f"{uuid4().hex}{suffix}"
        dest.write_bytes(content)
        payload = {
            "path": str(dest),
            "acl_tags": tags,
            "title": Path(file.filename).stem,
        }
        job_id = PgJobQueue(conn).enqueue(
            "ingest_upload", payload, created_by=str(claims.user_id)
        )
        return {"job_id": job_id, "status": "queued"}

    @app.delete("/documents/{document_id}", status_code=204)
    def delete_document(
        document_id: UUID,
        claims: TokenClaims = Depends(current_claims),
        settings: Settings = Depends(get_settings),
        conn=Depends(get_conn),
    ) -> None:
        row = conn.execute(
            "SELECT source_uri FROM documents WHERE id = %s AND acl_tags && %s::text[]",
            (document_id, list(claims.principals)),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="document not found")
        source_uri = row[0]

        store = PgVectorStore(conn)
        with store.transaction():
            store.delete_document_chunks(document_id)
            store.delete_document(document_id, claims.principals)

        # source_uri for uploads is a file:// URI (Path.as_uri()); resolve it back to a
        # filesystem path before checking containment, so we never unlink outside uploads/.
        uploads_dir = Path(settings.data_dir).resolve() / "uploads"
        parsed = urlparse(source_uri)
        path_str = unquote(parsed.path) if parsed.scheme == "file" else source_uri
        try:
            file_path = Path(path_str).resolve()
            if file_path.is_relative_to(uploads_dir):
                file_path.unlink(missing_ok=True)
        except (OSError, ValueError):
            pass

    @app.get("/jobs/{job_id}", response_model=JobInfo)
    def job_status(
        job_id: int,
        claims: TokenClaims = Depends(current_claims),
        conn=Depends(get_conn),
    ) -> JobInfo:
        job = PgJobQueue(conn).get(job_id)
        if job is None or job.created_by != str(claims.user_id):
            raise HTTPException(status_code=404, detail="job not found")
        return JobInfo(
            id=job.id,
            kind=job.kind,
            status=job.status,
            attempts=job.attempts,
            error=job.error,
        )

    @app.post("/ask", response_model=Answer)
    def ask(
        body: AskRequest,
        claims: TokenClaims = Depends(current_claims),
        services=Depends(get_services),
    ) -> Answer:
        try:
            return services.query.ask(
                body.question,
                principals=claims.principals,
                limit=body.limit,
                user_id=str(claims.user_id),
                source_ids=body.source_ids,
                connectors=body.connectors,
            )
        except ProviderError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/ask/stream")
    def ask_stream(
        body: AskRequest,
        claims: TokenClaims = Depends(current_claims),
        services=Depends(get_services),
    ) -> StreamingResponse:
        generator = _ask_stream_events(
            services.query,
            body.question,
            claims.principals,
            body.limit,
            str(claims.user_id),
            body.source_ids,
            body.connectors,
        )
        return StreamingResponse(generator, media_type="text/event-stream")

    dist = (
        Path(settings.web_dist_dir)
        if settings.web_dist_dir
        else Path(__file__).resolve().parents[3] / "web" / "dist"
    )
    if dist.is_dir():
        # html=True serves index.html at "/" only; the UI is single-route so no
        # catch-all SPA fallback is needed — add one if client-side routes ever land.
        app.mount("/", StaticFiles(directory=dist, html=True), name="web")

    return app
