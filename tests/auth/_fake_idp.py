"""Shared fake-IdP test helpers: RSA signing key, JWKS document, ID-token
minting, and a MockTransport-compatible handler. Used by tests/auth/test_oidc.py
(unit tests against OidcClient directly) and tests/api/test_oidc_endpoints.py
(end-to-end tests against the /auth/oidc/* endpoints)."""

import base64
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl

import httpx
import jwt as pyjwt
from cryptography.hazmat.primitives.asymmetric import rsa

RSA_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_RSA_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
KID = "test-key-1"
ISSUER = "https://idp.test"
CLIENT_ID = "kilnworks-client"


def _b64url_uint(n: int) -> str:
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _jwks(extra_keys: list[dict] | None = None) -> dict:
    public_numbers = RSA_KEY.public_key().public_numbers()
    keys = [
        {
            "kty": "RSA",
            "kid": KID,
            "use": "sig",
            "alg": "RS256",
            "n": _b64url_uint(public_numbers.n),
            "e": _b64url_uint(public_numbers.e),
        }
    ]
    if extra_keys:
        keys.extend(extra_keys)
    return {"keys": keys}


def make_id_token(
    nonce: str,
    *,
    email: str | None = "ada@example.com",
    groups=("eng",),
    key=None,
    kid: str | None = KID,
    algorithm: str = "RS256",
    **overrides,
) -> str:
    claims = {
        "iss": ISSUER,
        "aud": CLIENT_ID,
        "sub": "idp-user-1",
        "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        "nonce": nonce,
        "name": "Ada",
        "groups": list(groups),
    }
    if email is not None:
        claims["email"] = email
    claims.update(overrides)
    signing_key = RSA_KEY if key is None else key
    headers = {"kid": kid} if kid is not None else {}
    return pyjwt.encode(claims, signing_key, algorithm=algorithm, headers=headers)


def make_alg_confusion_token(nonce: str) -> str:
    """An HS256 token 'signed' with the RSA public modulus's raw bytes,
    as an attacker who can see the JWKS document could construct."""
    public_numbers = RSA_KEY.public_key().public_numbers()
    n = public_numbers.n
    secret = n.to_bytes((n.bit_length() + 7) // 8, "big")
    claims = {
        "iss": ISSUER,
        "aud": CLIENT_ID,
        "sub": "idp-user-1",
        "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        "nonce": nonce,
        "email": "ada@example.com",
        "name": "Ada",
        "groups": ["eng"],
    }
    return pyjwt.encode(claims, secret, algorithm="HS256", headers={"kid": KID})


class FakeIdp:
    def __init__(self):
        self.jwks_calls = 0
        self.token_calls: list[dict] = []
        self.next_id_token: str | None = None
        self.token_status = 200
        self.extra_jwks_keys: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={
                    "authorization_endpoint": f"{ISSUER}/authorize",
                    "token_endpoint": f"{ISSUER}/token",
                    "jwks_uri": f"{ISSUER}/jwks",
                },
            )
        if path == "/jwks":
            self.jwks_calls += 1
            return httpx.Response(200, json=_jwks(self.extra_jwks_keys))
        if path == "/token":
            form = dict(parse_qsl(request.content.decode()))
            self.token_calls.append(form)
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={"error": "invalid_grant"})
            return httpx.Response(
                200, json={"id_token": self.next_id_token, "token_type": "Bearer"}
            )
        return httpx.Response(404)


def make_transport(fake_idp: FakeIdp) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(fake_idp.handler))
