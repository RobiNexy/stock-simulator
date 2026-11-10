from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import date
from uuid import uuid4

from app.data.adjust import Bar
from app.data.baostock_source import to_baostock_code
from app.data.daily_bars import DailyBarNotFound, DailyBarStore
from app.db import Database


@dataclass(frozen=True)
class IndexComponent:
    code: str
    weight: float


@dataclass(frozen=True)
class CustomIndex:
    id: str
    name: str
    base_date: str
    base_point: float
    components: tuple[IndexComponent, ...]


class CustomIndexNotFound(Exception):
    pass


class CustomIndexStore:
    def __init__(self, database: Database, bars: DailyBarStore) -> None:
        self._database = database
        self._bars = bars
        self._computed: dict[tuple[str, str], tuple[str, list[Bar]]] = {}

    def create(
        self,
        user_id: str,
        name: str,
        codes: list[str],
        weights: list[float] | None = None,
    ) -> CustomIndex:
        clean_name = name.strip()
        if not clean_name or len(clean_name) > 40:
            raise ValueError("指数名称不能为空且不能超过 40 个字符")
        unique_codes = list(dict.fromkeys(code.strip() for code in codes))
        if len(unique_codes) != len(codes):
            raise ValueError("成分股代码不能重复")
        if not 1 <= len(unique_codes) <= 100:
            raise ValueError("成分股数量须为 1 至 100")
        for code in unique_codes:
            to_baostock_code(code)

        if weights is None:
            normalized = [1 / len(unique_codes)] * len(unique_codes)
        else:
            if len(weights) != len(unique_codes):
                raise ValueError("权重数量必须与成分股数量相同")
            if any(weight <= 0 for weight in weights):
                raise ValueError("权重必须为正数")
            total = sum(weights)
            normalized = [weight / total for weight in weights]

        history: dict[str, list[Bar]] = {}
        invalid: list[str] = []
        for code in unique_codes:
            try:
                history[code] = self._bars.get_daily(
                    code, "1990-12-19", date.today().isoformat(), "forward"
                )
            except (DailyBarNotFound, ValueError):
                invalid.append(code)
        if invalid:
            raise ValueError(f"以下成分股没有可用日线：{', '.join(invalid)}")

        date_sets = [{bar.date for bar in history[code]} for code in unique_codes]
        common_dates = set.intersection(*date_sets)
        if not common_dates:
            raise ValueError("成分股没有共同交易日，无法确定基日")
        base_date = min(common_dates)
        index = CustomIndex(
            id=uuid4().hex,
            name=clean_name,
            base_date=base_date,
            base_point=1000.0,
            components=tuple(
                IndexComponent(code, weight)
                for code, weight in zip(unique_codes, normalized, strict=True)
            ),
        )
        with self._database.connect() as connection:
            connection.execute(
                "INSERT INTO custom_indices"
                " (id, user_id, name, base_date, base_point, components, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, datetime('now'))",
                (
                    index.id,
                    user_id,
                    index.name,
                    index.base_date,
                    index.base_point,
                    json.dumps(
                        [asdict(component) for component in index.components],
                        ensure_ascii=False,
                    ),
                ),
            )
        return index

    def list(self, user_id: str) -> list[CustomIndex]:
        with self._database.connect() as connection:
            rows = connection.execute(
                "SELECT id, name, base_date, base_point, components"
                " FROM custom_indices WHERE user_id = ?"
                " ORDER BY created_at, name",
                (user_id,),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def get(self, user_id: str, index_id: str) -> CustomIndex:
        if not re.fullmatch(r"[0-9a-f]{32}", index_id):
            raise CustomIndexNotFound(index_id)
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT id, name, base_date, base_point, components"
                " FROM custom_indices WHERE user_id = ? AND id = ?",
                (user_id, index_id),
            ).fetchone()
        if row is None:
            raise CustomIndexNotFound(index_id)
        return self._from_row(row)

    def daily_bars(self, user_id: str, index_id: str, start: str, end: str) -> list[Bar]:
        if start > end:
            raise ValueError("start 不能晚于 end")
        index = self.get(user_id, index_id)
        cache_key = (user_id, index_id)
        cached = self._computed.get(cache_key)
        if cached is not None and cached[0] >= end:
            selected = [bar for bar in cached[1] if start <= bar.date <= end]
            if not selected:
                raise DailyBarNotFound(f"自定义指数在 {start} 至 {end} 之间没有日线")
            return selected
        component_history = {
            component.code: self._bars.get_daily(
                component.code, index.base_date, end, "forward"
            )
            for component in index.components
        }
        base_closes: dict[str, float] = {}
        mapped: dict[str, dict[str, Bar]] = {}
        all_dates: set[str] = set()
        for component in index.components:
            records = component_history[component.code]
            by_date = {bar.date: bar for bar in records}
            base = by_date.get(index.base_date)
            if base is None or base.close <= 0:
                raise ValueError(f"{component.code} 缺少基日收盘价")
            base_closes[component.code] = base.close
            mapped[component.code] = by_date
            all_dates.update(date for date in by_date if index.base_date <= date <= end)

        last_close = dict(base_closes)
        result: list[Bar] = []
        for day in sorted(all_dates):
            open_value = high_value = low_value = close_value = 0.0
            volume = amount = 0.0
            for component in index.components:
                record = mapped[component.code].get(day)
                base = base_closes[component.code]
                if record is None:
                    ratio_open = ratio_high = ratio_low = ratio_close = (
                        last_close[component.code] / base
                    )
                else:
                    ratio_open = record.open / base
                    ratio_high = record.high / base
                    ratio_low = record.low / base
                    ratio_close = record.close / base
                    last_close[component.code] = record.close
                    volume += record.volume
                    amount += record.amount
                factor = index.base_point * component.weight
                open_value += factor * ratio_open
                high_value += factor * ratio_high
                low_value += factor * ratio_low
                close_value += factor * ratio_close
            result.append(
                Bar(
                    date=day,
                    open=open_value,
                    high=high_value,
                    low=low_value,
                    close=close_value,
                    volume=volume,
                    amount=amount,
                )
            )
        if not result:
            raise DailyBarNotFound(f"自定义指数在 {start} 至 {end} 之间没有日线")
        self._computed[cache_key] = (end, result)
        selected = [bar for bar in result if start <= bar.date <= end]
        if not selected:
            raise DailyBarNotFound(f"自定义指数在 {start} 至 {end} 之间没有日线")
        return selected

    @staticmethod
    def _from_row(row: object) -> CustomIndex:
        payload = dict(row)  # type: ignore[arg-type]
        return CustomIndex(
            id=payload["id"],
            name=payload["name"],
            base_date=payload["base_date"],
            base_point=payload["base_point"],
            components=tuple(
                IndexComponent(**item) for item in json.loads(payload["components"])
            ),
        )
