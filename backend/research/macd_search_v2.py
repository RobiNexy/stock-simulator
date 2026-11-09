"""MACD parameter search, second experiment.

Fixes relative to research/macd_search.py:
- back-adjusted prices (Baostock adjustflag=1) recorded in metadata;
- signal at close t, execution at open t+1, return open t+1 -> open t+2;
- limit-up/down blocking on the execution open;
- T+1 is satisfied by construction (one decision per day);
- linear and half-life grids;
- paired block bootstrap of strategy vs buy-and-hold;
- random-timing baseline with the same exposure;
- risk metrics: annual return, drawdown, exposure, turnover;
- Deflated Sharpe on the walk-forward OOS series with the grid size as trials.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from statistics import NormalDist

import baostock as bs
import numpy as np
import pandas as pd

from app.data.baostock_source import _rows, _session, to_baostock_code

BACKEND_DIR = Path(__file__).resolve().parents[1]
OUTPUT_DIR = BACKEND_DIR / "data" / "research"

DEFAULT_CODES = ("600000", "000001", "600519", "000858", "600036")
DEFAULT_START = "2002-01-01"
DEFAULT_COST = 5e-4

LINEAR_GRID = [(fast, slow, signal) for fast in range(8, 15) for slow in range(18, 31) for signal in range(5, 13)]
HALF_LIVES = (2.0, 3.0, 4.0, 6.0, 8.0, 11.0, 16.0, 22.0, 32.0, 45.0, 64.0)
HALF_LIFE_GRID = [
    (fast, slow, signal)
    for fast in HALF_LIVES
    for slow in HALF_LIVES
    for signal in HALF_LIVES
    if fast < slow
]


def ema_alpha(values: np.ndarray, alpha: float) -> np.ndarray:
    series = pd.Series(values)
    return series.ewm(alpha=alpha, adjust=False).mean().to_numpy(dtype=float)


def alpha_from_period(period: float) -> float:
    return 2 / (period + 1)


def alpha_from_half_life(half_life: float) -> float:
    return 1 - 0.5 ** (1 / half_life)


def macd_lines(log_close: np.ndarray, fast_alpha: float, slow_alpha: float, signal_alpha: float) -> tuple[np.ndarray, np.ndarray]:
    dif = ema_alpha(log_close, fast_alpha) - ema_alpha(log_close, slow_alpha)
    return dif, ema_alpha(dif, signal_alpha)


def sharpe(pnl: np.ndarray) -> float:
    clean = pnl[~np.isnan(pnl)]
    if len(clean) < 2:
        return float("nan")
    deviation = clean.std(ddof=1)
    if deviation == 0:
        return float("nan")
    return float(clean.mean() / deviation * math.sqrt(252))


def annual_return(pnl: np.ndarray) -> float:
    clean = pnl[~np.isnan(pnl)]
    if len(clean) == 0:
        return float("nan")
    return float(math.exp(clean.mean() * 252) - 1)


def max_drawdown(pnl: np.ndarray) -> float:
    clean = pnl[~np.isnan(pnl)]
    if len(clean) == 0:
        return float("nan")
    equity = np.exp(np.cumsum(clean))
    peak = np.maximum.accumulate(equity)
    return float((equity / peak - 1).min())


def binomial_one_sided_p(successes: int, trials: int) -> float:
    if trials == 0:
        return float("nan")
    return float(sum(math.comb(trials, k) for k in range(successes, trials + 1)) / 2**trials)


def deflated_sharpe(
    observed_daily_sharpe: float,
    trial_daily_sharpes: list[float],
    observations: int,
    skew: float,
    kurtosis: float,
) -> float:
    trials = np.array([value for value in trial_daily_sharpes if math.isfinite(value)])
    if len(trials) < 2 or observations < 2 or not math.isfinite(observed_daily_sharpe):
        return float("nan")
    variance = float(trials.var(ddof=1))
    normal = NormalDist()
    gamma = 0.5772156649015329
    z1 = normal.inv_cdf(1 - 1 / len(trials))
    z2 = normal.inv_cdf(1 - 1 / (len(trials) * math.e))
    expected_max = math.sqrt(variance) * ((1 - gamma) * z1 + gamma * z2)
    denominator = math.sqrt(
        max(1e-12, 1 - skew * observed_daily_sharpe + (kurtosis - 1) / 4 * observed_daily_sharpe**2)
    )
    return float(normal.cdf((observed_daily_sharpe - expected_max) * math.sqrt(observations - 1) / denominator))


@dataclass
class StockData:
    code: str
    dates: list[str]
    open: np.ndarray
    close: np.ndarray
    log_open: np.ndarray
    log_close: np.ndarray


@dataclass
class Fold:
    train_dates: list[str]
    test_dates: list[str]


@dataclass
class StrategyResult:
    pnl: np.ndarray
    positions: np.ndarray
    turnover: np.ndarray
    blocked: int


def load_stock(code: str, start: str) -> StockData:
    with _session():
        result = bs.query_history_k_data_plus(
            to_baostock_code(code),
            "date,open,close",
            start_date=start,
            end_date=date.today().isoformat(),
            frequency="d",
            adjustflag="1",
        )
        rows = [row for row in _rows(result) if float(row[2]) > 0]
    dates = [row[0] for row in rows]
    open_prices = np.array([float(row[1]) for row in rows], dtype=float)
    close_prices = np.array([float(row[2]) for row in rows], dtype=float)
    return StockData(
        code=code,
        dates=dates,
        open=open_prices,
        close=close_prices,
        log_open=np.log(open_prices),
        log_close=np.log(close_prices),
    )


def load_index(code: str, start: str) -> StockData:
    with _session():
        result = bs.query_history_k_data_plus(
            code,
            "date,open,close",
            start_date=start,
            end_date=date.today().isoformat(),
            frequency="d",
        )
        rows = [row for row in _rows(result) if float(row[2]) > 0]
    dates = [row[0] for row in rows]
    open_prices = np.array([float(row[1]) for row in rows], dtype=float)
    close_prices = np.array([float(row[2]) for row in rows], dtype=float)
    return StockData(
        code=code,
        dates=dates,
        open=open_prices,
        close=close_prices,
        log_open=np.log(open_prices),
        log_close=np.log(close_prices),
    )


def load_data(codes: tuple[str, ...], start: str) -> dict[str, StockData]:
    data: dict[str, StockData] = {}
    for code in codes:
        stock = load_index(code, start) if "." in code else load_stock(code, start)
        data[code] = stock
        print(f"loaded {code}: {len(stock.dates)} bars, {stock.dates[0]} .. {stock.dates[-1]}", flush=True)
    return data


def limit_percent(code: str) -> float:
    if code.startswith(("300", "688")):
        return 0.20
    return 0.10


def execute_signals(stock: StockData, signals: np.ndarray, cost: float) -> StrategyResult:
    periods = len(stock.dates) - 2
    returns = stock.log_open[2:] - stock.log_open[1:-1]
    positions = np.zeros(periods, dtype=float)
    turnover = np.zeros(periods, dtype=float)
    pnl = np.zeros(periods, dtype=float)
    previous = 0.0
    blocked = 0
    limit = limit_percent(stock.code)
    is_index = "." in stock.code

    for index in range(periods):
        desired = signals[index]
        effective = desired
        if not is_index and desired != previous:
            ratio = stock.open[index + 1] / stock.close[index]
            if desired > previous and ratio >= 1 + limit - 1e-4:
                effective = previous
                blocked += 1
            elif desired < previous and ratio <= 1 - limit + 1e-4:
                effective = previous
                blocked += 1
        positions[index] = effective
        turnover[index] = abs(effective - previous)
        pnl[index] = effective * returns[index] - cost * turnover[index]
        previous = effective

    return StrategyResult(pnl=pnl, positions=positions, turnover=turnover, blocked=blocked)


def execute(
    stock: StockData,
    log_close: np.ndarray,
    fast_alpha: float,
    slow_alpha: float,
    signal_alpha: float,
    cost: float,
) -> StrategyResult:
    dif, dea = macd_lines(log_close, fast_alpha, slow_alpha, signal_alpha)
    return execute_signals(stock, (dif > dea).astype(float), cost)


def build_folds(decision_dates: list[str], train: int, test: int, step: int) -> list[Fold]:
    folds: list[Fold] = []
    start = 0
    while start + train < len(decision_dates):
        train_dates = decision_dates[start : start + train]
        test_dates = decision_dates[start + train : min(start + train + test, len(decision_dates))]
        if len(test_dates) >= 60:
            folds.append(Fold(train_dates, test_dates))
        start += step
    return folds


def slice_by_dates(values: np.ndarray, decision_dates: list[str], dates: list[str]) -> np.ndarray:
    lookup = {day: index for index, day in enumerate(decision_dates)}
    indices = [lookup[day] for day in dates if day in lookup]
    return values[indices]


def grid_alphas(param: tuple[float, float, float], mode: str) -> tuple[float, float, float]:
    if mode == "halflife":
        return tuple(alpha_from_half_life(value) for value in param)  # type: ignore[return-value]
    return tuple(alpha_from_period(value) for value in param)  # type: ignore[return-value]


def param_label(param: tuple[float, float, float], mode: str) -> str:
    if mode == "halflife":
        return ",".join(f"{value:g}" for value in param) + " (half-life)"
    return ",".join(str(int(value)) for value in param)


def run(
    codes: tuple[str, ...],
    start: str,
    cost: float,
    train: int,
    test: int,
    step: int,
    mode: str,
    grid: list[tuple[float, float, float]],
    bootstrap_draws: int = 5000,
    timing_draws: int = 2000,
    seed: int = 20261109,
) -> dict[str, object]:
    data = load_data(codes, start)
    common_start = max(stock.dates[0] for stock in data.values())
    master_dates = sorted(
        {day for stock in data.values() for day in stock.dates if day >= common_start}
    )
    decision_dates = master_dates[:-2]
    folds = build_folds(decision_dates, train, test, step)
    if not folds:
        raise RuntimeError("样本不足，无法形成 walk-forward 折")

    print(f"common start {common_start}, decision days {len(decision_dates)}, folds {len(folds)}", flush=True)

    data_end = max(stock.dates[-1] for stock in data.values())
    digest = hashlib.sha256()
    for code, stock in sorted(data.items()):
        digest.update(code.encode())
        digest.update(stock.dates[0].encode())
        digest.update(stock.dates[-1].encode())
        digest.update(stock.close.tobytes())
    fingerprint = digest.hexdigest()[:16]

    pnls = np.empty((len(grid), len(decision_dates)), dtype=float)
    positions = np.empty_like(pnls)
    turnovers = np.empty_like(pnls)
    blocked_counts: list[int] = []
    for index, param in enumerate(grid):
        fast_alpha, slow_alpha, signal_alpha = grid_alphas(param, mode)
        columns = []
        position_columns = []
        turnover_columns = []
        blocked = 0
        for stock in data.values():
            result = execute(stock, stock.log_close, fast_alpha, slow_alpha, signal_alpha, cost)
            pnl_series = pd.Series(result.pnl, index=stock.dates[:-2]).reindex(decision_dates).to_numpy(dtype=float)
            position_series = pd.Series(result.positions, index=stock.dates[:-2]).reindex(decision_dates).to_numpy(dtype=float)
            turnover_series = pd.Series(result.turnover, index=stock.dates[:-2]).reindex(decision_dates).to_numpy(dtype=float)
            columns.append(pnl_series)
            position_columns.append(position_series)
            turnover_columns.append(turnover_series)
            blocked += result.blocked
        pnls[index] = np.nanmean(np.vstack(columns), axis=0)
        positions[index] = np.nanmean(np.vstack(position_columns), axis=0)
        turnovers[index] = np.nanmean(np.vstack(turnover_columns), axis=0)
        blocked_counts.append(blocked)
    print("computed parameter grid", flush=True)

    benchmark_columns = []
    for stock in data.values():
        returns = stock.log_open[2:] - stock.log_open[1:-1]
        benchmark_columns.append(
            pd.Series(returns, index=stock.dates[:-2]).reindex(decision_dates).to_numpy(dtype=float)
        )
    benchmark = np.nanmean(np.vstack(benchmark_columns), axis=0)

    fold_records = []
    selected_pnl = []
    selected_positions = []
    selected_turnover = []
    selected_returns = []
    selected_params = []
    for fold_index, fold in enumerate(folds):
        train_scores = np.array(
            [sharpe(slice_by_dates(pnls[i], decision_dates, fold.train_dates)) for i in range(len(grid))]
        )
        best_index = int(np.nanargmax(train_scores))
        test_pnl = slice_by_dates(pnls[best_index], decision_dates, fold.test_dates)
        test_positions = slice_by_dates(positions[best_index], decision_dates, fold.test_dates)
        test_turnover = slice_by_dates(turnovers[best_index], decision_dates, fold.test_dates)
        test_returns = slice_by_dates(benchmark, decision_dates, fold.test_dates)
        selected_pnl.append(test_pnl)
        selected_positions.append(test_positions)
        selected_turnover.append(test_turnover)
        selected_returns.append(test_returns)
        selected_params.append(param_label(grid[best_index], mode))
        fold_records.append(
            {
                "fold": fold_index + 1,
                "train_start": fold.train_dates[0],
                "train_end": fold.train_dates[-1],
                "test_start": fold.test_dates[0],
                "test_end": fold.test_dates[-1],
                "selected": selected_params[-1],
                "train_sharpe": float(train_scores[best_index]),
                "test_sharpe": sharpe(test_pnl),
                "test_return": annual_return(test_pnl),
                "exposure": float(np.nanmean(test_positions)),
                "blocked_trades": int(blocked_counts[best_index]),
            }
        )
        print(
            f"fold {fold_index + 1}: {fold.test_dates[0]}..{fold.test_dates[-1]} "
            f"selected {selected_params[-1]} train {train_scores[best_index]:.2f} "
            f"test {fold_records[-1]['test_sharpe']:.2f}",
            flush=True,
        )

    walk_pnl = np.concatenate(selected_pnl)
    walk_positions = np.concatenate(selected_positions)
    walk_turnover = np.concatenate(selected_turnover)
    walk_returns = np.concatenate(selected_returns)
    walk_sharpe = sharpe(walk_pnl)
    benchmark_sharpe = sharpe(walk_returns)
    years = len(walk_pnl) / 252

    fixed_rows = []
    fixed_pnls = []
    for index, param in enumerate(grid):
        pieces = [slice_by_dates(pnls[index], decision_dates, fold.test_dates) for fold in folds]
        pooled = np.concatenate(pieces)
        fixed_pnls.append(pooled)
        fixed_rows.append(
            {
                "params": param_label(param, mode),
                "sharpe": sharpe(pooled),
                "annual_return": annual_return(pooled),
                "max_drawdown": max_drawdown(pooled),
                "exposure": float(np.nanmean(np.concatenate([slice_by_dates(positions[index], decision_dates, fold.test_dates) for fold in folds]))),
                "turnover_annual": float(np.nansum(np.concatenate([slice_by_dates(turnovers[index], decision_dates, fold.test_dates) for fold in folds])) / years),
            }
        )
    fixed_rows.sort(key=lambda row: (math.isfinite(row["sharpe"]), row["sharpe"]), reverse=True)
    best_row = fixed_rows[0]

    trial_daily = []
    for pooled in fixed_pnls:
        clean = pooled[~np.isnan(pooled)]
        trial_daily.append(float(clean.mean() / clean.std(ddof=1)) if clean.std(ddof=1) > 0 else float("nan"))

    clean_walk = walk_pnl[~np.isnan(walk_pnl)]
    daily_sharpe = float(clean_walk.mean() / clean_walk.std(ddof=1))
    dsr = deflated_sharpe(
        daily_sharpe,
        trial_daily,
        len(clean_walk),
        float(pd.Series(clean_walk).skew()),
        float(pd.Series(clean_walk).kurt() + 3),
    )

    best_pooled = fixed_pnls[grid.index(next(param for param in grid if param_label(param, mode) == best_row["params"]))]
    best_clean = best_pooled[~np.isnan(best_pooled)]
    dsr_hindsight = deflated_sharpe(
        float(best_clean.mean() / best_clean.std(ddof=1)),
        trial_daily,
        len(best_clean),
        float(pd.Series(best_clean).skew()),
        float(pd.Series(best_clean).kurt() + 3),
    )

    ci, bootstrap_p = block_bootstrap_sharpe_diff(walk_pnl, walk_returns, seed=seed, n_boot=bootstrap_draws)
    timing_p = random_timing_p(walk_pnl, selected_returns, selected_positions, cost, seed, timing_draws)

    picks = pd.Series(selected_params).value_counts()
    positive_folds = sum(1 for row in fold_records if row["test_sharpe"] > 0)

    return {
        "config": {
            "codes": list(codes),
            "start": start,
            "cost_per_side": cost,
            "train_days": train,
            "test_days": test,
            "step_days": step,
            "grid_mode": mode,
            "grid_size": len(grid),
            "adjusted": "back (Baostock adjustflag=1)" if any("." not in code for code in codes) else "not applicable (index)",
            "execution": "signal at close t, trade at open t+1, return open t+1 -> open t+2, limit-up/down blocking",
            "sample_start": common_start,
            "sample_end": decision_dates[-1],
            "data_end": data_end,
            "decision_days": len(decision_dates),
            "folds": len(folds),
            "data_fingerprint": fingerprint,
            "bootstrap_draws": bootstrap_draws,
            "timing_draws": timing_draws,
            "random_seed": seed,
        },
        "walk_forward": {
            "sharpe": walk_sharpe,
            "annual_return": annual_return(walk_pnl),
            "max_drawdown": max_drawdown(walk_pnl),
            "exposure": float(np.nanmean(walk_positions)),
            "turnover_annual": float(np.nansum(walk_turnover) / years),
            "deflated_sharpe_probability": dsr,
            "selection_counts": picks.to_dict(),
            "positive_folds": positive_folds,
            "positive_fold_p_value": binomial_one_sided_p(positive_folds, len(fold_records)),
            "bootstrap_sharpe_diff_ci95": [float(ci[0]), float(ci[1])],
            "bootstrap_p_value": bootstrap_p,
            "random_timing_p_value": timing_p,
            "folds": fold_records,
        },
        "benchmark_buy_hold": {
            "sharpe": benchmark_sharpe,
            "annual_return": annual_return(walk_returns),
            "max_drawdown": max_drawdown(walk_returns),
            "exposure": 1.0,
            "turnover_annual": 0.0,
        },
        "grid_best_fixed_oos": {
            "params": best_row["params"],
            "sharpe": best_row["sharpe"],
            "annual_return": best_row["annual_return"],
            "max_drawdown": best_row["max_drawdown"],
            "exposure": best_row["exposure"],
            "turnover_annual": best_row["turnover_annual"],
            "deflated_sharpe_probability_hindsight": dsr_hindsight,
        },
        "fixed_parameter_oos_top": fixed_rows[:10],
        "sharpe_standard_error": math.sqrt((1 + walk_sharpe**2 / 2) / years) if years > 0 else float("nan"),
    }


def block_bootstrap_sharpe_diff(
    strategy: np.ndarray, benchmark: np.ndarray, block: int = 60, n_boot: int = 5000, seed: int = 0
) -> tuple[tuple[float, float], float]:
    valid = ~np.isnan(strategy) & ~np.isnan(benchmark)
    strategy = strategy[valid]
    benchmark = benchmark[valid]
    length = len(strategy)
    if length <= block + 1:
        return (float("nan"), float("nan")), float("nan")
    rng = np.random.default_rng(seed)
    draws = np.empty(n_boot)
    blocks = int(math.ceil(length / block))
    for draw in range(n_boot):
        starts = rng.integers(0, length - block, blocks)
        indices = (starts[:, None] + np.arange(block)).ravel()[:length]
        draws[draw] = sharpe(strategy[indices]) - sharpe(benchmark[indices])
    lower, upper = np.percentile(draws, [2.5, 97.5])
    return (float(lower), float(upper)), float((draws <= 0).mean())


def random_timing_p(
    strategy_pnl: np.ndarray,
    fold_returns: list[np.ndarray],
    fold_positions: list[np.ndarray],
    cost: float,
    seed: int,
    draws: int,
) -> float:
    observed = sharpe(strategy_pnl)
    if not math.isfinite(observed):
        return float("nan")
    rng = np.random.default_rng(seed + 1)
    scores = np.empty(draws)
    for draw in range(draws):
        pieces = []
        for returns, positions in zip(fold_returns, fold_positions, strict=True):
            if len(positions) < 2:
                continue
            shift = int(rng.integers(1, len(positions)))
            shifted = np.roll(positions, shift)
            change = np.abs(np.diff(np.concatenate(([0.0], shifted))))
            pieces.append(shifted * returns - cost * change)
        scores[draw] = sharpe(np.concatenate(pieces)) if pieces else float("nan")
    return float(np.nanmean(scores >= observed))


def main() -> None:
    parser = argparse.ArgumentParser(description="MACD parameter search v2")
    parser.add_argument("--codes", nargs="+", default=list(DEFAULT_CODES))
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--cost", type=float, default=DEFAULT_COST)
    parser.add_argument("--train", type=int, default=1250)
    parser.add_argument("--test", type=int, default=250)
    parser.add_argument("--step", type=int, default=250)
    parser.add_argument("--grid", choices=["linear", "halflife"], default="linear")
    parser.add_argument("--bootstrap-draws", type=int, default=5000)
    parser.add_argument("--timing-draws", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=20261109)
    parser.add_argument("--output", default=str(OUTPUT_DIR / "macd_v2.json"))
    args = parser.parse_args()

    grid = HALF_LIFE_GRID if args.grid == "halflife" else LINEAR_GRID
    print(f"grid mode {args.grid}, size {len(grid)}", flush=True)
    report = run(
        tuple(args.codes),
        args.start,
        args.cost,
        args.train,
        args.test,
        args.step,
        args.grid,
        grid,  # type: ignore[arg-type]
        args.bootstrap_draws,
        args.timing_draws,
        args.seed,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    summary_keys = ("config", "walk_forward", "benchmark_buy_hold", "grid_best_fixed_oos", "sharpe_standard_error")
    summary = {key: report[key] for key in summary_keys}
    summary["walk_forward"] = {key: value for key, value in summary["walk_forward"].items() if key != "folds"}  # type: ignore[union-attr]
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"written to {output}")


if __name__ == "__main__":
    main()
