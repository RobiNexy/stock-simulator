import math

import numpy as np
import pytest

from research.macd_search_v2 import (
    StockData,
    binomial_one_sided_p,
    block_bootstrap_sharpe_diff,
    ema_alpha,
    execute_signals,
    max_drawdown,
    macd_lines,
)


def make_stock(opens: list[float], closes: list[float]) -> StockData:
    open_array = np.array(opens, dtype=float)
    close_array = np.array(closes, dtype=float)
    dates = [f"2024-01-{index + 1:02d}" for index in range(len(opens))]
    return StockData(
        code="600000",
        dates=dates,
        open=open_array,
        close=close_array,
        log_open=np.log(open_array),
        log_close=np.log(close_array),
    )


def test_execution_uses_next_open_and_charges_entry_cost() -> None:
    stock = make_stock([10, 10, 12, 12], [10, 10, 12, 12])
    result = execute_signals(stock, np.array([1.0, 1.0]), cost=0.0005)
    assert result.positions.tolist() == [1.0, 1.0]
    assert result.turnover.tolist() == [1.0, 0.0]
    assert result.pnl[0] == pytest.approx(math.log(12 / 10) - 0.0005)
    assert result.pnl[1] == pytest.approx(0.0)


def test_limit_up_blocks_buy() -> None:
    stock = make_stock([10, 11, 11, 11], [10, 11, 11, 11])
    result = execute_signals(stock, np.array([1.0, 1.0]), cost=0.0)
    assert result.positions.tolist() == [0.0, 1.0]
    assert result.blocked == 1
    assert result.pnl.tolist() == [0.0, 0.0]


def test_limit_down_blocks_sell() -> None:
    stock = make_stock([10, 10, 9, 9], [10, 10, 10, 10])
    result = execute_signals(stock, np.array([1.0, 0.0]), cost=0.0)
    assert result.positions.tolist() == [1.0, 1.0]
    assert result.blocked == 1
    assert result.pnl[0] == pytest.approx(math.log(9 / 10))
    assert result.pnl[1] == pytest.approx(0.0)


def test_signals_are_causal_under_truncation() -> None:
    values = np.log(np.linspace(10, 20, 300) + np.sin(np.arange(300)))
    dif, dea = macd_lines(values, 2 / 11, 2 / 23, 2 / 9)
    truncated_dif, truncated_dea = macd_lines(values[:200], 2 / 11, 2 / 23, 2 / 9)
    assert np.allclose(dif[:200], truncated_dif)
    assert np.allclose(dea[:200], truncated_dea)
    assert np.allclose(ema_alpha(values, 0.3)[:150], ema_alpha(values[:150], 0.3))


def test_bootstrap_returns_interval_and_bounded_p_value() -> None:
    rng = np.random.default_rng(7)
    benchmark = rng.normal(0.0003, 0.01, 800)
    strategy = benchmark * 0.5 + rng.normal(0.0001, 0.004, 800)
    (lower, upper), p_value = block_bootstrap_sharpe_diff(strategy, benchmark, n_boot=200, seed=3)
    assert lower <= upper
    assert 0 <= p_value <= 1


def test_binomial_and_drawdown() -> None:
    assert binomial_one_sided_p(13, 19) == pytest.approx(0.0835, abs=1e-3)
    drawdown = max_drawdown(np.array([-0.1, 0.2, -0.3]))
    assert drawdown == pytest.approx(math.exp(-0.3) - 1, abs=1e-9)
