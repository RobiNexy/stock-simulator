import json
from datetime import date
from pathlib import Path
from typing import Literal, Protocol

import pandas as pd

from app.data.adjust import Bar, Factor, apply_forward_adjust
from app.data.baostock_source import to_baostock_code

HISTORY_START = "1990-12-19"


class BarSource(Protocol):
    def fetch_daily_raw(self, code: str, start: str, end: str) -> list[Bar]: ...

    def fetch_factors(self, code: str, start: str, end: str) -> list[Factor]: ...


class DailyBarNotFound(Exception):
    pass


class DailyBarStore:
    def __init__(self, cache_dir: Path, source: BarSource) -> None:
        self._dir = cache_dir
        self._source = source

    def get_daily(
        self,
        code: str,
        start: str,
        end: str,
        adjust: Literal["none", "forward"] = "forward",
    ) -> list[Bar]:
        to_baostock_code(code)
        if start > end:
            raise ValueError("start 不能晚于 end")
        self._ensure_cache(code, end)
        bars = self._read_bars(code)
        selected = [b for b in bars if start <= b.date <= end]
        if not selected:
            raise DailyBarNotFound(f"{code} 在 {start} 至 {end} 之间没有交易数据")
        if adjust == "forward":
            return apply_forward_adjust(selected, self._read_factors(code))
        return selected

    def _ensure_cache(self, code: str, end: str) -> None:
        meta = self._read_meta(code)
        if meta is not None and end <= meta["fetched_through"]:
            return
        today = date.today().isoformat()
        bars = self._source.fetch_daily_raw(code, HISTORY_START, today)
        factors = self._source.fetch_factors(code, HISTORY_START, today)
        if not bars:
            raise DailyBarNotFound(f"{code} 没有可用的历史日线")
        self._dir.mkdir(parents=True, exist_ok=True)
        pd.DataFrame([b.__dict__ for b in bars]).to_parquet(self._bars_path(code))
        pd.DataFrame([f.__dict__ for f in factors]).to_parquet(self._factors_path(code))
        self._meta_path(code).write_text(
            json.dumps({"fetched_through": today}), encoding="utf-8"
        )

    def _read_bars(self, code: str) -> list[Bar]:
        df = pd.read_parquet(self._bars_path(code))
        return [Bar(**row) for row in df.to_dict(orient="records")]

    def _read_factors(self, code: str) -> list[Factor]:
        df = pd.read_parquet(self._factors_path(code))
        return [Factor(**row) for row in df.to_dict(orient="records")]

    def _read_meta(self, code: str) -> dict[str, str] | None:
        path = self._meta_path(code)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def _bars_path(self, code: str) -> Path:
        return self._dir / f"{code}_daily.parquet"

    def _factors_path(self, code: str) -> Path:
        return self._dir / f"{code}_factor.parquet"

    def _meta_path(self, code: str) -> Path:
        return self._dir / f"{code}_meta.json"
