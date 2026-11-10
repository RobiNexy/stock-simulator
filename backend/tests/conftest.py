from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.auth import User
from app.db import Database
from app.deps import get_current_user
from app.main import app

USER_ID = "user-1"


@pytest.fixture
def database(tmp_path: Path) -> Database:
    database = Database(tmp_path / "test.db")
    database.initialize()
    from app.auth import hash_password

    with database.connect() as connection:
        connection.execute(
            "INSERT INTO users (id, username, password_hash, is_admin, is_active, created_at)"
            " VALUES (?, ?, ?, 0, 1, datetime('now'))",
            (USER_ID, "tester", hash_password("password123")),
        )
    return database


@pytest.fixture
def user() -> User:
    return User(USER_ID, "tester", False)


@pytest.fixture
def admin() -> User:
    return User("admin-1", "admin", True)


@pytest.fixture
def authed_client(user: User) -> Iterator[TestClient]:
    app.dependency_overrides[get_current_user] = lambda: user
    yield TestClient(app)
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def admin_client(admin: User) -> Iterator[TestClient]:
    app.dependency_overrides[get_current_user] = lambda: admin
    yield TestClient(app)
    app.dependency_overrides.pop(get_current_user, None)
