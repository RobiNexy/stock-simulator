import math

import numpy as np
import pytest

from research.macd_search import (
    deflated_sharpe,
    ema,
    macd_lines,
    net_pnl,
    sharpe,
)


def test_ema_matches_manual_recursion() -> None:
    result = ema(np.array([1.0, 2.0, 3.0]), 2)
    assert result[0] == pytest.approx(1.0)
    assert result[1] == pytest.approx(1.6666666667)
    assert result[2] == pytest.approx(2.5555555556)


def test_macd_lines_use_ema_difference() -> None:
    values = np.log(np.array([10.0, 11.0, 12.0, 11.0, 10.0, 12.0]))
    dif, dea = macd_lines(values, 2, 4, 2)
    assert dif[0] == pytest.approx(0.0)
    assert dea[0] == pytest.approx(0.0)
    assert np.all(np.isfinite(dif))
    assert np.all(np.isfinite(dea))


def test_net_pnl_charges_only_position_changes() -> None:
    log_close = np.log(np.array([100.0, 110.0, 99.0]))
    dif = np.array([1.0, 1.0, 0.0])
    dea = np.zeros(3)
    pnl, turnover = net_pnl(log_close, dif, dea, 0.001)
    assert turnover.tolist() == [1.0, 0.0]
    assert pnl[0] == pytest.approx(math.log(1.1) - 0.001)
    assert pnl[1] == pytest.approx(math.log(0.9))


def test_sharpe_handles_flat_and_short_series() -> None:
    assert math.isnan(sharpe(np.array([0.01, 0.01, 0.01])))
    assert math.isnan(sharpe(np.array([0.01])))


def test_deflated_sharpe_is_bounded_and_rewards_larger_sharpe() -> None:
    trials = list(np.linspace(-0.05, 0.08, 50))
    low = deflated_sharpe(0.01, trials, 500, 0.0, 3.0)
    high = deflated_sharpe(0.20, trials, 500, 0.0, 3.0)
    assert 0 <= low <= 1
    assert 0 <= high <= 1
    assert high > low
