from app.data.baostock_source import parse_bars, parse_factors


def test_parse_bars_skips_suspended_and_missing_rows() -> None:
    rows = [
        ["2020-01-02", "10", "11", "9", "10.5", "100", "1000"],
        ["2020-01-03", "", "", "", "", "", ""],
        ["2020-01-06", "11", "12", "10", "11.5", "", ""],
        ["2020-01-07", "bad", "12", "10", "11.5", "200", "2000"],
    ]

    bars = parse_bars(rows)

    assert [bar.date for bar in bars] == ["2020-01-02", "2020-01-06"]
    assert (bars[1].volume, bars[1].amount) == (0.0, 0.0)


def test_parse_factors_skips_missing_rows() -> None:
    rows = [
        ["600000", "2020-06-01", "0.5"],
        ["600000", "", ""],
        ["600000", "2021-06-01", "bad"],
    ]

    factors = parse_factors(rows)

    assert len(factors) == 1
    assert factors[0].fore == 0.5
