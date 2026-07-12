from uuid import uuid4

import jwt as pyjwt
import pytest

from kilnworks.auth.tokens import issue_token, verify_token
from kilnworks.auth.users import User

USER = User(id=uuid4(), email="mike@example.com", principals=["public", "hr"])
K1 = "k1-" + "x" * 32
K2 = "k2-" + "y" * 32


def test_issue_and_verify_roundtrip():
    token = issue_token(USER, secret_key=K1)
    claims = verify_token(token, secret_key=K1)
    assert claims.user_id == USER.id
    assert claims.email == "mike@example.com"
    assert claims.principals == ["public", "hr"]


def test_wrong_key_and_garbage_return_none():
    token = issue_token(USER, secret_key=K1)
    assert verify_token(token, secret_key=K2) is None
    assert verify_token("not-a-jwt", secret_key=K1) is None


def test_expired_token_returns_none():
    token = issue_token(USER, secret_key=K1, ttl_minutes=-1)
    assert verify_token(token, secret_key=K1) is None


def test_empty_secret_rejected():
    with pytest.raises(ValueError, match="KILNWORKS_SECRET_KEY"):
        issue_token(USER, secret_key="")
    with pytest.raises(ValueError, match="KILNWORKS_SECRET_KEY"):
        verify_token("x", secret_key="")


def test_validly_signed_but_malformed_payload_returns_none():
    token = pyjwt.encode({"sub": "not-a-uuid", "exp": 9999999999}, K1, algorithm="HS256")
    assert verify_token(token, secret_key=K1) is None
    token = pyjwt.encode({"exp": 9999999999}, K1, algorithm="HS256")
    assert verify_token(token, secret_key=K1) is None
    token = pyjwt.encode({"sub": 123, "email": "e", "principals": ["p"], "exp": 9999999999},
                         K1, algorithm="HS256")
    assert verify_token(token, secret_key=K1) is None
    token = pyjwt.encode({"sub": str(USER.id), "email": "e", "principals": 7,
                          "exp": 9999999999}, K1, algorithm="HS256")
    assert verify_token(token, secret_key=K1) is None
    token = pyjwt.encode({"sub": str(USER.id), "email": "e", "principals": "abc",
                          "exp": 9999999999}, K1, algorithm="HS256")
    assert verify_token(token, secret_key=K1) is None
