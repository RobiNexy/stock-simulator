from types import SimpleNamespace

import pytest

from app.data.account_store import InMemoryAccountRepository
from app.data.adjust import Bar
from app.data.simulator import PaperSimulator

USER_ID = "user-1"


class FakeAssets:
    def __init__(self) -> None:
        self.data = [
            Bar("2020-01-02", 10, 12, 9, 11, 1000, 11000),
            Bar("2020-01-03", 12, 14, 11, 13, 1000, 13000),
            Bar("2020-01-06", 13, 15, 12, 14, 1000, 14000),
            Bar("2020-01-07", 14, 16, 13, 15, 1000, 15000),
        ]

    def get_daily(
        self, asset_type: str, asset_id: str, start: str, end: str, adjust: str
    ) -> list[Bar]:
        return [bar for bar in self.data if start <= bar.date <= end]


class FakeIndices:
    def get(self, user_id: str, index_id: str) -> object:
        if index_id != "a" * 32:
            raise ValueError("not found")
        return SimpleNamespace(base_date="2020-01-02")

    def daily_bars(
        self, user_id: str, index_id: str, start: str, end: str
    ) -> list[Bar]:
        return FakeAssets().get_daily("stock", "600000", start, end, "none")


@pytest.fixture
def accounts() -> InMemoryAccountRepository:
    return InMemoryAccountRepository()


@pytest.fixture
def simulator(accounts: InMemoryAccountRepository) -> PaperSimulator:
    return PaperSimulator(
        accounts,
        USER_ID,
        FakeAssets(),  # type: ignore[arg-type]
        FakeIndices(),  # type: ignore[arg-type]
    )


def test_start_and_step_never_expose_future_bars(simulator: PaperSimulator) -> None:
    state = simulator.start("stock", "600000", "2020-01-03")
    assert state["current_date"] == "2020-01-03"
    assert [bar["date"] for bar in state["bars"]] == ["2020-01-02", "2020-01-03"]  # type: ignore[index]

    stepped = simulator.step()
    assert stepped["current_date"] == "2020-01-06"
    assert all(bar["date"] <= "2020-01-06" for bar in stepped["bars"])  # type: ignore[index]


def test_buy_fills_on_next_bar_and_t1_prevents_same_day_sale(
    simulator: PaperSimulator,
) -> None:
    simulator.start("stock", "600000", "2020-01-02")
    simulator.submit_order("buy", 100)

    filled = simulator.step()
    assert filled["portfolio"]["quantity"] == 100  # type: ignore[index]
    assert filled["portfolio"]["cash"] == 998794.88  # type: ignore[index]
    assert filled["recent_trades"][-1]["price"] == 12  # type: ignore[index]
    with pytest.raises(ValueError, match="可卖数量不足"):
        simulator.submit_order("sell", 100)

    simulator.step()
    simulator.submit_order("sell", 100)
    state = simulator.step()
    assert state["portfolio"]["quantity"] == 0  # type: ignore[index]
    assert state["recent_trades"][-1]["status"] == "filled"  # type: ignore[index]


def test_stock_orders_require_lots_and_custom_index_uses_integer_units(
    simulator: PaperSimulator,
) -> None:
    simulator.start("stock", "600000", "2020-01-02")
    with pytest.raises(ValueError, match="100 股"):
        simulator.submit_order("buy", 1)

    simulator.reset()
    simulator.start("custom_index", "a" * 32, "2020-01-02")
    simulator.submit_order("buy", 1)


def test_pending_order_persists_across_simulator_restart(
    accounts: InMemoryAccountRepository, simulator: PaperSimulator
) -> None:
    simulator.start("stock", "600000", "2020-01-02")
    simulator.submit_order("buy", 100)

    restarted = PaperSimulator(
        accounts, USER_ID, FakeAssets(), FakeIndices()  # type: ignore[arg-type]
    )
    assert restarted.state()["pending_order"] is not None
    result = restarted.step()
    assert result["recent_trades"][-1]["status"] == "filled"  # type: ignore[index]


def test_random_price_mode_is_repeatable_for_same_seed(
    accounts: InMemoryAccountRepository, simulator: PaperSimulator
) -> None:
    simulator.start("stock", "600000", "2020-01-02")
    simulator.update_settings(price_mode="random")
    seed = accounts.load(USER_ID).random_seed
    simulator.submit_order("buy", 100)
    first_price = simulator.step()["recent_trades"][-1]["price"]  # type: ignore[index]

    simulator.reset()
    simulator.start("stock", "600000", "2020-01-02")
    account = accounts.load(USER_ID)
    account.price_mode = "random"
    account.random_seed = seed
    accounts.save(USER_ID, account)
    simulator.submit_order("buy", 100)
    second_price = simulator.step()["recent_trades"][-1]["price"]  # type: ignore[index]
    assert first_price == second_price


def test_cash_shortage_rejects_order_without_changing_portfolio(
    simulator: PaperSimulator,
) -> None:
    simulator.reset(100)
    simulator.start("stock", "600000", "2020-01-02")
    simulator.submit_order("buy", 100)
    state = simulator.step()

    assert state["portfolio"]["cash"] == 100  # type: ignore[index]
    assert state["portfolio"]["quantity"] == 0  # type: ignore[index]
    assert state["recent_trades"][-1]["status"] == "rejected"  # type: ignore[index]
