import json
import logging
import os
import tempfile
import threading
import time
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Literal, Protocol

import pandas as pd

from app.data.adjust import Bar, Factor, apply_forward_adjust
from app.data.baostock_source import DataSourceError, to_baostock_code

HISTORY_START = "1990-12-19"
FETCH_RETRY_SECONDS = 600
logger = logging.getLogger(__name__)


class BarSource(Protocol):
    def fetch_daily_raw(self, code: str, start: str, end: str) -> list[Bar]: ...

    def fetch_factors(self, code: str, start: str, end: str) -> list[Factor]: ...


class DailyBarNotFound(Exception):
    pass


class DailyBarStore:
    def __init__(self, cache_dir: Path, source: BarSource) -> None:
        self._dir = cache_dir
        self._source = source
        self._locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)
        self._locks_guard = threading.Lock()
        self._retry_after: dict[str, float] = {}

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

    def _lock_for(self, code: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks[code]

    def _ensure_cache(self, code: str, end: str) -> None:
        meta = self._read_meta(code)
        if meta is not None and end <= meta["fetched_through"]:
            return
        with self._lock_for(code):
            meta = self._read_meta(code)
            if meta is not None and end <= meta["fetched_through"]:
                return
            has_cache = (
                self._bars_path(code).exists() and self._factors_path(code).exists()
            )
            now = time.monotonic()
            if has_cache and self._retry_after.get(code, 0.0) > now:
                return
            today = date.today().isoformat()
            try:
                bars = self._source.fetch_daily_raw(code, HISTORY_START, today)
                factors = self._source.fetch_factors(code, HISTORY_START, today)
            except DataSourceError:
                if has_cache:
                    self._retry_after[code] = now + FETCH_RETRY_SECONDS
                    logger.warning(
                        "%s 刷新失败，继续使用截至 %s 的本地缓存",
                        code,
                        meta.get("fetched_through") if meta else "未知",
                    )
                    return
                raise
            if not bars:
                raise DailyBarNotFound(f"{code} 没有可用的历史日线")
            self._retry_after.pop(code, None)
            self._dir.mkdir(parents=True, exist_ok=True)
            self._write_parquet(pd.DataFrame([b.__dict__ for b in bars]), self._bars_path(code))
            factor_frame = (
                pd.DataFrame([f.__dict__ for f in factors])
                if factors
                else pd.DataFrame(columns=["date", "fore"])
            )
            self._write_parquet(factor_frame, self._factors_path(code))
            self._atomic_write(
                self._meta_path(code),
                json.dumps({"fetched_through": today}),
            )

    def _write_parquet(self, frame: pd.DataFrame, path: Path) -> None:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=path.name, suffix=".tmp", delete=False
        ) as handle:
            temp_path = Path(handle.name)
        try:
            frame.to_parquet(temp_path)
            os.replace(temp_path, path)
        finally:
            temp_path.unlink(missing_ok=True)

    @staticmethod
    def _atomic_write(path: Path, text: str) -> None:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=path.name, suffix=".tmp",
            mode="w", encoding="utf-8", delete=False,
        ) as handle:
            handle.write(text)
            temp_path = Path(handle.name)
        try:
            os.replace(temp_path, path)
        finally:
            temp_path.unlink(missing_ok=True)

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
