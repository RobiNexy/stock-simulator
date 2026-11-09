import math

import numpy as np
import pytest

from research.macd_theory import (
    TheoryRow,
    kernel_c,
    kernel_h,
    mean_delay,
    pareto_front,
    simulate,
    trend_analytic,
    turn_analytic,
)


def test_impulse_response_moments() -> None:
    for fast, slow in [(3, 8), (5, 12), (12, 26)]:
        c = kernel_c(fast, slow)
        k = np.arange(len(c))
        assert c.sum() == pytest.approx((slow - fast) / 2)
        assert (c * k).sum() / c.sum() == pytest.approx((fast + slow) / 2 - 1)


def test_h_kernel_sums_to_zero() -> None:
    for fast, slow, signal in [(3, 8, 5), (5, 12, 9), (12, 26, 9)]:
        h = kernel_h(fast, slow, signal)
        assert h.sum() == pytest.approx(0.0, abs=1e-9)


def test_trend_error_matches_theory_table() -> None:
    assert trend_analytic(3, 8, 0.05, 0.05)[0] == pytest.approx(0.4564, abs=1e-4)
    assert trend_analytic(5, 12, 0.05, 0.05)[0] == pytest.approx(0.4562, abs=1e-4)
    assert trend_analytic(12, 26, 0.05, 0.05)[0] == pytest.approx(0.4648, abs=1e-4)
    assert trend_analytic(20, 40, 0.05, 0.05)[0] == pytest.approx(0.4732, abs=1e-4)


def test_turn_metrics_are_probabilities() -> None:
    flip, steady, mean_h = turn_analytic(5, 12, 9, 0.05, 0.05)
    assert 0 <= flip <= 1
    assert 0 <= steady <= 1
    assert math.isfinite(mean_h)


def test_simulation_matches_chain_statistics() -> None:
    z, p = simulate(200_000, 0.05, 0.05, seed=11)
    flips = np.mean(z[1:] != z[:-1])
    assert flips == pytest.approx(0.05, abs=0.002)
    assert abs(z.mean()) < 0.01
    assert len(p) == len(z)


def test_mean_delay_is_zero_for_perfect_signal() -> None:
    z, _ = simulate(20_000, 0.05, 0.05, seed=3)
    delay, switches = mean_delay(z, z, burn=100, horizon=30)
    assert switches > 500
    assert delay == 0.0


def test_pareto_front_excludes_dominated_points() -> None:
    rows = [
        TheoryRow(3, 8, 5, 0.46, 0.20, 0.40, 5.0, 0.1),
        TheoryRow(4, 9, 5, 0.46, 0.18, 0.40, 7.0, 0.1),
        TheoryRow(5, 10, 5, 0.46, 0.22, 0.40, 4.0, 0.1),
        TheoryRow(6, 11, 5, 0.47, 0.25, 0.40, 8.0, 0.1),
    ]
    front = pareto_front(rows, ("trend_error", "flip_rate", "delay"))
    assert len(front) == 3
    assert 6 not in front["fast"].tolist()
    assert front.iloc[0]["distance"] <= 1.0
