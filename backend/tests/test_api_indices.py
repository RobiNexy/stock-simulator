from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api.indices import get_index_store
from app.data.adjust import Bar
from app.data.custom_indices import CustomIndexStore
from app.db import Database
from app.main import app


class FakeBars:
    def get_daily(self, code: str, start: str, end: str, adjust: str) -> list[Bar]:
        return [
            Bar("2020-01-02", 10, 11, 9, 10, 100, 1000),
            Bar("2020-01-03", 11, 12, 10, 11, 100, 1100),
        ]


@pytest.fixture
def client(database: Database, authed_client: TestClient) -> Iterator[TestClient]:
    store = CustomIndexStore(database, FakeBars())  # type: ignore[arg-type]
    app.dependency_overrides[get_index_store] = lambda: store
    yield authed_client
    app.dependency_overrides.pop(get_index_store, None)


def test_create_and_get_custom_index_daily_bars(client: TestClient) -> None:
    response = client.post(
        "/api/indices",
        json={"name": "My index", "components": [{"code": "600000"}]},
    )
    assert response.status_code == 201
    index_id = response.json()["id"]
    assert response.json()["components"][0]["weight"] == 1

    bars = client.get(
        f"/api/indices/{index_id}/daily",
        params={"start": "2020-01-02", "end": "2020-01-03"},
    )
    assert bars.status_code == 200
    assert bars.json()[1]["close"] == pytest.approx(1100)


def test_rejects_partial_manual_weights(client: TestClient) -> None:
    response = client.post(
        "/api/indices",
        json={
            "name": "Bad weights",
            "components": [
                {"code": "600000", "weight": 1},
                {"code": "000001"},
            ],
        },
    )
    assert response.status_code == 422


def test_other_users_cannot_see_index(
    client: TestClient, user: object
) -> None:
    created = client.post(
        "/api/indices",
        json={"name": "My index", "components": [{"code": "600000"}]},
    ).json()

    from app.auth import User
    from app.deps import get_current_user

    other = User("other-user", "other", False)
    app.dependency_overrides[get_current_user] = lambda: other
    try:
        assert client.get("/api/indices").json() == []
        assert client.get(f"/api/indices/{created['id']}/daily", params={
            "start": "2020-01-02", "end": "2020-01-03"
        }).status_code == 404
    finally:
        app.dependency_overrides[get_current_user] = lambda: user
