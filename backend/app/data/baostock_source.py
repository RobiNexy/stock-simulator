from collections.abc import Iterator
from contextlib import contextmanager
from threading import Lock
from typing import Any

import baostock as bs

from app.data.adjust import Bar, Factor

_BAOSTOCK_LOCK = Lock()


class DataSourceError(Exception):
    pass


def to_baostock_code(code: str) -> str:
    if len(code) != 6 or not code.isdigit():
        raise ValueError(f"股票代码须为 6 位数字：{code}")
    if code.startswith("6"):
        return f"sh.{code}"
    if code.startswith(("0", "3")):
        return f"sz.{code}"
    raise ValueError(f"暂不支持该代码段：{code}")


def _rows(rs: Any) -> list[list[str]]:
    if rs.error_code != "0":
        raise DataSourceError(f"Baostock 查询失败：{rs.error_msg}")
    out: list[list[str]] = []
    while rs.next():
        out.append(rs.get_row_data())
    return out


def parse_bars(rows: list[list[str]]) -> list[Bar]:
    """跳过停牌或缺失价格的行；缺失的量额按 0 处理。"""
    bars: list[Bar] = []
    for row in rows:
        if len(row) < 7 or not row[0]:
            continue
        try:
            open_, high, low, close = (float(value) for value in row[1:5])
            volume = float(row[5]) if row[5] else 0.0
            amount = float(row[6]) if row[6] else 0.0
        except (TypeError, ValueError):
            continue
        bars.append(Bar(row[0], open_, high, low, close, volume, amount))
    return bars


def parse_factors(rows: list[list[str]]) -> list[Factor]:
    factors: list[Factor] = []
    for row in rows:
        if len(row) < 3 or not row[1] or not row[2]:
            continue
        try:
            factors.append(Factor(row[1], float(row[2])))
        except (TypeError, ValueError):
            continue
    return factors


@contextmanager
def _session() -> Iterator[None]:
    with _BAOSTOCK_LOCK:
        lg = bs.login()
        if lg.error_code != "0":
            raise DataSourceError(f"Baostock 登录失败：{lg.error_msg}")
        try:
            yield
        finally:
            bs.logout()


class BaostockSource:
    def fetch_daily_raw(self, code: str, start: str, end: str) -> list[Bar]:
        with _session():
            rs = bs.query_history_k_data_plus(
                to_baostock_code(code),
                "date,open,high,low,close,volume,amount",
                start_date=start,
                end_date=end,
                frequency="d",
                adjustflag="3",
            )
            return parse_bars(_rows(rs))

    def fetch_factors(self, code: str, start: str, end: str) -> list[Factor]:
        with _session():
            rs = bs.query_adjust_factor(
                to_baostock_code(code), start_date=start, end_date=end
            )
            return parse_factors(_rows(rs))
