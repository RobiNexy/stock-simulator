import baostock as bs
import pytest

from app.data.adjust import Bar, apply_forward_adjust
from app.data.baostock_source import BaostockSource


def _rows(rs: object) -> list[list[str]]:
    out: list[list[str]] = []
    while rs.error_code == "0" and rs.next():  # type: ignore[attr-defined]
        out.append(rs.get_row_data())  # type: ignore[attr-defined]
    return out


@pytest.mark.network
def test_forward_adjust_matches_baostock_adjustflag_2() -> None:
    source = BaostockSource()
    raw = source.fetch_daily_raw("600000", "2010-01-01", "2024-12-31")
    factors = source.fetch_factors("600000", "1990-12-19", "2024-12-31")

    adjusted = apply_forward_adjust(raw, factors)

    bs.login()
    try:
        expected = _rows(
            bs.query_history_k_data_plus(
                "sh.600000",
                "date,open,high,low,close,volume,amount",
                start_date="2010-01-01",
                end_date="2024-12-31",
                frequency="d",
                adjustflag="2",
            )
        )
    finally:
        bs.logout()

    assert len(adjusted) == len(expected)
    for got, row in zip(adjusted, expected, strict=True):
        assert got.date == row[0]
        assert got.open == pytest.approx(float(row[1]), abs=1e-6)
        assert got.high == pytest.approx(float(row[2]), abs=1e-6)
        assert got.low == pytest.approx(float(row[3]), abs=1e-6)
        assert got.close == pytest.approx(float(row[4]), abs=1e-6)
        assert got.volume == pytest.approx(float(row[5]), abs=1e-6)
        assert got.amount == pytest.approx(float(row[6]), abs=1e-2)
