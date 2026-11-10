from pathlib import Path

import pytest

from app.data.adjust import Bar, Factor
from app.data.baostock_source import DataSourceError
from app.data.daily_bars import DailyBarNotFound, DailyBarStore
from app.data.seed_cache import seed_cache


class FakeSource:
    def __init__(self, bars: list[Bar] | None = None) -> None:
        self.bars = bars if bars is not None else [
            Bar("2020-01-02", 10.0, 10.0, 10.0, 10.0, 100.0, 1000.0),
            Bar("2020-06-01", 10.0, 10.0, 10.0, 10.0, 200.0, 2000.0),
        ]
        self.factors = [Factor("2020-06-01", 0.5)]
        self.calls = 0

    def fetch_daily_raw(self, code: str, start: str, end: str) -> list[Bar]:
        self.calls += 1
        return list(self.bars)

    def fetch_factors(self, code: str, start: str, end: str) -> list[Factor]:
        return list(self.factors)


def test_first_request_fetches_and_later_requests_use_cache(tmp_path: Path) -> None:
    source = FakeSource()
    store = DailyBarStore(tmp_path, source)

    first = store.get_daily("600000", "2020-01-01", "2020-12-31", "none")
    second = store.get_daily("600000", "2020-01-01", "2020-12-31", "none")

    assert source.calls == 1
    assert first == second
    assert len(first) == 2


def test_forward_adjust_applies_factors_from_cache(tmp_path: Path) -> None:
    store = DailyBarStore(tmp_path, FakeSource())

    bars = store.get_daily("600000", "2020-01-01", "2020-12-31", "forward")

    assert [b.close for b in bars] == [10.0, 5.0]


def test_range_filter_is_inclusive(tmp_path: Path) -> None:
    store = DailyBarStore(tmp_path, FakeSource())

    bars = store.get_daily("600000", "2020-06-01", "2020-06-01", "none")

    assert [b.date for b in bars] == ["2020-06-01"]


def test_empty_range_raises_not_found(tmp_path: Path) -> None:
    store = DailyBarStore(tmp_path, FakeSource())

    with pytest.raises(DailyBarNotFound):
        store.get_daily("600000", "2030-01-01", "2030-12-31", "none")


def test_unknown_code_without_history_raises_not_found(tmp_path: Path) -> None:
    store = DailyBarStore(tmp_path, FakeSource(bars=[]))

    with pytest.raises(DailyBarNotFound):
        store.get_daily("600000", "2020-01-01", "2020-12-31", "none")


def test_end_after_fetch_date_triggers_refetch(tmp_path: Path) -> None:
    source = FakeSource()
    store = DailyBarStore(tmp_path, source)
    store.get_daily("600000", "2020-01-01", "2020-12-31", "none")
    (tmp_path / "600000_meta.json").write_text('{"fetched_through": "2000-01-01"}', encoding="utf-8")

    store.get_daily("600000", "2020-01-01", "2020-12-31", "none")

    assert source.calls == 2


def test_invalid_code_and_reversed_range_are_rejected(tmp_path: Path) -> None:
    store = DailyBarStore(tmp_path, FakeSource())

    with pytest.raises(ValueError):
        store.get_daily("abc", "2020-01-01", "2020-12-31")
    with pytest.raises(ValueError):
        store.get_daily("600000", "2021-01-01", "2020-12-31")


class FailingSource:
    def __init__(self) -> None:
        self.calls = 0

    def fetch_daily_raw(self, code: str, start: str, end: str) -> list[Bar]:
        self.calls += 1
        raise DataSourceError("离线")

    def fetch_factors(self, code: str, start: str, end: str) -> list[Factor]:
        raise DataSourceError("离线")


def test_stale_cache_survives_data_source_failure(tmp_path: Path) -> None:
    store = DailyBarStore(tmp_path, FakeSource())
    store.get_daily("600000", "2020-01-01", "2020-12-31", "none")
    (tmp_path / "600000_meta.json").write_text(
        '{"fetched_through": "2000-01-01"}', encoding="utf-8"
    )

    fallback = DailyBarStore(tmp_path, FailingSource())
    bars = fallback.get_daily("600000", "2020-01-01", "2020-12-31", "none")

    assert [bar.date for bar in bars] == ["2020-01-02", "2020-06-01"]


def test_failed_refresh_is_not_retried_immediately(tmp_path: Path) -> None:
    store = DailyBarStore(tmp_path, FakeSource())
    store.get_daily("600000", "2020-01-01", "2020-12-31", "none")
    (tmp_path / "600000_meta.json").write_text(
        '{"fetched_through": "2000-01-01"}', encoding="utf-8"
    )
    source = FailingSource()
    fallback = DailyBarStore(tmp_path, source)

    fallback.get_daily("600000", "2020-01-01", "2020-12-31", "none")
    fallback.get_daily("600000", "2020-01-01", "2020-12-31", "none")

    assert source.calls == 1


def test_seed_cache_copies_only_missing_files(tmp_path: Path) -> None:
    seed_dir = tmp_path / "seed"
    cache_dir = tmp_path / "cache"
    seed_dir.mkdir()
    cache_dir.mkdir()
    (seed_dir / "600000_daily.parquet").write_bytes(b"seed")
    (seed_dir / "600000_meta.json").write_text('{"fetched_through": "2020-01-01"}')
    (cache_dir / "600000_meta.json").write_text('{"fetched_through": "2024-01-01"}')

    copied = seed_cache(seed_dir, cache_dir)

    assert copied == 1
    assert (cache_dir / "600000_daily.parquet").read_bytes() == b"seed"
    assert "2024" in (cache_dir / "600000_meta.json").read_text()
