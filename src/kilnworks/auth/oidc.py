"""OIDC authorization-code + PKCE client: discovery, code exchange, and
RS256/JWKS ID-token validation, plus a signed state-cookie codec.

Mirrors the defensive idioms of ``kilnworks.auth.tokens``: a pinned algorithm,
claim-shape guards, and terse error messages that never echo tokens, codes,
or secrets back to the caller.
"""

import base64
import hashlib
import secrets
import threading
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlsplit

import httpx
import jwt
from pydantic import BaseModel

_STATE_ALGORITHM = "HS256"
_STATE_AUDIENCE = "kilnworks-oidc-state"
_STATE_TTL_MINUTES = 10
_ID_TOKEN_ALGORITHMS = ["RS256"]


class OidcError(Exception):
    """Raised for any OIDC flow failure. Messages are short and never
    contain tokens, codes, or secrets."""


class OidcIdentity(BaseModel):
    email: str
    display_name: str = ""
    groups: list[str] = []


class StateBundle(BaseModel):
    state: str
    nonce: str
    code_verifier: str


def _pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def encode_state(bundle: StateBundle, secret_key: str) -> str:
    payload = {
        "state": bundle.state,
        "nonce": bundle.nonce,
        "cv": bundle.code_verifier,
        "aud": _STATE_AUDIENCE,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=_STATE_TTL_MINUTES),
    }
    return jwt.encode(payload, secret_key, algorithm=_STATE_ALGORITHM)


def decode_state(value: str, secret_key: str) -> StateBundle | None:
    try:
        payload = jwt.decode(
            value,
            secret_key,
            algorithms=[_STATE_ALGORITHM],
            audience=_STATE_AUDIENCE,
            options={"require": ["exp"]},
        )
    except jwt.InvalidTokenError:
        return None
    try:
        return StateBundle(
            state=payload["state"],
            nonce=payload["nonce"],
            code_verifier=payload["cv"],
        )
    except (KeyError, TypeError, ValueError):
        return None


class OidcClient:
    def __init__(
        self,
        issuer: str,
        client_id: str,
        client_secret: str = "",
        scopes: str = "openid email profile",
        groups_claim: str = "groups",
        http: httpx.Client | None = None,
    ):
        parsed_issuer = urlsplit(issuer)
        if parsed_issuer.scheme != "https":
            hostname = (parsed_issuer.hostname or "").lower()
            if hostname not in ("localhost", "127.0.0.1", "::1"):
                raise ValueError("issuer must use https (except for localhost)")
        # Kept exactly as configured: OIDC Core requires an exact string
        # match against the token's `iss` claim, including trailing slash.
        self._issuer = issuer
        # A separate rstrip'd form is used only for building the discovery
        # URL, so a bare trailing slash doesn't produce a doubled-up path.
        self._discovery_base = issuer.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._scopes = scopes
        self._groups_claim = groups_claim
        self._http = http or httpx.Client(timeout=httpx.Timeout(10.0, connect=5.0))
        self._config: dict | None = None  # discovery cache
        self._jwks: dict[str, jwt.PyJWK] | None = None  # kid -> key cache
        self._lock = threading.Lock()  # guards _config/_jwks cache mutation

    def authorization_request(self, redirect_uri: str) -> tuple[str, StateBundle]:
        config = self._discover()
        state = secrets.token_urlsafe(24)
        nonce = secrets.token_urlsafe(24)
        code_verifier, code_challenge = _pkce_pair()
        params = {
            "response_type": "code",
            "client_id": self._client_id,
            "redirect_uri": redirect_uri,
            "scope": self._scopes,
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        url = f"{config['authorization_endpoint']}?{urlencode(params)}"
        return url, StateBundle(state=state, nonce=nonce, code_verifier=code_verifier)

    def complete(
        self, code: str, code_verifier: str, nonce: str, redirect_uri: str
    ) -> OidcIdentity:
        config = self._discover()
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
            "client_id": self._client_id,
        }
        if self._client_secret:
            data["client_secret"] = self._client_secret
        try:
            response = self._http.post(config["token_endpoint"], data=data)
        except httpx.TransportError as exc:
            raise OidcError("token endpoint request failed") from exc
        if response.status_code != 200:
            raise OidcError(f"token endpoint returned status {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise OidcError("token endpoint returned invalid json") from exc
        id_token = payload.get("id_token")
        if not isinstance(id_token, str) or not id_token:
            raise OidcError("token response missing id_token")

        claims = self._validate_id_token(id_token, nonce)

        email = claims.get("email")
        if not isinstance(email, str) or not email:
            raise OidcError("id token missing email claim")
        display_name = claims.get("name")
        if not isinstance(display_name, str):
            display_name = ""
        groups = claims.get(self._groups_claim)
        if isinstance(groups, str):
            groups = [groups]
        elif not isinstance(groups, list):
            groups = []
        return OidcIdentity(
            email=email,
            display_name=display_name,
            groups=[str(group) for group in groups],
        )

    def _discover(self) -> dict:
        with self._lock:
            if self._config is None:
                try:
                    response = self._http.get(
                        f"{self._discovery_base}/.well-known/openid-configuration"
                    )
                except httpx.TransportError as exc:
                    raise OidcError("discovery request failed") from exc
                if response.status_code != 200:
                    raise OidcError(f"discovery endpoint returned status {response.status_code}")
                try:
                    data = response.json()
                except ValueError as exc:
                    raise OidcError("discovery document is not valid json") from exc
                if not isinstance(data, dict):
                    raise OidcError("discovery document is not a json object")
                for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
                    if not isinstance(data.get(key), str) or not data[key]:
                        raise OidcError(f"discovery document missing {key}")
                self._config = data
            return self._config

    def _load_jwks(self) -> dict[str, jwt.PyJWK]:
        # _discover() takes its own lock; call it outside ours to avoid
        # nested acquisition of a non-reentrant Lock.
        config = self._discover()
        with self._lock:
            if self._jwks is None:
                try:
                    response = self._http.get(config["jwks_uri"])
                except httpx.TransportError as exc:
                    raise OidcError("jwks request failed") from exc
                if response.status_code != 200:
                    raise OidcError(f"jwks endpoint returned status {response.status_code}")
                try:
                    data = response.json()
                except ValueError as exc:
                    raise OidcError("jwks document is not valid json") from exc
                keys = data.get("keys") if isinstance(data, dict) else None
                if not isinstance(keys, list):
                    raise OidcError("jwks document malformed")
                jwks: dict[str, jwt.PyJWK] = {}
                for jwk_dict in keys:
                    if not isinstance(jwk_dict, dict):
                        continue
                    kid = jwk_dict.get("kid")
                    if not isinstance(kid, str) or not kid:
                        continue
                    try:
                        jwks[kid] = jwt.PyJWK(jwk_dict)
                    except (jwt.PyJWKError, ValueError, KeyError):
                        continue
                self._jwks = jwks
            return self._jwks

    def _signing_key(self, kid: str | None):
        jwks = self._load_jwks()
        key = jwks.get(kid) if kid else None
        if key is None:
            # Cache miss: the IdP may have rotated keys. Drop the cache and
            # re-fetch exactly once before giving up.
            with self._lock:
                self._jwks = None
            jwks = self._load_jwks()
            key = jwks.get(kid) if kid else None
        if key is None:
            raise OidcError("unknown signing key")
        return key.key

    def _validate_id_token(self, id_token: str, nonce: str) -> dict:
        if not nonce:
            raise OidcError("id token nonce mismatch")
        try:
            header = jwt.get_unverified_header(id_token)
        except jwt.InvalidTokenError as exc:
            raise OidcError("malformed id token") from exc
        signing_key = self._signing_key(header.get("kid"))
        try:
            payload = jwt.decode(
                id_token,
                key=signing_key,
                algorithms=_ID_TOKEN_ALGORITHMS,
                audience=self._client_id,
                issuer=self._issuer,
                options={"require": ["exp", "iss", "aud"]},
            )
        except jwt.exceptions.PyJWTError as exc:
            raise OidcError("id token validation failed") from exc
        # OIDC Core 3.1.3.7: if `aud` contains multiple entries, an `azp`
        # claim matching our client_id is required to guard against a token
        # meant for another audience member being replayed here.
        aud = payload.get("aud")
        if isinstance(aud, list) and len(aud) > 1 and payload.get("azp") != self._client_id:
            raise OidcError("id token validation failed")
        if payload.get("nonce") != nonce:
            raise OidcError("id token nonce mismatch")
        return payload
