import base64
import hashlib
import hmac
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.db import Database

SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32


@dataclass(frozen=True)
class User:
    id: str
    username: str
    is_admin: bool


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
    )
    return "$".join(
        [
            "scrypt",
            str(SCRYPT_N),
            str(SCRYPT_R),
            str(SCRYPT_P),
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(digest).decode("ascii"),
        ]
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, n, r, p, salt_b64, digest_b64 = encoded.split("$")
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_user(
    database: Database,
    username: str,
    password: str,
    *,
    is_admin: bool = False,
) -> User:
    from uuid import uuid4

    user_id = uuid4().hex
    with database.connect() as connection:
        connection.execute(
            "INSERT INTO users (id, username, password_hash, is_admin, is_active, created_at)"
            " VALUES (?, ?, ?, ?, 1, ?)",
            (
                user_id,
                username,
                hash_password(password),
                int(is_admin),
                _iso(_now()),
            ),
        )
    return User(user_id, username, is_admin)


def list_users(database: Database) -> list[sqlite3.Row]:
    with database.connect() as connection:
        return list(
            connection.execute(
                "SELECT id, username, is_admin, is_active, created_at"
                " FROM users ORDER BY created_at, username"
            )
        )


def get_user_by_id(database: Database, user_id: str) -> sqlite3.Row | None:
    with database.connect() as connection:
        return connection.execute(
            "SELECT id, username, is_admin, is_active, created_at"
            " FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()


def get_user_by_username(database: Database, username: str) -> sqlite3.Row | None:
    with database.connect() as connection:
        return connection.execute(
            "SELECT id, username, password_hash, is_admin, is_active, created_at"
            " FROM users WHERE username = ?",
            (username,),
        ).fetchone()


def create_session(database: Database, user_id: str, days: int) -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    now = _now()
    with database.connect() as connection:
        connection.execute(
            "DELETE FROM sessions WHERE expires_at <= ?",
            (_iso(now),),
        )
        connection.execute(
            "INSERT INTO sessions (token_hash, user_id, csrf_token, created_at, expires_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                _token_hash(token),
                user_id,
                csrf_token,
                _iso(now),
                _iso(now + timedelta(days=days)),
            ),
        )
    return token, csrf_token


def resolve_session(
    database: Database, token: str | None
) -> tuple[User, str] | None:
    if not token:
        return None
    with database.connect() as connection:
        row = connection.execute(
            "SELECT u.id, u.username, u.is_admin, s.csrf_token"
            " FROM sessions s JOIN users u ON u.id = s.user_id"
            " WHERE s.token_hash = ? AND s.expires_at > ? AND u.is_active = 1",
            (_token_hash(token), _iso(_now())),
        ).fetchone()
    if row is None:
        return None
    return User(row["id"], row["username"], bool(row["is_admin"])), row["csrf_token"]


def delete_session(database: Database, token: str | None) -> None:
    if not token:
        return
    with database.connect() as connection:
        connection.execute(
            "DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),)
        )


def delete_user_sessions(database: Database, user_id: str) -> None:
    with database.connect() as connection:
        connection.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


def set_user_active(database: Database, user_id: str, active: bool) -> None:
    with database.connect() as connection:
        connection.execute(
            "UPDATE users SET is_active = ? WHERE id = ?", (int(active), user_id)
        )
        if not active:
            connection.execute(
                "DELETE FROM sessions WHERE user_id = ?", (user_id,)
            )


def set_user_password(database: Database, user_id: str, password: str) -> None:
    with database.connect() as connection:
        connection.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (hash_password(password), user_id),
        )
        connection.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


def delete_user(database: Database, user_id: str) -> None:
    with database.connect() as connection:
        connection.execute("DELETE FROM users WHERE id = ?", (user_id,))
