import pytest

from app.data.adjust import Bar
from app.data.custom_indices import CustomIndexNotFound, CustomIndexStore
from app.db import Database

USER_ID = "user-1"


class FakeBars:
    def __init__(self, data: dict[str, list[Bar]]) -> None:
        self.data = data

    def get_daily(self, code: str, start: str, end: str, adjust: str) -> list[Bar]:
        return [bar for bar in self.data[code] if start <= bar.date <= end]


def make_bar(day: str, open_: float, high: float, low: float, close: float) -> Bar:
    return Bar(day, open_, high, low, close, 100.0, 1000.0)


@pytest.fixture
def stock_bars() -> FakeBars:
    return FakeBars(
        {
            "600000": [
                make_bar("2020-01-02", 100, 105, 95, 100),
                make_bar("2020-01-03", 110, 115, 108, 110),
            ],
            "000001": [
                make_bar("2020-01-02", 200, 210, 190, 200),
                make_bar("2020-01-03", 220, 230, 215, 220),
            ],
        }
    )


@pytest.fixture
def store(database: Database, stock_bars: FakeBars) -> CustomIndexStore:
    return CustomIndexStore(database, stock_bars)  # type: ignore[arg-type]


def test_creates_equal_weight_index_and_persists_definition(
    database: Database, store: CustomIndexStore, stock_bars: FakeBars
) -> None:
    created = store.create(USER_ID, "Test Index", ["600000", "000001"])
    loaded = CustomIndexStore(database, stock_bars).get(USER_ID, created.id)  # type: ignore[arg-type]

    assert created.base_date == "2020-01-02"
    assert [component.weight for component in loaded.components] == [0.5, 0.5]
    assert loaded.name == "Test Index"


def test_manual_weights_are_normalized(store: CustomIndexStore) -> None:
    index = store.create(USER_ID, "Weighted", ["600000", "000001"], [1, 3])

    assert [component.weight for component in index.components] == [0.25, 0.75]


def test_index_bars_use_weighted_component_returns(store: CustomIndexStore) -> None:
    index = store.create(USER_ID, "Equal", ["600000", "000001"])

    bars = store.daily_bars(USER_ID, index.id, "2020-01-02", "2020-01-03")

    assert bars[0].close == pytest.approx(1000)
    assert bars[1].close == pytest.approx(1100)
    assert bars[1].high == pytest.approx(1150)
    assert bars[1].volume == 200


def test_missing_day_carries_forward_previous_close(
    store: CustomIndexStore, stock_bars: FakeBars
) -> None:
    stock_bars.data["000001"].pop()
    index = store.create(USER_ID, "Paused", ["600000", "000001"])

    bars = store.daily_bars(USER_ID, index.id, "2020-01-03", "2020-01-03")

    assert bars[0].close == pytest.approx(1050)
    assert bars[0].volume == 100


def test_rejects_duplicate_codes_and_nonpositive_weights(
    store: CustomIndexStore,
) -> None:
    with pytest.raises(ValueError, match="重复"):
        store.create(USER_ID, "Duplicate", ["600000", "600000"])
    with pytest.raises(ValueError, match="正数"):
        store.create(USER_ID, "Bad weight", ["600000", "000001"], [0, 1])


def test_rejects_no_common_base_day(database: Database) -> None:
    store = CustomIndexStore(
        database,
        FakeBars(
            {
                "600000": [make_bar("2020-01-02", 1, 1, 1, 1)],
                "000001": [make_bar("2020-01-03", 1, 1, 1, 1)],
            }
        ),  # type: ignore[arg-type]
    )

    with pytest.raises(ValueError, match="共同交易日"):
        store.create(USER_ID, "No common", ["600000", "000001"])


def test_indices_are_private_to_each_user(store: CustomIndexStore) -> None:
    index = store.create(USER_ID, "Mine", ["600000"])

    assert store.list(USER_ID)[0].id == index.id
    assert store.list("someone-else") == []
    with pytest.raises(CustomIndexNotFound):
        store.get("someone-else", index.id)
