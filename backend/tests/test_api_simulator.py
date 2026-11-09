from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.simulator import get_simulator
from app.data.adjust import Bar
from app.data.simulator import PaperSimulator
from app.main import app


class FakeAssets:
    def get_daily(self, asset_type: str, asset_id: str, start: str, end: str, adjust: str) -> list[Bar]:
        return [
            Bar("2020-01-02", 10, 12, 9, 11, 1000, 11000),
            Bar("2020-01-03", 12, 14, 11, 13, 1000, 13000),
        ]


class FakeIndices:
    def get(self, index_id: str) -> object:
        return object()


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    simulator = PaperSimulator(
        tmp_path / "account.json", FakeAssets(), FakeIndices()  # type: ignore[arg-type]
    )
    app.dependency_overrides[get_simulator] = lambda: simulator
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_api_start_order_and_step(client: TestClient) -> None:
    started = client.post(
        "/api/sim/start",
        json={"asset_type": "stock", "asset_id": "600000", "start_date": "2020-01-02"},
    )
    assert started.status_code == 200
    assert started.json()["current_date"] == "2020-01-02"

    order = client.post("/api/sim/orders", json={"side": "buy", "quantity": 100})
    assert order.status_code == 200
    assert order.json()["pending_order"] is not None

    stepped = client.post("/api/sim/step")
    assert stepped.status_code == 200
    assert stepped.json()["current_date"] == "2020-01-03"
    assert stepped.json()["recent_trades"][-1]["status"] == "filled"


def test_api_requires_active_simulation(client: TestClient) -> None:
    response = client.post("/api/sim/orders", json={"side": "buy", "quantity": 100})
    assert response.status_code == 404


def test_api_validates_start_and_order_inputs(client: TestClient) -> None:
    bad_start = client.post(
        "/api/sim/start",
        json={"asset_type": "stock", "asset_id": "bad", "start_date": "2020-01-02"},
    )
    assert bad_start.status_code == 422
    bad_order = client.post("/api/sim/orders", json={"side": "buy", "quantity": -1})
    assert bad_order.status_code == 422
