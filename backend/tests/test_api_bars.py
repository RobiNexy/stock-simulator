from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api.bars import get_store
from app.data.adjust import Bar
from app.data.baostock_source import DataSourceError
from app.data.daily_bars import DailyBarNotFound
from app.main import app


class StubStore:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    def get_daily(self, code: str, start: str, end: str, adjust: str) -> list[Bar]:
        if self.error is not None:
            raise self.error
        return [Bar("2020-01-02", 10.0, 11.0, 9.0, 10.5, 100.0, 1000.0)]


@pytest.fixture
def client(authed_client: TestClient) -> Iterator[TestClient]:
    yield authed_client
    app.dependency_overrides.pop(get_store, None)


def use_store(store: StubStore) -> None:
    app.dependency_overrides[get_store] = lambda: store


def test_returns_bars_as_json(client: TestClient) -> None:
    use_store(StubStore())

    res = client.get("/api/bars/daily", params={"code": "600000", "start": "2020-01-01", "end": "2020-12-31"})

    assert res.status_code == 200
    assert res.json() == [
        {"date": "2020-01-02", "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5, "volume": 100.0, "amount": 1000.0}
    ]


def test_unknown_range_returns_404(client: TestClient) -> None:
    use_store(StubStore(DailyBarNotFound("无数据")))

    res = client.get("/api/bars/daily", params={"code": "600000", "start": "2030-01-01", "end": "2030-12-31"})

    assert res.status_code == 404


def test_data_source_failure_returns_503(client: TestClient) -> None:
    use_store(StubStore(DataSourceError("登录失败")))

    res = client.get("/api/bars/daily", params={"code": "600000", "start": "2020-01-01", "end": "2020-12-31"})

    assert res.status_code == 503


def test_reversed_range_returns_422(client: TestClient) -> None:
    use_store(StubStore())

    res = client.get("/api/bars/daily", params={"code": "600000", "start": "2021-01-01", "end": "2020-12-31"})

    assert res.status_code == 422


def test_non_six_digit_code_returns_422(client: TestClient) -> None:
    use_store(StubStore())

    res = client.get("/api/bars/daily", params={"code": "60000", "start": "2020-01-01", "end": "2020-12-31"})

    assert res.status_code == 422


def test_unsupported_code_prefix_returns_422(client: TestClient) -> None:
    use_store(StubStore(ValueError("暂不支持该代码段")))

    res = client.get("/api/bars/daily", params={"code": "200001", "start": "2020-01-01", "end": "2020-12-31"})

    assert res.status_code == 422
