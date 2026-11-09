"""Walk-forward grid search for MACD parameters on Baostock daily data.

Method follows design/RESEARCH__macd-parameter-search.md:
log close, causal EMA, x_t = 1{DIF > DEA}, next-day return, per-side cost,
walk-forward selection, Deflated Sharpe Ratio for the selected series.
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

import baostock as bs

from app.data.baostock_source import BaostockSource, _rows, _session
from app.data.daily_bars import DailyBarStore

BACKEND_DIR = Path(__file__).resolve().parents[1]
CACHE_DIR = BACKEND_DIR / "data" / "cache"
OUTPUT_DIR = BACKEND_DIR / "data" / "research"

DEFAULT_CODES = ("600000", "000001", "600519", "000858", "600036")
DEFAULT_START = "2002-01-01"
DEFAULT_COST = 5e-4
DEFAULT_GRID = [(fast, slow, signal) for fast in range(8, 15) for slow in range(18, 31) for signal in range(5, 13)]
EXTENDED_GRID = [(fast, slow, signal) for fast in range(6, 21) for slow in range(20, 41) for signal in range(4, 17)]


def ema(values: np.ndarray, period: int) -> np.ndarray:
    return pd.Series(values).ewm(span=period, adjust=False).mean().to_numpy(dtype=float)


def macd_lines(log_close: np.ndarray, fast: int, slow: int, signal: int) -> tuple[np.ndarray, np.ndarray]:
    dif = ema(log_close, fast) - ema(log_close, slow)
    return dif, ema(dif, signal)


def net_pnl(log_close: np.ndarray, dif: np.ndarray, dea: np.ndarray, cost: float) -> tuple[np.ndarray, np.ndarray]:
    position = (dif > dea).astype(float)
    returns = np.diff(log_close)
    held = position[:-1]
    previous = np.concatenate(([0.0], held[:-1]))
    turnover = np.abs(held - previous)
    return held * returns - cost * turnover, turnover


def sharpe(pnl: np.ndarray) -> float:
    clean = pnl[~np.isnan(pnl)]
    if len(clean) < 2:
        return float("nan")
    deviation = clean.std(ddof=1)
    if deviation == 0:
        return float("nan")
    return float(clean.mean() / deviation * math.sqrt(252))


def total_return(pnl: np.ndarray) -> float:
    return float(np.nansum(pnl))


def deflated_sharpe(
    observed_sharpe: float,
    trial_sharpes: list[float],
    observations: int,
    skew: float,
    kurtosis: float,
) -> float:
    """Bailey & Lopez de Prado (2014). Sharpes here are per-day, not annualized."""
    trials = np.array([value for value in trial_sharpes if math.isfinite(value)])
    if len(trials) < 2 or observations < 2 or not math.isfinite(observed_sharpe):
        return float("nan")
    variance = float(trials.var(ddof=1))
    trials_count = len(trials)
    normal = NormalDist()
    gamma = 0.5772156649015329
    z1 = normal.inv_cdf(1 - 1 / trials_count)
    z2 = normal.inv_cdf(1 - 1 / (trials_count * math.e))
    expected_max = math.sqrt(variance) * ((1 - gamma) * z1 + gamma * z2)
    denominator = math.sqrt(
        max(1e-12, 1 - skew * observed_sharpe + (kurtosis - 1) / 4 * observed_sharpe**2)
    )
    return float(normal.cdf((observed_sharpe - expected_max) * math.sqrt(observations - 1) / denominator))


@dataclass
class StockSeries:
    code: str
    dates: list[str]
    log_close: np.ndarray


@dataclass
class Fold:
    train_dates: list[str]
    test_dates: list[str]


def load_index(code: str, start: str) -> StockSeries:
    today = date.today().isoformat()
    with _session():
        result = bs.query_history_k_data_plus(
            code, "date,close", start_date=start, end_date=today, frequency="d"
        )
        rows = _rows(result)
    dates = [row[0] for row in rows]
    closes = np.array([float(row[1]) for row in rows], dtype=float)
    return StockSeries(code=code, dates=dates, log_close=np.log(closes))


def load_prices(codes: tuple[str, ...], start: str) -> dict[str, StockSeries]:
    store = DailyBarStore(CACHE_DIR, BaostockSource())
    today = date.today().isoformat()
    series: dict[str, StockSeries] = {}
    for code in codes:
        if "." in code:
            stock = load_index(code, start)
            bars_count = len(stock.dates)
            first, last = stock.dates[0], stock.dates[-1]
        else:
            bars = store.get_daily(code, start, today, "forward")
            stock = StockSeries(
                code=code,
                dates=[bar.date for bar in bars],
                log_close=np.log(np.array([bar.close for bar in bars], dtype=float)),
            )
            bars_count = len(bars)
            first, last = bars[0].date, bars[-1].date
        series[code] = stock
        print(f"loaded {code}: {bars_count} bars, {first} .. {last}", flush=True)
    return series


def build_folds(decision_dates: list[str], train: int, test: int, step: int) -> list[Fold]:
    folds: list[Fold] = []
    start = 0
    while start + train < len(decision_dates):
        train_dates = decision_dates[start : start + train]
        test_end = min(start + train + test, len(decision_dates))
        test_dates = decision_dates[start + train : test_end]
        if len(test_dates) >= 60:
            folds.append(Fold(train_dates, test_dates))
        start += step
    return folds


def basket_pnl(
    series: dict[str, StockSeries],
    param: tuple[int, int, int],
    cost: float,
    decision_dates: list[str],
) -> np.ndarray:
    fast, slow, signal = param
    columns: list[np.ndarray] = []
    for stock in series.values():
        dif, dea = macd_lines(stock.log_close, fast, slow, signal)
        pnl, _ = net_pnl(stock.log_close, dif, dea, cost)
        aligned = pd.Series(pnl, index=stock.dates[:-1]).reindex(decision_dates).to_numpy(dtype=float)
        columns.append(aligned)
    return np.nanmean(np.vstack(columns), axis=0)


def slice_by_dates(values: np.ndarray, decision_dates: list[str], dates: list[str]) -> np.ndarray:
    lookup = {day: index for index, day in enumerate(decision_dates)}
    indices = [lookup[day] for day in dates if day in lookup]
    return values[indices]


def annualized_mean_sharpe(pnl: np.ndarray) -> float:
    clean = pnl[~np.isnan(pnl)]
    if len(clean) < 2 or clean.std(ddof=1) == 0:
        return float("nan")
    return float(clean.mean() / clean.std(ddof=1) * math.sqrt(252))


def run(
    codes: tuple[str, ...],
    start: str,
    cost: float,
    train: int,
    test: int,
    step: int,
    grid: list[tuple[int, int, int]] | None = None,
) -> dict[str, object]:
    grid = grid or DEFAULT_GRID
    series = load_prices(codes, start)
    common_start = max(stock.dates[0] for stock in series.values())
    master_dates = sorted({day for stock in series.values() for day in stock.dates if day >= common_start})
    decision_dates = master_dates[:-1]
    folds = build_folds(decision_dates, train, test, step)
    if not folds:
        raise RuntimeError("样本不足，无法形成 walk-forward 折")

    print(f"common start {common_start}, decision days {len(decision_dates)}, folds {len(folds)}", flush=True)

    pnls = np.empty((len(grid), len(decision_dates)), dtype=float)
    for index, param in enumerate(grid):
        pnls[index] = basket_pnl(series, param, cost, decision_dates)
    print("computed parameter grid", flush=True)

    fold_records: list[dict[str, object]] = []
    selected_pnl: list[np.ndarray] = []
    selected_params: list[str] = []
    for fold_index, fold in enumerate(folds):
        train_scores = np.array(
            [annualized_mean_sharpe(slice_by_dates(pnls[i], decision_dates, fold.train_dates)) for i in range(len(grid))]
        )
        best_index = int(np.nanargmax(train_scores))
        fast, slow, signal = grid[best_index]
        test_pnl = slice_by_dates(pnls[best_index], decision_dates, fold.test_dates)
        selected_pnl.append(test_pnl)
        selected_params.append(f"{fast},{slow},{signal}")
        fold_records.append(
            {
                "fold": fold_index + 1,
                "train_start": fold.train_dates[0],
                "train_end": fold.train_dates[-1],
                "test_start": fold.test_dates[0],
                "test_end": fold.test_dates[-1],
                "selected": f"{fast},{slow},{signal}",
                "train_sharpe": float(train_scores[best_index]),
                "test_sharpe": annualized_mean_sharpe(test_pnl),
            }
        )
        print(
            f"fold {fold_index + 1}: {fold.test_dates[0]}..{fold.test_dates[-1]} "
            f"selected {fast},{slow},{signal} train {train_scores[best_index]:.2f} "
            f"test {fold_records[-1]['test_sharpe']:.2f}",
            flush=True,
        )

    walk_forward_pnl = np.concatenate(selected_pnl)
    walk_forward_sr = annualized_mean_sharpe(walk_forward_pnl)

    fixed_rows: list[dict[str, object]] = []
    fixed_oos_pnl: list[np.ndarray] = []
    for index, param in enumerate(grid):
        pieces = [slice_by_dates(pnls[index], decision_dates, fold.test_dates) for fold in folds]
        pooled = np.concatenate(pieces)
        fixed_oos_pnl.append(pooled)
        fixed_rows.append(
            {
                "fast": param[0],
                "slow": param[1],
                "signal": param[2],
                "sharpe": annualized_mean_sharpe(pooled),
                "total_return": total_return(pooled),
            }
        )
    fixed_rows.sort(key=lambda row: (math.isfinite(row["sharpe"]), row["sharpe"]), reverse=True)

    def oos_pnl_for(param: tuple[int, int, int], active_cost: float) -> np.ndarray:
        values = basket_pnl(series, param, active_cost, decision_dates)
        return np.concatenate([slice_by_dates(values, decision_dates, fold.test_dates) for fold in folds])

    best_row = fixed_rows[0]
    best_param = (int(best_row["fast"]), int(best_row["slow"]), int(best_row["signal"]))

    sensitivity = {}
    for label, param in {
        "grid_best": best_param,
        "classic_12_26_9": (12, 26, 9),
        "article_10_22_8": (10, 22, 8),
    }.items():
        sensitivity[label] = {
            str(active_cost): annualized_mean_sharpe(oos_pnl_for(param, active_cost))
            for active_cost in (0.0, 5e-4, 1e-3)
        }

    benchmark_columns: list[np.ndarray] = []
    benchmark_per_stock = {}
    for code, stock in series.items():
        returns = np.diff(stock.log_close)
        aligned = pd.Series(returns, index=stock.dates[:-1]).reindex(decision_dates).to_numpy(dtype=float)
        benchmark_columns.append(aligned)
        benchmark_per_stock[code] = {
            "sharpe": annualized_mean_sharpe(aligned),
            "total_return": total_return(aligned),
        }
    benchmark_basket = np.nanmean(np.vstack(benchmark_columns), axis=0)

    per_stock = {}
    for code, stock in series.items():
        dif, dea = macd_lines(stock.log_close, best_param[0], best_param[1], best_param[2])
        pnl, turnover = net_pnl(stock.log_close, dif, dea, cost)
        aligned = pd.Series(pnl, index=stock.dates[:-1]).reindex(decision_dates).to_numpy(dtype=float)
        per_stock[code] = {
            "sharpe": annualized_mean_sharpe(aligned),
            "total_return": total_return(aligned),
            "turnover_sum": float(np.nansum(turnover)),
        }

    clean = walk_forward_pnl[~np.isnan(walk_forward_pnl)]
    daily_sr = float(clean.mean() / clean.std(ddof=1)) if clean.std(ddof=1) > 0 else float("nan")
    skew = float(pd.Series(clean).skew())
    kurtosis = float(pd.Series(clean).kurt() + 3)
    trial_daily = []
    for pooled in fixed_oos_pnl:
        trial_clean = pooled[~np.isnan(pooled)]
        trial_daily.append(
            float(trial_clean.mean() / trial_clean.std(ddof=1))
            if trial_clean.std(ddof=1) > 0
            else float("nan")
        )
    dsr = deflated_sharpe(daily_sr, trial_daily, len(clean), skew, kurtosis)

    picks = pd.Series(selected_params).value_counts()
    best_row_dict = fixed_rows[0]
    neighbourhood = [
        row
        for row in fixed_rows
        if abs(row["fast"] - best_row_dict["fast"]) <= 1
        and abs(row["slow"] - best_row_dict["slow"]) <= 2
        and abs(row["signal"] - best_row_dict["signal"]) <= 1
        and math.isfinite(row["sharpe"])
    ]
    neighbourhood_sr = float(np.mean([row["sharpe"] for row in neighbourhood])) if neighbourhood else float("nan")

    return {
        "config": {
            "codes": list(codes),
            "start": start,
            "cost_per_side": cost,
            "train_days": train,
            "test_days": test,
            "step_days": step,
            "grid_size": len(grid),
            "sample_start": common_start,
            "sample_end": decision_dates[-1],
            "decision_days": len(decision_dates),
            "folds": len(folds),
        },
        "walk_forward": {
            "selected_sharpe": walk_forward_sr,
            "selected_total_return": total_return(walk_forward_pnl),
            "per_day_sharpe": daily_sr,
            "skew": skew,
            "kurtosis": kurtosis,
            "deflated_sharpe_probability": dsr,
            "selection_counts": picks.to_dict(),
            "folds": fold_records,
        },
        "fixed_parameter_oos": fixed_rows,
        "fixed_parameter_oos_top": fixed_rows[:10],
        "grid_best": {
            "fast": best_row_dict["fast"],
            "slow": best_row_dict["slow"],
            "signal": best_row_dict["signal"],
            "sharpe": best_row_dict["sharpe"],
            "total_return": best_row_dict["total_return"],
            "neighbourhood_mean_sharpe": neighbourhood_sr,
        },
        "benchmark_buy_hold": {
            "basket_sharpe": annualized_mean_sharpe(benchmark_basket),
            "basket_total_return": total_return(benchmark_basket),
            "per_stock": benchmark_per_stock,
        },
        "per_stock_grid_best": per_stock,
        "cost_sensitivity": sensitivity,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="MACD walk-forward parameter search")
    parser.add_argument("--codes", nargs="+", default=list(DEFAULT_CODES))
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--cost", type=float, default=DEFAULT_COST)
    parser.add_argument("--train", type=int, default=1250)
    parser.add_argument("--test", type=int, default=250)
    parser.add_argument("--step", type=int, default=250)
    parser.add_argument("--extended", action="store_true", help="use the wider boundary-check grid")
    parser.add_argument("--output", default=str(OUTPUT_DIR / "macd_grid_search.json"))
    args = parser.parse_args()

    grid = EXTENDED_GRID if args.extended else DEFAULT_GRID
    print(f"grid size {len(grid)}", flush=True)
    report = run(tuple(args.codes), args.start, args.cost, args.train, args.test, args.step, grid)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("config", "walk_forward", "grid_best", "benchmark_buy_hold", "cost_sensitivity", "per_stock_grid_best")}, ensure_ascii=False, indent=2))
    print(f"written to {output}")


if __name__ == "__main__":
    main()
