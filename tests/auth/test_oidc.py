import base64
import hashlib
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlparse

import httpx
import jwt as pyjwt
import pytest

from kilnworks.auth.oidc import (
    OidcClient,
    OidcError,
    StateBundle,
    decode_state,
    encode_state,
)
from kilnworks.auth.tokens import issue_token
from kilnworks.auth.users import User
from tests.auth._fake_idp import (
    CLIENT_ID,
    ISSUER,
    KID,
    OTHER_RSA_KEY,
    RSA_KEY,
    make_alg_confusion_token,
    make_id_token,
    make_transport,
)


def _client(fake_idp, **kwargs) -> OidcClient:
    return OidcClient(issuer=ISSUER, client_id=CLIENT_ID, http=make_transport(fake_idp), **kwargs)


def _raw_token(*, groups, nonce="nonce-1") -> str:
    """Builds an ID token with an arbitrary (possibly non-list) `groups` claim value —
    bypasses make_id_token's `list(groups)` coercion so shapes real IdPs might emit
    (a bare string, a number) can be exercised directly."""
    claims = {
        "iss": ISSUER,
        "aud": CLIENT_ID,
        "sub": "idp-user-1",
        "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        "nonce": nonce,
        "email": "ada@example.com",
        "name": "Ada",
        "groups": groups,
    }
    return pyjwt.encode(claims, RSA_KEY, algorithm="RS256", headers={"kid": KID})


# --- constructor: issuer scheme -----------------------------------------------


def test_constructor_rejects_http_issuer():
    with pytest.raises(ValueError):
        OidcClient(issuer="http://idp.example.com", client_id=CLIENT_ID)


def test_constructor_accepts_http_localhost_issuer():
    OidcClient(issuer="http://127.0.0.1:5556/dex", client_id=CLIENT_ID)


def test_constructor_accepts_http_localhost_hostname_issuer():
    OidcClient(issuer="http://localhost:5556/dex", client_id=CLIENT_ID)


def test_constructor_accepts_https_issuer(fake_idp):
    # Existing-behavior smoke check: unaffected by the scheme guard.
    _client(fake_idp)


# --- authorization_request --------------------------------------------------


def test_authorization_request_url_carries_pkce_and_state(fake_idp):
    client = _client(fake_idp)
    url, bundle = client.authorization_request("https://app.test/callback")

    parsed = urlparse(url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "idp.test"
    assert parsed.path == "/authorize"
    params = dict(parse_qsl(parsed.query))
    assert params["response_type"] == "code"
    assert params["client_id"] == CLIENT_ID
    assert params["redirect_uri"] == "https://app.test/callback"
    assert params["scope"] == "openid email profile"
    assert params["state"] == bundle.state
    assert params["nonce"] == bundle.nonce
    assert params["code_challenge_method"] == "S256"

    expected_challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(bundle.code_verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    assert params["code_challenge"] == expected_challenge


# --- complete: happy path ----------------------------------------------------


def test_complete_returns_identity_for_valid_token(fake_idp):
    client = _client(fake_idp)
    fake_idp.next_id_token = make_id_token(
        "nonce-1", email="ada@example.com", groups=("eng", "sre")
    )

    identity = client.complete(
        code="auth-code",
        code_verifier="verifier-value",
        nonce="nonce-1",
        redirect_uri="https://app.test/callback",
    )
    assert identity.email == "ada@example.com"
    assert identity.display_name == "Ada"
    assert identity.groups == ["eng", "sre"]


def test_complete_posts_expected_token_request_without_client_secret(fake_idp):
    client = _client(fake_idp)
    fake_idp.next_id_token = make_id_token("nonce-1")

    client.complete(
        code="auth-code",
        code_verifier="verifier-value",
        nonce="nonce-1",
        redirect_uri="https://app.test/callback",
    )
    assert len(fake_idp.token_calls) == 1
    form = fake_idp.token_calls[0]
    assert form["grant_type"] == "authorization_code"
    assert form["code"] == "auth-code"
    assert form["redirect_uri"] == "https://app.test/callback"
    assert form["code_verifier"] == "verifier-value"
    assert "client_secret" not in form


def test_complete_posts_client_secret_when_configured(fake_idp):
    client = _client(fake_idp, client_secret="shh-secret")
    fake_idp.next_id_token = make_id_token("nonce-1")

    client.complete(
        code="auth-code",
        code_verifier="verifier-value",
        nonce="nonce-1",
        redirect_uri="https://app.test/callback",
    )
    form = fake_idp.token_calls[0]
    assert form["client_secret"] == "shh-secret"


# --- complete: rejections ----------------------------------------------------


def test_complete_rejects_wrong_issuer(fake_idp):
    client = _client(fake_idp)
    fake_idp.next_id_token = make_id_token("nonce-1", iss="https://evil.test")
    with pytest.raises(OidcError):
        client.complete("c", "v", "nonce-1", "https://app.test/callback")


def test_complete_rejects_wrong_audience(fake_idp):
    client = _client(fake_idp)
    fake_idp.next_id_token = make_id_token("nonce-1", aud="someone-else")
    with pytest.raises(OidcError):
        client.complete("c", "v", "nonce-1", "https://app.test/callback")


def test_complete_rejects_expired_token(fake_idp):
    client = _client(fake_idp)
    fake_idp.next_id_token = make_id_token(
        "nonce-1", exp=datetime.now(timezone.utc) - timedelta(minutes=5)
    )
    with pytest.raises(OidcError):
        client.complete("c", "v", "nonce-1", "https://app.test/callback")


def test_complete_rejects_nonce_mismatch(fake_idp):
    client = _client(fake_idp)
    fake_idp.next_id_token = make_id_token("nonce-1")
    with pytest.raises(OidcError):
        client.complete("c", "v", "different-nonce", "https://app.test/callback")


def test_complete_rejects_unknown_kid_after_one_refetch(fake_idp):
    client = _client(fake_idp)
    fake_idp.next_id_token = make_id_token("nonce-1", kid="unknown-key")
    with pytest.raises(OidcError):
        client.complete("c", "v", "nonce-1", "https://app.test/callback")
    assert fake_idp.jwks_calls == 2


def test_complete_rejects_token_signed_by_different_key(fake_idp):
    client = _client(fake_idp)
    fake_idp.next_id_token = make_id_token("nonce-1", key=OTHER_RSA_KEY)
    with pytest.raises(OidcError):
        client.complete("c", "v", "nonce-1", "https://app.test/callback")


def test_complete_rejects_missing_email_claim(fake_idp):
    client = _client(fake_idp)
    fake_idp.next_id_token = make_id_token("nonce-1", email=None)
    with pytest.raises(OidcError):
        client.complete("c", "v", "nonce-1", "https://app.test/callback")


def test_complete_rejects_token_endpoint_400(fake_idp):
    client = _client(fake_idp)
    fake_idp.token_status = 400
    with pytest.raises(OidcError):
        client.complete("c", "v", "nonce-1", "https://app.test/callback")


def test_complete_rejects_alg_confusion_hs256_token(fake_idp):
    client = _client(fake_idp)
    fake_idp.next_id_token = make_alg_confusion_token("nonce-1")
    with pytest.raises(OidcError):
        client.complete("c", "v", "nonce-1", "https://app.test/callback")


def test_complete_rejects_empty_nonce(fake_idp):
    client = _client(fake_idp)
    fake_idp.next_id_token = make_id_token("nonce-1")
    with pytest.raises(OidcError):
        client.complete("c", "v", "", "https://app.test/callback")


def test_complete_rejects_key_matching_kid_with_wrong_key_type(fake_idp):
    """A JWKS entry whose kid matches the token's, but whose kty is `oct`
    (e.g. a misconfigured or hostile IdP), must surface as OidcError, not
    let jwt.exceptions.InvalidKeyError escape the OidcError contract."""
    client = _client(fake_idp)
    fake_idp.extra_jwks_keys = [
        {
            "kty": "oct",
            "kid": "oct-key",
            "k": "c2VjcmV0LWJ5dGVzLWZvci10ZXN0aW5n",
        }
    ]
    fake_idp.next_id_token = make_id_token("nonce-1", kid="oct-key")
    with pytest.raises(OidcError):
        client.complete("c", "v", "nonce-1", "https://app.test/callback")


# --- complete: multi-audience / azp -------------------------------------------


def test_complete_rejects_multi_audience_without_azp(fake_idp):
    client = _client(fake_idp)
    fake_idp.next_id_token = make_id_token(
        "nonce-1", aud=[CLIENT_ID, "evil-client"]
    )
    with pytest.raises(OidcError):
        client.complete("c", "v", "nonce-1", "https://app.test/callback")


def test_complete_accepts_multi_audience_with_correct_azp(fake_idp):
    client = _client(fake_idp)
    fake_idp.next_id_token = make_id_token(
        "nonce-1", aud=[CLIENT_ID, "evil-client"], azp=CLIENT_ID
    )
    identity = client.complete("c", "v", "nonce-1", "https://app.test/callback")
    assert identity.email == "ada@example.com"


def test_complete_rejects_multi_audience_with_wrong_azp(fake_idp):
    client = _client(fake_idp)
    fake_idp.next_id_token = make_id_token(
        "nonce-1", aud=[CLIENT_ID, "evil-client"], azp="evil-client"
    )
    with pytest.raises(OidcError):
        client.complete("c", "v", "nonce-1", "https://app.test/callback")


# --- groups claim -------------------------------------------------------------


def test_complete_honors_configurable_groups_claim(fake_idp):
    client = _client(fake_idp, groups_claim="roles")
    fake_idp.next_id_token = make_id_token("nonce-1", groups=("ignored",), roles=["admin", "eng"])
    identity = client.complete("c", "v", "nonce-1", "https://app.test/callback")
    assert identity.groups == ["admin", "eng"]


def test_complete_missing_groups_claim_defaults_to_empty_list(fake_idp):
    client = _client(fake_idp, groups_claim="roles")
    fake_idp.next_id_token = make_id_token("nonce-1")
    identity = client.complete("c", "v", "nonce-1", "https://app.test/callback")
    assert identity.groups == []


def test_complete_coerces_bare_string_groups_claim_to_single_element_list(fake_idp):
    # Some IdPs emit a single group as a bare string rather than a one-element
    # array; silently defaulting that to [] would drop real ACL data.
    client = _client(fake_idp)
    fake_idp.next_id_token = _raw_token(groups="engineering")
    identity = client.complete("c", "v", "nonce-1", "https://app.test/callback")
    assert identity.groups == ["engineering"]


def test_complete_non_list_non_str_groups_claim_defaults_to_empty_list(fake_idp):
    client = _client(fake_idp)
    fake_idp.next_id_token = _raw_token(groups=42)
    identity = client.complete("c", "v", "nonce-1", "https://app.test/callback")
    assert identity.groups == []


# --- issuer trailing slash ----------------------------------------------------


def test_complete_succeeds_when_configured_issuer_has_trailing_slash(fake_idp):
    # The fake IdP's discovery doc is fixed to ISSUER regardless of what the
    # client was configured with, so this only needs a trailing-slash issuer
    # on the client plus a matching `iss` claim on the token.
    trailing_issuer = f"{ISSUER}/"
    http = httpx.Client(transport=httpx.MockTransport(fake_idp.handler))
    client = OidcClient(issuer=trailing_issuer, client_id=CLIENT_ID, http=http)
    fake_idp.next_id_token = make_id_token("nonce-1", iss=trailing_issuer)

    identity = client.complete("c", "v", "nonce-1", "https://app.test/callback")
    assert identity.email == "ada@example.com"


# --- state cookie encode/decode ----------------------------------------------


def test_state_round_trip():
    bundle = StateBundle(state="s1", nonce="n1", code_verifier="cv1")
    encoded = encode_state(bundle, "secret-key")
    decoded = decode_state(encoded, "secret-key")
    assert decoded == bundle


def test_state_tampered_value_returns_none():
    bundle = StateBundle(state="s1", nonce="n1", code_verifier="cv1")
    encoded = encode_state(bundle, "secret-key")
    tampered = encoded[:-2] + ("aa" if encoded[-2:] != "aa" else "bb")
    assert decode_state(tampered, "secret-key") is None


def test_state_wrong_secret_returns_none():
    bundle = StateBundle(state="s1", nonce="n1", code_verifier="cv1")
    encoded = encode_state(bundle, "secret-key")
    assert decode_state(encoded, "wrong-secret") is None


def test_state_expired_returns_none():
    payload = {
        "state": "s1",
        "nonce": "n1",
        "cv": "cv1",
        "aud": "kilnworks-oidc-state",
        "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
    }
    expired = pyjwt.encode(payload, "secret-key", algorithm="HS256")
    assert decode_state(expired, "secret-key") is None


def test_state_rejects_real_kilnworks_user_token():
    user = User(id="11111111-1111-1111-1111-111111111111", email="ada@example.com")
    user_token = issue_token(user, "secret-key")
    assert decode_state(user_token, "secret-key") is None


def test_state_missing_exp_returns_none():
    # All server-minted states carry `exp`; a token lacking it entirely (as opposed
    # to one with a past `exp`) must also be rejected — defense in depth against a
    # signing key that finds its way into some other token-issuing path.
    payload = {
        "state": "s1",
        "nonce": "n1",
        "cv": "cv1",
        "aud": "kilnworks-oidc-state",
    }
    no_exp = pyjwt.encode(payload, "secret-key", algorithm="HS256")
    assert decode_state(no_exp, "secret-key") is None
