"""Numerical program for the theoretical MACD optimum.

Implements design/RESEARCH__macd-theoretical-optimum.md:
analytic trend error and false-flip rate, Monte Carlo reversal delay,
Pareto front and knee, plus sensitivity over trend duration and signal-to-noise.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from statistics import NormalDist

import numpy as np
import pandas as pd

BACKEND_DIR = Path(__file__).resolve().parents[1]
OUTPUT_DIR = BACKEND_DIR / "data" / "research"

KMAX = 4000
DOC_GRID = [
    (fast, slow, signal)
    for fast in (3, 4, 5, 6)
    for slow in (8, 10, 12, 15, 20, 26)
    for signal in (5, 7, 9, 12)
    if fast < slow
]


@dataclass
class TheoryRow:
    fast: int
    slow: int
    signal: int
    trend_error: float
    flip_rate: float
    steady_error: float
    delay: float
    delay_se: float


def kernel_c(fast: int, slow: int, size: int = KMAX) -> np.ndarray:
    a = 2 / (fast + 1)
    b = 2 / (slow + 1)
    k = np.arange(size)
    return (1 - b) ** (k + 1) - (1 - a) ** (k + 1)


def kernel_h(fast: int, slow: int, signal: int, size: int = KMAX) -> np.ndarray:
    """Exact H kernel: H_t = beta * dM_t + beta * H_{t-1}."""
    beta = (signal - 1) / (signal + 1)
    c = kernel_c(fast, slow, size)
    h = np.empty(size, dtype=float)
    h[0] = beta * c[0]
    for index in range(1, size):
        h[index] = beta * (c[index] - c[index - 1]) + beta * h[index - 1]
    return h


def trend_analytic(fast: int, slow: int, rho: float, q: float) -> tuple[float, float, float]:
    gamma = 1 - 2 * q
    c = kernel_c(fast, slow)
    k = np.arange(KMAX)
    s_eff = float(np.sum(c * gamma**k))
    variance = float(np.sum(c * c))
    t_stat = rho * s_eff / math.sqrt(variance)
    return NormalDist().cdf(-t_stat), s_eff, variance


def turn_analytic(
    fast: int, slow: int, signal: int, rho: float, q: float
) -> tuple[float, float, float]:
    gamma = 1 - 2 * q
    h = kernel_h(fast, slow, signal)
    k = np.arange(KMAX)
    rho1 = float(np.sum(h[:-1] * h[1:]) / np.sum(h * h))
    flip = math.acos(max(-1.0, min(1.0, rho1))) / math.pi
    mean_h = float(np.sum(h * gamma**k))
    rms = math.sqrt(float(np.sum(h * h)))
    steady = NormalDist().cdf(-rho * mean_h / rms) if rms > 0 else float("nan")
    return flip, steady, mean_h


def simulate(days: int, q: float, rho: float, seed: int) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    flips = rng.random(days) < q
    parity = np.cumsum(flips) % 2
    z = np.where(parity == 0, 1.0, -1.0)
    returns = rho * z + rng.standard_normal(days)
    return z, np.cumsum(returns)


def mean_delay(h: np.ndarray, z: np.ndarray, burn: int, horizon: int) -> tuple[float, int]:
    switches = np.where(z[1:] != z[:-1])[0] + 1
    switches = switches[(switches > burn) & (switches < len(z) - horizon - 1)]
    if len(switches) == 0:
        return float("nan"), 0
    indices = switches[:, None] + np.arange(horizon + 1)
    hit = (h[indices] > 0) == (z[switches] > 0)[:, None]
    first = np.where(hit.any(axis=1), hit.argmax(axis=1), horizon)
    return float(first.mean()), int(len(switches))


def delays_for_params(
    params: list[tuple[int, int, int]],
    z: np.ndarray,
    p: np.ndarray,
    burn: int,
    horizon: int,
) -> np.ndarray:
    result = np.empty(len(params), dtype=float)
    for index, (fast, slow, signal) in enumerate(params):
        m = pd.Series(p).ewm(span=fast, adjust=False).mean().to_numpy() - pd.Series(p).ewm(
            span=slow, adjust=False
        ).mean().to_numpy()
        h = m - pd.Series(m).ewm(span=signal, adjust=False).mean().to_numpy()
        delay, _ = mean_delay(h, z, burn, horizon)
        result[index] = delay
    return result


def pareto_front(rows: list[TheoryRow], columns: tuple[str, ...]) -> pd.DataFrame:
    frame = pd.DataFrame([row.__dict__ for row in rows])
    matrix = frame[list(columns)].to_numpy(dtype=float)
    dominated = np.zeros(len(matrix), dtype=bool)
    for index, point in enumerate(matrix):
        dominated[index] = np.any(
            np.all(matrix <= point, axis=1) & np.any(matrix < point, axis=1)
        )
    front = frame[~dominated].copy()
    return knee_table(front, columns)


def knee_table(front: pd.DataFrame, columns: tuple[str, ...]) -> pd.DataFrame:
    result = front.copy()
    for column in columns:
        low = result[column].min()
        high = result[column].max()
        result[f"norm_{column}"] = (result[column] - low) / (high - low + 1e-12)
    result["distance"] = np.sqrt(
        sum(result[f"norm_{column}"] ** 2 for column in columns)
    )
    return result.sort_values("distance").reset_index(drop=True)


def grid_analysis(
    params: list[tuple[int, int, int]],
    q: float,
    rho: float,
    days: int,
    seeds: list[int],
    horizon: int,
    burn: int,
) -> list[TheoryRow]:
    simulations = [simulate(days, q, rho, seed) for seed in seeds]
    delay_matrix = np.vstack(
        [delays_for_params(params, z, p, burn, horizon) for z, p in simulations]
    )
    mean_delay_values = np.nanmean(delay_matrix, axis=0)
    if len(seeds) > 1:
        delay_se = np.nanstd(delay_matrix, axis=0, ddof=1) / math.sqrt(len(seeds))
    else:
        delay_se = np.full(len(params), float("nan"))

    rows = []
    for index, (fast, slow, signal) in enumerate(params):
        trend_error, _, _ = trend_analytic(fast, slow, rho, q)
        flip, steady, _ = turn_analytic(fast, slow, signal, rho, q)
        rows.append(
            TheoryRow(
                fast=fast,
                slow=slow,
                signal=signal,
                trend_error=trend_error,
                flip_rate=flip,
                steady_error=steady,
                delay=float(mean_delay_values[index]),
                delay_se=float(delay_se[index]),
            )
        )
    return rows


def knee_per_seed(
    params: list[tuple[int, int, int]],
    q: float,
    rho: float,
    days: int,
    seed: int,
    horizon: int,
    burn: int,
) -> dict[str, object]:
    z, p = simulate(days, q, rho, seed)
    delays = delays_for_params(params, z, p, burn, horizon)
    rows = []
    for index, (fast, slow, signal) in enumerate(params):
        trend_error, _, _ = trend_analytic(fast, slow, rho, q)
        flip, steady, _ = turn_analytic(fast, slow, signal, rho, q)
        rows.append(
            TheoryRow(
                fast=fast,
                slow=slow,
                signal=signal,
                trend_error=trend_error,
                flip_rate=flip,
                steady_error=steady,
                delay=float(delays[index]),
                delay_se=float("nan"),
            )
        )
    front = pareto_front(rows, ("trend_error", "flip_rate", "delay"))
    knee = front.iloc[0]
    return {
        "seed": seed,
        "knee": {
            "fast": int(knee["fast"]),
            "slow": int(knee["slow"]),
            "signal": int(knee["signal"]),
            "trend_error": float(knee["trend_error"]),
            "flip_rate": float(knee["flip_rate"]),
            "delay": float(knee["delay"]),
        },
        "front_size": int(len(front)),
    }


def wide_analytic(q: float, rho: float) -> dict[str, object]:
    rows = []
    for fast in range(3, 9):
        for slow in range(6, 31):
            if slow <= fast:
                continue
            for signal in range(3, 16):
                trend_error, _, _ = trend_analytic(fast, slow, rho, q)
                flip, steady, _ = turn_analytic(fast, slow, signal, rho, q)
                rows.append(
                    {
                        "fast": fast,
                        "slow": slow,
                        "signal": signal,
                        "trend_error": trend_error,
                        "flip_rate": flip,
                        "steady_error": steady,
                    }
                )
    frame = pd.DataFrame(rows)
    best_trend = frame.sort_values("trend_error").head(10).to_dict(orient="records")
    best_steady = frame.sort_values("steady_error").head(10).to_dict(orient="records")
    best_flip = frame.sort_values("flip_rate").head(10).to_dict(orient="records")
    return {
        "rows": len(frame),
        "trend_error_min": float(frame["trend_error"].min()),
        "trend_error_max": float(frame["trend_error"].max()),
        "best_trend": best_trend,
        "best_steady_error": best_steady,
        "best_flip_rate": best_flip,
    }


def run(
    days: int,
    seeds: list[int],
    horizon: int,
    burn: int,
    q: float,
    rho: float,
) -> dict[str, object]:
    params = DOC_GRID
    print(f"primary grid {len(params)} params, {len(seeds)} seeds, {days} days", flush=True)
    rows = grid_analysis(params, q, rho, days, seeds, horizon, burn)
    front = pareto_front(rows, ("trend_error", "flip_rate", "delay"))
    knee = front.iloc[0]

    seed_knees = [
        knee_per_seed(params, q, rho, days, seed, horizon, burn) for seed in seeds
    ]

    sensitivity = []
    for duration in (10, 20, 40, 60):
        scenario_q = 1 / duration
        if duration == 20:
            scenario_rows = rows
        else:
            scenario_rows = grid_analysis(
                params, scenario_q, rho, days, [seeds[0]], horizon, burn
            )
        scenario_front = pareto_front(scenario_rows, ("trend_error", "flip_rate", "delay"))
        scenario_knee = scenario_front.iloc[0]
        best_trend = min(scenario_rows, key=lambda row: row.trend_error)
        sensitivity.append(
            {
                "duration": duration,
                "q": scenario_q,
                "rho": rho,
                "best_trend": {
                    "fast": best_trend.fast,
                    "slow": best_trend.slow,
                    "trend_error": best_trend.trend_error,
                },
                "knee": {
                    "fast": int(scenario_knee["fast"]),
                    "slow": int(scenario_knee["slow"]),
                    "signal": int(scenario_knee["signal"]),
                    "trend_error": float(scenario_knee["trend_error"]),
                    "flip_rate": float(scenario_knee["flip_rate"]),
                    "delay": float(scenario_knee["delay"]),
                },
            }
        )

    for rho_value in (0.02, 0.05, 0.1):
        if rho_value == rho:
            scenario_rows = rows
        else:
            scenario_rows = grid_analysis(
                params, q, rho_value, days, [seeds[0]], horizon, burn
            )
        scenario_front = pareto_front(scenario_rows, ("trend_error", "flip_rate", "delay"))
        scenario_knee = scenario_front.iloc[0]
        best_trend = min(scenario_rows, key=lambda row: row.trend_error)
        sensitivity.append(
            {
                "duration": 20,
                "q": q,
                "rho": rho_value,
                "best_trend": {
                    "fast": best_trend.fast,
                    "slow": best_trend.slow,
                    "trend_error": best_trend.trend_error,
                },
                "knee": {
                    "fast": int(scenario_knee["fast"]),
                    "slow": int(scenario_knee["slow"]),
                    "signal": int(scenario_knee["signal"]),
                    "trend_error": float(scenario_knee["trend_error"]),
                    "flip_rate": float(scenario_knee["flip_rate"]),
                    "delay": float(scenario_knee["delay"]),
                },
            }
        )

    wide = wide_analytic(q, rho)

    def serialize(row: pd.Series | TheoryRow) -> dict[str, object]:
        data = row.__dict__ if isinstance(row, TheoryRow) else row.to_dict()
        return {
            key: (float(value) if isinstance(value, (float, np.floating)) else int(value) if isinstance(value, (int, np.integer)) else value)
            for key, value in data.items()
            if key in {"fast", "slow", "signal", "trend_error", "flip_rate", "steady_error", "delay", "delay_se"}
        }

    return {
        "config": {
            "model": "symmetric two-state Markov chain; r_t = rho*z_t + N(0,1); sigma=1",
            "q": q,
            "duration": 1 / q,
            "rho": rho,
            "days": days,
            "seeds": seeds,
            "horizon": horizon,
            "burn": burn,
            "grid": len(params),
            "generated": date.today().isoformat(),
        },
        "trend_table": [
            {
                "fast": fast,
                "slow": slow,
                "trend_error": trend_analytic(fast, slow, rho, q)[0],
            }
            for fast, slow in [
                (3, 8),
                (4, 8),
                (4, 10),
                (5, 12),
                (8, 18),
                (12, 26),
                (20, 40),
            ]
        ],
        "rows": [serialize(row) for row in rows],
        "front": [
            serialize(row)
            for _, row in pareto_front(rows, ("trend_error", "flip_rate", "delay")).iterrows()
        ],
        "knee": {
            "fast": int(knee["fast"]),
            "slow": int(knee["slow"]),
            "signal": int(knee["signal"]),
            "trend_error": float(knee["trend_error"]),
            "flip_rate": float(knee["flip_rate"]),
            "steady_error": float(knee["steady_error"]),
            "delay": float(knee["delay"]),
            "distance": float(knee["distance"]),
        },
        "seed_knees": seed_knees,
        "sensitivity": sensitivity,
        "wide_analytic": wide,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="MACD theoretical optimum (section 6)")
    parser.add_argument("--days", type=int, default=1_000_000)
    parser.add_argument("--seeds", nargs="+", type=int, default=[0, 1, 2])
    parser.add_argument("--horizon", type=int, default=60)
    parser.add_argument("--burn", type=int, default=2000)
    parser.add_argument("--q", type=float, default=0.05)
    parser.add_argument("--rho", type=float, default=0.05)
    parser.add_argument("--output", default=str(OUTPUT_DIR / "macd_theory.json"))
    args = parser.parse_args()

    report = run(args.days, args.seeds, args.horizon, args.burn, args.q, args.rho)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("config", "trend_table", "knee", "seed_knees", "sensitivity")}, ensure_ascii=False, indent=2))
    print(f"written to {output}")
    front = report["front"]
    print(f"front size {len(front)}")
    for row in front[:15]:
        print(
            row["fast"], row["slow"], row["signal"],
            f"LA={row['trend_error']:.4f} flip={row['flip_rate']:.4f} delay={row['delay']:.2f} se={row['delay_se']:.3f}",
        )


if __name__ == "__main__":
    main()
