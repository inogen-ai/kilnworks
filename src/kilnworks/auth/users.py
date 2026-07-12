from collections.abc import Sequence
from uuid import UUID, uuid4

import psycopg
from pydantic import BaseModel

from kilnworks.auth.passwords import hash_password, verify_password

_DUMMY_HASH = hash_password("kilnworks-dummy-password")


class User(BaseModel):
    id: UUID
    email: str
    display_name: str = ""
    principals: list[str] = ["public"]


class PgUserStore:
    def __init__(self, conn: psycopg.Connection):
        self._conn = conn

    def create_user(
        self,
        email: str,
        password: str,
        display_name: str = "",
        principals: Sequence[str] = ("public",),
    ) -> User:
        user = User(id=uuid4(), email=email, display_name=display_name,
                    principals=list(principals))
        try:
            self._conn.execute(
                """INSERT INTO users (id, email, password_hash, display_name, principals)
                   VALUES (%s, %s, %s, %s, %s)""",
                (user.id, user.email, hash_password(password), user.display_name,
                 user.principals),
            )
        except psycopg.errors.UniqueViolation as exc:
            raise ValueError(f"user already exists: {email}") from exc
        return user

    def upsert_sso_user(
        self, email: str, display_name: str = "", principals: Sequence[str] = ("public",)
    ) -> User:
        """SSO identities have no local password (NULL hash) and their display name and
        principals track the IdP: every login re-syncs them."""
        row = self._conn.execute(
            """INSERT INTO users (id, email, password_hash, display_name, principals)
               VALUES (%s, %s, NULL, %s, %s)
               ON CONFLICT (email) DO UPDATE
               SET display_name = EXCLUDED.display_name, principals = EXCLUDED.principals
               RETURNING id, email, display_name, principals""",
            (uuid4(), email, display_name, list(principals)),
        ).fetchone()
        return User(id=row[0], email=row[1], display_name=row[2], principals=row[3])

    def authenticate(self, email: str, password: str) -> User | None:
        row = self._conn.execute(
            "SELECT id, email, password_hash, display_name, principals FROM users"
            " WHERE email = %s",
            (email,),
        ).fetchone()
        if row is None:
            # no early exit on unknown email: keep the timing profile close to a real lookup
            verify_password(_DUMMY_HASH, password)
            return None
        if row[2] is None:
            # SSO-only user: no local password hash. Run the dummy verify to keep the
            # timing profile flat, same as the unknown-email path above.
            verify_password(_DUMMY_HASH, password)
            return None
        if not verify_password(row[2], password):
            return None
        return User(id=row[0], email=row[1], display_name=row[3], principals=row[4])
