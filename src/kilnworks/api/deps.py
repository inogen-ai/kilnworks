from collections.abc import Iterator

import psycopg
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from psycopg_pool import PoolTimeout

from kilnworks.auth.tokens import TokenClaims, verify_token
from kilnworks.settings import Settings
from kilnworks.wiring import Services, build_services_prepared

_bearer = HTTPBearer(auto_error=False)


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_conn(request: Request) -> Iterator:
    try:
        with request.app.state.pool.connection() as conn:
            yield conn
    except (psycopg.OperationalError, PoolTimeout) as exc:
        raise HTTPException(
            status_code=503,
            detail="database unavailable; check the db service and try again",
        ) from exc


def get_services(
    settings: Settings = Depends(get_settings), conn=Depends(get_conn)
) -> Services:
    return build_services_prepared(settings, conn)


def current_claims(
    settings: Settings = Depends(get_settings),
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> TokenClaims:
    if credentials is None:
        raise HTTPException(status_code=401, detail="missing bearer token")
    claims = verify_token(credentials.credentials, settings.secret_key)
    if claims is None:
        raise HTTPException(status_code=401, detail="invalid or expired token")
    return claims
