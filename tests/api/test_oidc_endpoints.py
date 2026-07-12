from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, unquote, urlparse

import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient

from kilnworks.api.app import create_app
from kilnworks.auth.oidc import OidcClient, OidcError, decode_state
from kilnworks.auth.tokens import verify_token
from kilnworks.db.connection import connect, init_db
from kilnworks.settings import Settings
from tests.auth._fake_idp import CLIENT_ID, ISSUER, make_id_token, make_transport

SECRET_KEY = "test-secret-0123456789abcdef-0123456789abcdef"


@pytest.fixture()
def oidc_settings(pg_url, tmp_path):
    return Settings(
        database_url=pg_url,
        fake_providers=True,
        secret_key=SECRET_KEY,
        openai_api_key="",
        web_dist_dir=str(tmp_path / "no-dist"),
        oidc_issuer=ISSUER,
        oidc_client_id=CLIENT_ID,
    )


@pytest.fixture()
def oidc_client(oidc_settings, fake_idp):
    conn = connect(oidc_settings.database_url)
    init_db(conn)
    app = create_app(oidc_settings)
    # Swap in an OidcClient wired to the fake IdP's MockTransport so no real
    # network call happens; construction itself does no I/O.
    app.state.oidc = OidcClient(
        issuer=ISSUER, client_id=CLIENT_ID, http=make_transport(fake_idp)
    )
    with TestClient(app) as test_client:
        yield test_client
    conn.execute("TRUNCATE documents CASCADE")
    conn.execute("TRUNCATE cost_events")
    conn.execute("TRUNCATE users")
    conn.execute("TRUNCATE jobs")
    conn.close()


def _login(oidc_client, oidc_settings):
    """Drive GET /auth/oidc/login and return (cookie_value, bundle, params)."""
    response = oidc_client.get("/auth/oidc/login", follow_redirects=False)
    assert response.status_code == 302
    cookie = response.cookies["kilnworks_oidc"]
    bundle = decode_state(cookie, oidc_settings.secret_key)
    assert bundle is not None
    params = dict(parse_qsl(urlparse(response.headers["location"]).query))
    return cookie, bundle, params, response


def _extract_token(location: str) -> str:
    assert location.startswith("/#token=")
    return unquote(location[len("/#token=") :])


def _extract_error(location: str) -> str:
    assert location.startswith("/#sso_error=")
    return unquote(location[len("/#sso_error=") :])


# --- /auth/config -------------------------------------------------------------


def test_auth_config_reports_disabled_by_default(client):
    response = client.get("/auth/config")
    assert response.status_code == 200
    assert response.json() == {"sso_enabled": False}


def test_auth_config_reports_enabled_when_oidc_configured(oidc_client):
    response = oidc_client.get("/auth/config")
    assert response.status_code == 200
    assert response.json() == {"sso_enabled": True}


# --- /auth/oidc/login -----------------------------------------------------


def test_oidc_login_404_when_disabled(client):
    response = client.get("/auth/oidc/login", follow_redirects=False)
    assert response.status_code == 404


def test_oidc_callback_404_when_disabled(client):
    response = client.get("/auth/oidc/callback", follow_redirects=False)
    assert response.status_code == 404


def test_oidc_login_redirects_with_signed_state_cookie(oidc_client, oidc_settings):
    cookie, bundle, params, response = _login(oidc_client, oidc_settings)
    parsed = urlparse(response.headers["location"])
    assert parsed.scheme == "https"
    assert parsed.netloc == "idp.test"
    assert parsed.path == "/authorize"
    assert params["state"] == bundle.state

    set_cookie_header = response.headers.get("set-cookie", "").lower()
    assert "httponly" in set_cookie_header
    assert "samesite=lax" in set_cookie_header


class _UnreachableOidc:
    """Stand-in for an OidcClient whose IdP discovery is unreachable."""

    def authorization_request(self, redirect_uri: str):
        raise OidcError("discovery request failed")


def test_oidc_login_redirects_to_sso_error_when_idp_unreachable(oidc_client):
    oidc_client.app.state.oidc = _UnreachableOidc()
    response = oidc_client.get("/auth/oidc/login", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"].startswith("/#sso_error=")
    # A stale kilnworks_oidc cookie from a prior login attempt must not survive
    # an unreachable-IdP response: the Set-Cookie header carries its deletion
    # (empty value, expired) rather than a fresh signed state.
    set_cookie_header = response.headers.get("set-cookie", "")
    assert "kilnworks_oidc=" in set_cookie_header
    assert 'kilnworks_oidc=""' in set_cookie_header or "kilnworks_oidc=;" in set_cookie_header
    assert "kilnworks_oidc" not in response.cookies


def test_oidc_login_cookie_not_secure_over_http(oidc_client):
    response = oidc_client.get("/auth/oidc/login", follow_redirects=False)
    set_cookie_header = response.headers.get("set-cookie", "")
    assert "secure" not in set_cookie_header.lower()


def test_oidc_login_cookie_secure_over_https(oidc_settings, fake_idp):
    conn = connect(oidc_settings.database_url)
    init_db(conn)
    app = create_app(oidc_settings)
    app.state.oidc = OidcClient(
        issuer=ISSUER, client_id=CLIENT_ID, http=make_transport(fake_idp)
    )
    with TestClient(app, base_url="https://testserver") as https_client:
        response = https_client.get("/auth/oidc/login", follow_redirects=False)
    conn.execute("TRUNCATE documents CASCADE")
    conn.execute("TRUNCATE cost_events")
    conn.execute("TRUNCATE users")
    conn.execute("TRUNCATE jobs")
    conn.close()

    set_cookie_header = response.headers.get("set-cookie", "")
    assert "secure" in set_cookie_header.lower()


# --- /auth/oidc/callback: happy path ---------------------------------------


def test_oidc_callback_happy_path_issues_token_and_upserts_user(
    oidc_client, oidc_settings, fake_idp
):
    cookie, bundle, params, _ = _login(oidc_client, oidc_settings)
    fake_idp.next_id_token = make_id_token(bundle.nonce, groups=("eng",))

    response = oidc_client.get(
        "/auth/oidc/callback",
        params={"code": "fake-code", "state": bundle.state},
        follow_redirects=False,
    )
    assert response.status_code == 302
    token = _extract_token(response.headers["location"])
    claims = verify_token(token, oidc_settings.secret_key)
    assert claims is not None
    assert claims.email == "ada@example.com"
    assert sorted(claims.principals) == ["eng", "public"]

    # cookie cleared
    set_cookie_header = response.headers.get("set-cookie", "")
    assert "kilnworks_oidc=" in set_cookie_header
    assert "Max-Age=0" in set_cookie_header

    # user row upserted
    conn = connect(oidc_settings.database_url)
    row = conn.execute(
        "SELECT email, principals FROM users WHERE email = %s", ("ada@example.com",)
    ).fetchone()
    conn.close()
    assert row is not None
    assert sorted(row[1]) == ["eng", "public"]


def test_second_login_updates_principals_after_group_change(
    oidc_client, oidc_settings, fake_idp
):
    cookie, bundle, _, _ = _login(oidc_client, oidc_settings)
    fake_idp.next_id_token = make_id_token(bundle.nonce, groups=("eng",))
    first = oidc_client.get(
        "/auth/oidc/callback",
        params={"code": "fake-code", "state": bundle.state},
        follow_redirects=False,
    )
    first_claims = verify_token(_extract_token(first.headers["location"]), oidc_settings.secret_key)
    assert sorted(first_claims.principals) == ["eng", "public"]

    cookie2, bundle2, _, _ = _login(oidc_client, oidc_settings)
    fake_idp.next_id_token = make_id_token(bundle2.nonce, groups=("eng", "admin"))
    second = oidc_client.get(
        "/auth/oidc/callback",
        params={"code": "fake-code-2", "state": bundle2.state},
        follow_redirects=False,
    )
    second_claims = verify_token(
        _extract_token(second.headers["location"]), oidc_settings.secret_key
    )
    assert sorted(second_claims.principals) == ["admin", "eng", "public"]
    assert second_claims.user_id == first_claims.user_id


def test_callback_dedupes_duplicate_groups_preserving_order(
    oidc_client, oidc_settings, fake_idp
):
    cookie, bundle, _, _ = _login(oidc_client, oidc_settings)
    fake_idp.next_id_token = make_id_token(bundle.nonce, groups=("eng", "eng", "public"))

    response = oidc_client.get(
        "/auth/oidc/callback",
        params={"code": "fake-code", "state": bundle.state},
        follow_redirects=False,
    )
    token = _extract_token(response.headers["location"])
    claims = verify_token(token, oidc_settings.secret_key)
    assert claims is not None
    assert claims.principals == ["public", "eng"]


# --- /auth/oidc/callback: failure paths -------------------------------------


def test_oidc_callback_no_cookie_never_500(oidc_client, oidc_settings):
    response = oidc_client.get(
        "/auth/oidc/callback",
        params={"code": "fake-code", "state": "whatever"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["location"].startswith("/#sso_error=")


def test_oidc_callback_state_mismatch(oidc_client, oidc_settings):
    cookie, bundle, _, _ = _login(oidc_client, oidc_settings)
    response = oidc_client.get(
        "/auth/oidc/callback",
        params={"code": "fake-code", "state": "not-the-real-state"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    _extract_error(response.headers["location"])


def test_oidc_callback_expired_cookie(oidc_client, oidc_settings):
    cookie, bundle, _, _ = _login(oidc_client, oidc_settings)
    expired_cookie = pyjwt.encode(
        {
            "state": bundle.state,
            "nonce": bundle.nonce,
            "cv": bundle.code_verifier,
            "aud": "kilnworks-oidc-state",
            "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
        },
        oidc_settings.secret_key,
        algorithm="HS256",
    )
    oidc_client.cookies.set("kilnworks_oidc", expired_cookie)
    response = oidc_client.get(
        "/auth/oidc/callback",
        params={"code": "fake-code", "state": bundle.state},
        follow_redirects=False,
    )
    assert response.status_code == 302
    _extract_error(response.headers["location"])


def test_oidc_callback_tampered_cookie(oidc_client, oidc_settings):
    cookie, bundle, _, _ = _login(oidc_client, oidc_settings)
    tampered = cookie[:-2] + ("aa" if cookie[-2:] != "aa" else "bb")
    oidc_client.cookies.set("kilnworks_oidc", tampered)
    response = oidc_client.get(
        "/auth/oidc/callback",
        params={"code": "fake-code", "state": bundle.state},
        follow_redirects=False,
    )
    assert response.status_code == 302
    _extract_error(response.headers["location"])


def test_oidc_callback_missing_cookie_but_valid_state_param(oidc_client, oidc_settings):
    cookie, bundle, _, _ = _login(oidc_client, oidc_settings)
    oidc_client.cookies.delete("kilnworks_oidc")
    response = oidc_client.get(
        "/auth/oidc/callback",
        params={"code": "fake-code", "state": bundle.state},
        follow_redirects=False,
    )
    assert response.status_code == 302
    _extract_error(response.headers["location"])


def test_oidc_callback_missing_code(oidc_client, oidc_settings):
    cookie, bundle, _, _ = _login(oidc_client, oidc_settings)
    response = oidc_client.get(
        "/auth/oidc/callback",
        params={"state": bundle.state},
        follow_redirects=False,
    )
    assert response.status_code == 302
    _extract_error(response.headers["location"])


def test_oidc_callback_oidc_error_from_complete(oidc_client, oidc_settings, fake_idp):
    cookie, bundle, _, _ = _login(oidc_client, oidc_settings)
    fake_idp.token_status = 400
    response = oidc_client.get(
        "/auth/oidc/callback",
        params={"code": "fake-code", "state": bundle.state},
        follow_redirects=False,
    )
    assert response.status_code == 302
    _extract_error(response.headers["location"])


def test_oidc_callback_missing_email_claim(oidc_client, oidc_settings, fake_idp):
    cookie, bundle, _, _ = _login(oidc_client, oidc_settings)
    fake_idp.next_id_token = make_id_token(bundle.nonce, email=None)
    response = oidc_client.get(
        "/auth/oidc/callback",
        params={"code": "fake-code", "state": bundle.state},
        follow_redirects=False,
    )
    assert response.status_code == 302
    _extract_error(response.headers["location"])
