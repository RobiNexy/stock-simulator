from app.data.adjust import Bar, Factor, apply_forward_adjust


def bar(date: str, price: float) -> Bar:
    return Bar(date=date, open=price, high=price, low=price, close=price, volume=100.0, amount=1000.0)


def test_uses_latest_event_on_or_before_date() -> None:
    bars = [bar("2020-01-02", 10.0), bar("2020-06-01", 10.0), bar("2021-01-04", 10.0)]
    factors = [Factor("2020-06-01", 0.5), Factor("2021-01-01", 0.8)]

    out = apply_forward_adjust(bars, factors)

    assert [b.close for b in out] == [10.0, 5.0, 8.0]


def test_dates_before_first_event_use_factor_one() -> None:
    out = apply_forward_adjust([bar("2019-01-02", 10.0)], [Factor("2020-06-01", 0.5)])

    assert out[0].close == 10.0


def test_volume_and_amount_are_not_adjusted() -> None:
    out = apply_forward_adjust([bar("2021-01-04", 10.0)], [Factor("2020-06-01", 0.5)])

    assert out[0].volume == 100.0
    assert out[0].amount == 1000.0


def test_unsorted_factors_are_handled() -> None:
    bars = [bar("2021-01-04", 10.0)]
    factors = [Factor("2021-01-01", 0.8), Factor("2020-06-01", 0.5)]

    assert apply_forward_adjust(bars, factors)[0].close == 8.0
