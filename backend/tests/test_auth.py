from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.auth import User, create_user, hash_password, verify_password
from app.db import Database, get_database
from app.main import app


@pytest.fixture
def client(database: Database) -> Iterator[TestClient]:
    app.dependency_overrides[get_database] = lambda: database
    yield TestClient(app)
    app.dependency_overrides.pop(get_database, None)


def test_password_hashing_roundtrip() -> None:
    encoded = hash_password("correct horse battery staple")

    assert encoded.startswith("scrypt$")
    assert verify_password("correct horse battery staple", encoded)
    assert not verify_password("wrong", encoded)


def test_login_me_and_logout(client: TestClient, database: Database) -> None:
    create_user(database, "alice", "password123")

    unauthenticated = client.get("/api/auth/me")
    assert unauthenticated.status_code == 401

    login = client.post(
        "/api/auth/login", json={"username": "alice", "password": "password123"}
    )
    assert login.status_code == 200
    assert login.json() == {"id": login.json()["id"], "username": "alice", "is_admin": False}
    assert client.cookies.get("stock_sim_session")

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["username"] == "alice"

    logout = client.post("/api/auth/logout")
    assert logout.status_code == 204
    assert client.get("/api/auth/me").status_code == 401


def test_wrong_password_and_inactive_user_are_rejected(
    client: TestClient, database: Database
) -> None:
    user = create_user(database, "bob", "password123")

    wrong = client.post(
        "/api/auth/login", json={"username": "bob", "password": "nope"}
    )
    assert wrong.status_code == 401

    with database.connect() as connection:
        connection.execute("UPDATE users SET is_active = 0 WHERE id = ?", (user.id,))

    inactive = client.post(
        "/api/auth/login", json={"username": "bob", "password": "password123"}
    )
    assert inactive.status_code == 401


def test_protected_api_requires_login() -> None:
    client = TestClient(app)

    assert client.get("/api/bars/daily", params={
        "code": "600000", "start": "2020-01-01", "end": "2020-12-31"
    }).status_code == 401
    assert client.get("/api/sim/state").status_code == 401
    assert client.get("/api/stocks/random").status_code == 401


def test_admin_creates_user_and_can_revoke_session(
    database: Database,
) -> None:
    import re

    from app.auth import get_user_by_id, get_user_by_username
    from app.bootstrap import authenticate

    admin = create_user(database, "boss", "password123", is_admin=True)
    app.dependency_overrides[get_database] = lambda: database
    client = TestClient(app)
    try:
        login = client.post(
            "/admin/login",
            data={"username": "boss", "password": "password123"},
            follow_redirects=False,
        )
        assert login.status_code == 303

        page = client.get("/admin")
        assert page.status_code == 200
        csrf = re.search(r'name="csrf" value="([^"]+)"', page.text)
        assert csrf is not None

        created = client.post(
            "/admin/users",
            data={
                "csrf": csrf.group(1),
                "username": "friend",
                "password": "friendpass",
            },
            follow_redirects=False,
        )
        assert created.status_code == 303
        friend = get_user_by_username(database, "friend")
        assert friend is not None
        assert friend["is_active"] == 1

        toggled = client.post(
            f"/admin/users/{friend['id']}/toggle",
            data={"csrf": csrf.group(1)},
            follow_redirects=False,
        )
        assert toggled.status_code == 303
        assert get_user_by_id(database, friend["id"])["is_active"] == 0  # type: ignore[index]
        assert authenticate(database, "friend", "friendpass") is None

        enabled = client.post(
            f"/admin/users/{friend['id']}/toggle",
            data={"csrf": csrf.group(1)},
            follow_redirects=False,
        )
        assert enabled.status_code == 303

        reset = client.post(
            f"/admin/users/{friend['id']}/password",
            data={"csrf": csrf.group(1), "password": "newpassword"},
            follow_redirects=False,
        )
        assert reset.status_code == 303
        assert isinstance(authenticate(database, "friend", "newpassword"), User)

        deleted = client.post(
            f"/admin/users/{friend['id']}/delete",
            data={"csrf": csrf.group(1)},
            follow_redirects=False,
        )
        assert deleted.status_code == 303
        assert get_user_by_id(database, friend["id"]) is None
        assert get_user_by_id(database, admin.id) is not None
    finally:
        app.dependency_overrides.pop(get_database, None)


def test_admin_page_requires_admin(database: Database) -> None:
    from app.auth import create_user

    create_user(database, "member", "password123")
    app.dependency_overrides[get_database] = lambda: database
    client = TestClient(app)
    try:
        login = client.post(
            "/api/auth/login",
            json={"username": "member", "password": "password123"},
        )
        assert login.status_code == 200
        assert client.get("/admin").status_code == 403
    finally:
        app.dependency_overrides.pop(get_database, None)
