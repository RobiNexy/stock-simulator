from bisect import bisect_right
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Bar:
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float


@dataclass(frozen=True)
class Factor:
    date: str
    fore: float


def apply_forward_adjust(bars: list[Bar], factors: list[Factor]) -> list[Bar]:
    events = sorted(factors, key=lambda f: f.date)
    event_dates = [f.date for f in events]
    adjusted: list[Bar] = []
    for bar in bars:
        index = bisect_right(event_dates, bar.date) - 1
        k = events[index].fore if index >= 0 else 1.0
        adjusted.append(
            replace(
                bar,
                open=bar.open * k,
                high=bar.high * k,
                low=bar.low * k,
                close=bar.close * k,
            )
        )
    return adjusted
