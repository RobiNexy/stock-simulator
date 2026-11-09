from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from uuid import uuid4

from app.data.adjust import Bar
from app.data.baostock_source import to_baostock_code
from app.data.daily_bars import DailyBarNotFound, DailyBarStore


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
    def __init__(self, directory: Path, bars: DailyBarStore) -> None:
        self._directory = directory
        self._bars = bars
        self._computed: dict[str, tuple[str, list[Bar]]] = {}

    def create(
        self,
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
        self._write(index)
        return index

    def list(self) -> list[CustomIndex]:
        self._directory.mkdir(parents=True, exist_ok=True)
        return [self._read(path) for path in sorted(self._directory.glob("*.json"))]

    def get(self, index_id: str) -> CustomIndex:
        if not re.fullmatch(r"[0-9a-f]{32}", index_id):
            raise CustomIndexNotFound(index_id)
        path = self._path(index_id)
        if not path.exists():
            raise CustomIndexNotFound(index_id)
        return self._read(path)

    def daily_bars(self, index_id: str, start: str, end: str) -> list[Bar]:
        if start > end:
            raise ValueError("start 不能晚于 end")
        index = self.get(index_id)
        cached = self._computed.get(index_id)
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
        self._computed[index_id] = (end, result)
        selected = [bar for bar in result if start <= bar.date <= end]
        if not selected:
            raise DailyBarNotFound(f"自定义指数在 {start} 至 {end} 之间没有日线")
        return selected

    def _write(self, index: CustomIndex) -> None:
        self._directory.mkdir(parents=True, exist_ok=True)
        payload = {
            "id": index.id,
            "name": index.name,
            "base_date": index.base_date,
            "base_point": index.base_point,
            "components": [asdict(component) for component in index.components],
        }
        self._path(index.id).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @staticmethod
    def _read(path: Path) -> CustomIndex:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return CustomIndex(
            id=payload["id"],
            name=payload["name"],
            base_date=payload["base_date"],
            base_point=payload["base_point"],
            components=tuple(IndexComponent(**item) for item in payload["components"]),
        )

    def _path(self, index_id: str) -> Path:
        return self._directory / f"{index_id}.json"
