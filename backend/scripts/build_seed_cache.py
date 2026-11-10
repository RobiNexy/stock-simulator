"""批量生成镜像内置的日线种子缓存。

用法（在 backend/ 下）：
    uv run python scripts/build_seed_cache.py --pool hs300
    uv run python scripts/build_seed_cache.py --pool hs300 --refresh --limit 50
"""

import argparse
import json
import os
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

import baostock as bs
import pandas as pd

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.data.adjust import Bar, Factor
from app.data.baostock_source import (
    DataSourceError,
    _rows,
    parse_bars,
    parse_factors,
    to_baostock_code,
)

HISTORY_START = "1990-12-19"
DEFAULT_OUTPUT = BACKEND_DIR / "data" / "seed_cache"


def latest_trading_day(today: date) -> str:
    completed_through = today - timedelta(days=1)
    result = bs.query_trade_dates(
        start_date=(completed_through - timedelta(days=30)).isoformat(),
        end_date=completed_through.isoformat(),
    )
    rows = _rows(result)
    days = [
        row[0]
        for row in rows
        if len(row) > 1 and row[1] == "1" and row[0] <= completed_through.isoformat()
    ]
    if not days:
        raise DataSourceError("最近 30 天没有可用交易日")
    return max(days)


def pool_codes(pool: str, trading_day: str) -> list[str]:
    if pool == "hs300":
        result = bs.query_hs300_stocks(trading_day)
    elif pool == "sz50":
        result = bs.query_sz50_stocks(trading_day)
    elif pool == "zz500":
        result = bs.query_zz500_stocks(trading_day)
    elif pool == "all":
        result = bs.query_all_stock(trading_day)
    else:
        raise ValueError(f"未知股票池：{pool}")

    fields = result.fields
    code_index = fields.index("code") if "code" in fields else 0
    status_index = fields.index("tradeStatus") if "tradeStatus" in fields else None
    codes: list[str] = []
    for row in _rows(result):
        code = row[code_index]
        if status_index is not None and row[status_index] != "1":
            continue
        code6 = code.split(".", maxsplit=1)[-1]
        if len(code6) != 6 or not code6.isdigit():
            continue
        if not code6.startswith(("0", "3", "6")):
            continue
        codes.append(code6)
    return list(dict.fromkeys(codes))


def fetch(code: str, end: str) -> tuple[list[Bar], list[Factor]]:
    bs_code = to_baostock_code(code)
    daily = bs.query_history_k_data_plus(
        bs_code,
        "date,open,high,low,close,volume,amount",
        start_date=HISTORY_START,
        end_date=end,
        frequency="d",
        adjustflag="3",
    )
    bars = parse_bars(_rows(daily))
    factors_result = bs.query_adjust_factor(bs_code, start_date=HISTORY_START, end_date=end)
    factors = parse_factors(_rows(factors_result))
    return bars, factors


def write_atomic(path: Path, writer) -> None:  # type: ignore[no-untyped-def]
    descriptor, temp_name = tempfile.mkstemp(
        dir=path.parent, prefix=path.name, suffix=".tmp"
    )
    os.close(descriptor)
    temp_path = Path(temp_name)
    try:
        writer(temp_path)
        os.chmod(temp_path, 0o644)
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def write_stock(output: Path, code: str, bars: list[Bar], factors: list[Factor], end: str) -> None:
    bar_frame = pd.DataFrame([bar.__dict__ for bar in bars])
    factor_frame = (
        pd.DataFrame([factor.__dict__ for factor in factors])
        if factors
        else pd.DataFrame(columns=["date", "fore"])
    )
    write_atomic(output / f"{code}_daily.parquet", lambda path: bar_frame.to_parquet(path))
    write_atomic(output / f"{code}_factor.parquet", lambda path: factor_frame.to_parquet(path))
    write_atomic(
        output / f"{code}_meta.json",
        lambda path: path.write_text(
            json.dumps({"fetched_through": end}), encoding="utf-8"
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="生成日线种子缓存")
    parser.add_argument(
        "--pool", default="hs300", choices=["hs300", "sz50", "zz500", "all"]
    )
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--refresh", action="store_true", help="覆盖已有文件")
    parser.add_argument("--sleep", type=float, default=0.05, help="每只股票间隔秒数")
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    login = bs.login()
    if login.error_code != "0":
        raise DataSourceError(f"Baostock 登录失败：{login.error_msg}")

    today = date.today()
    end = today.isoformat()
    failures: list[tuple[str, str]] = []
    try:
        trading_day = latest_trading_day(today)
        codes = pool_codes(args.pool, trading_day)
        if args.limit:
            codes = codes[: args.limit]
        print(f"股票池 {args.pool}：{len(codes)} 只，缓存截至 {end}", flush=True)

        for index, code in enumerate(codes, start=1):
            files = [
                args.output / f"{code}_daily.parquet",
                args.output / f"{code}_factor.parquet",
                args.output / f"{code}_meta.json",
            ]
            if not args.refresh and all(path.exists() for path in files):
                continue
            try:
                bars, factors = fetch(code, end)
                if not bars:
                    raise DataSourceError("没有日线数据")
                write_stock(args.output, code, bars, factors, end)
            except (DataSourceError, ValueError) as exc:
                failures.append((code, str(exc)))
                print(f"[{index}/{len(codes)}] {code} 失败：{exc}", flush=True)
            else:
                if index == 1 or index % 20 == 0 or index == len(codes):
                    print(f"[{index}/{len(codes)}] {code} 已缓存", flush=True)
            time.sleep(args.sleep)
    finally:
        bs.logout()

    print(f"完成：成功 {len(codes) - len(failures)}，失败 {len(failures)}", flush=True)
    for code, reason in failures:
        print(f"  {code}: {reason}", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
