import pytest

from kilnworks.auth.passwords import hash_password, verify_password
from kilnworks.auth.users import PgUserStore


def test_password_hash_roundtrip():
    hashed = hash_password("hunter2")
    assert hashed != "hunter2"
    assert verify_password(hashed, "hunter2") is True
    assert verify_password(hashed, "wrong") is False


def test_verify_password_returns_false_for_malformed_hash():
    assert verify_password("not-an-argon2-hash", "anything") is False


def test_create_and_authenticate_user(conn):
    store = PgUserStore(conn)
    user = store.create_user("mike@example.com", "hunter2", display_name="Mike",
                             principals=("public", "hr"))
    assert user.email == "mike@example.com"
    assert user.principals == ["public", "hr"]
    assert store.authenticate("mike@example.com", "hunter2").id == user.id
    assert store.authenticate("mike@example.com", "wrong") is None
    assert store.authenticate("nobody@example.com", "hunter2") is None


def test_duplicate_email_rejected(conn):
    store = PgUserStore(conn)
    store.create_user("dup@example.com", "pw")
    with pytest.raises(ValueError, match="already exists"):
        store.create_user("dup@example.com", "pw2")


def test_upsert_sso_user_creates_then_updates(conn):
    store = PgUserStore(conn)
    created = store.upsert_sso_user("sso@example.com", "Ada", ["public", "eng"])
    assert created.principals == ["public", "eng"]
    updated = store.upsert_sso_user("sso@example.com", "Ada L", ["public", "ops"])
    assert updated.id == created.id
    assert updated.display_name == "Ada L"
    assert updated.principals == ["public", "ops"]


def test_sso_user_cannot_password_login(conn):
    store = PgUserStore(conn)
    store.upsert_sso_user("sso@example.com", "Ada", ["public"])
    assert store.authenticate("sso@example.com", "anything") is None
    assert store.authenticate("sso@example.com", "") is None


def test_hybrid_user_keeps_password_login_after_sso_upsert(conn):
    store = PgUserStore(conn)
    created = store.create_user("hybrid@example.com", "hunter2", display_name="Mike")
    store.upsert_sso_user("hybrid@example.com", "Mike SSO", ["public", "eng"])
    authed = store.authenticate("hybrid@example.com", "hunter2")
    assert authed is not None
    assert authed.id == created.id
