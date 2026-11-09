from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.indices import get_index_store
from app.data.adjust import Bar
from app.data.custom_indices import CustomIndexStore
from app.main import app


class FakeBars:
    def get_daily(self, code: str, start: str, end: str, adjust: str) -> list[Bar]:
        return [
            Bar("2020-01-02", 10, 11, 9, 10, 100, 1000),
            Bar("2020-01-03", 11, 12, 10, 11, 100, 1100),
        ]


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    store = CustomIndexStore(tmp_path, FakeBars())  # type: ignore[arg-type]
    app.dependency_overrides[get_index_store] = lambda: store
    yield TestClient(app)
    app.dependency_overrides.clear()


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
