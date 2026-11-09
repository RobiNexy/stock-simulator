from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import baostock as bs

from app.data.adjust import Bar, Factor


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


@contextmanager
def _session() -> Iterator[None]:
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
            return [
                Bar(
                    date=r[0],
                    open=float(r[1]),
                    high=float(r[2]),
                    low=float(r[3]),
                    close=float(r[4]),
                    volume=float(r[5]),
                    amount=float(r[6]),
                )
                for r in _rows(rs)
            ]

    def fetch_factors(self, code: str, start: str, end: str) -> list[Factor]:
        with _session():
            rs = bs.query_adjust_factor(
                to_baostock_code(code), start_date=start, end_date=end
            )
            return [
                Factor(date=r[1], fore=float(r[2])) for r in _rows(rs)
            ]
