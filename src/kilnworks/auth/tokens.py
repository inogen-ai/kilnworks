from datetime import datetime, timedelta, timezone
from uuid import UUID

import jwt
from pydantic import BaseModel

from kilnworks.auth.users import User

_ALGORITHM = "HS256"
_NO_SECRET = "KILNWORKS_SECRET_KEY is not set; set it to enable authentication"


class TokenClaims(BaseModel):
    user_id: UUID
    email: str
    principals: list[str]


def issue_token(user: User, secret_key: str, ttl_minutes: int = 60) -> str:
    if not secret_key:
        raise ValueError(_NO_SECRET)
    payload = {
        "sub": str(user.id),
        "email": user.email,
        "principals": user.principals,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes),
    }
    return jwt.encode(payload, secret_key, algorithm=_ALGORITHM)


def verify_token(token: str, secret_key: str) -> TokenClaims | None:
    if not secret_key:
        raise ValueError(_NO_SECRET)
    try:
        payload = jwt.decode(token, secret_key, algorithms=[_ALGORITHM])
    except jwt.InvalidTokenError:
        return None
    try:
        sub = payload["sub"]
        principals = payload["principals"]
        if not isinstance(sub, str) or not isinstance(principals, list):
            return None
        return TokenClaims(user_id=UUID(sub), email=payload["email"], principals=principals)
    except (KeyError, ValueError, TypeError):
        return None
