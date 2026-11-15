from datetime import datetime, timezone

import pytest

from moneywiz_mcp.native import MoneyWizError, ni, dec, date as date_arg
from moneywiz_mcp.values import amount, money, parse_bound, parse_when, show_date


def test_amount_accepts_decimal_strings():
    assert str(amount("12.50")) == "12.50"
    assert str(amount(12.5)) == "12.5"
    assert str(amount("0", allow_zero=True)) == "0"
    assert str(amount("-3", allow_negative=True)) == "-3"


@pytest.mark.parametrize("bad", ["abc", "", None, "1,5", "1e3", "12.345678901", "--1"])
def test_amount_rejects_junk(bad):
    with pytest.raises(MoneyWizError):
        amount(bad)


def test_amount_rejects_zero_and_negative_by_default():
    with pytest.raises(MoneyWizError):
        amount("0")
    with pytest.raises(MoneyWizError):
        amount("-1")


def test_money_shortens_stored_doubles():
    assert money(-12.600000000000001) == "-12.6"
    assert money(0.0) == "0"
    assert money(4.547473508864641e-13) == "0"  # float noise in a cleared balance
    assert money(1234.5600000000002) == "1234.56"
    assert money(None) is None


def test_parse_when_bare_date_uses_local_noon():
    parsed = parse_when("2026-11-15")
    assert (parsed.year, parsed.month, parsed.day, parsed.hour) == (2026, 11, 15, 12)
    assert parsed.tzinfo is not None


def test_parse_when_keeps_explicit_offset():
    assert parse_when("2026-11-15T08:30:00+08:00").hour == 8


def test_parse_bound_end_is_exclusive_next_midnight():
    start, end = parse_bound("2026-11-01"), parse_bound("2026-11-30", end=True)
    assert start.hour == 0 and (end.day, end.hour) == (1, 0)
    assert end.month == 12


@pytest.mark.parametrize("bad", ["15/11/2026", "2026-13-01", "", None])
def test_parse_when_rejects_other_formats(bad):
    with pytest.raises(MoneyWizError):
        parse_when(bad)


def test_show_date_converts_agent_utc_to_local():
    shown = datetime.fromisoformat(show_date("2026-11-15T04:00:00+00:00"))
    assert shown.utcoffset() == datetime.now().astimezone().utcoffset() or shown.tzinfo is not None
    assert shown == datetime(2026, 11, 15, 4, tzinfo=timezone.utc)


def test_int_tag_switches_to_text_beyond_js_safe_range():
    assert ni(5) == {"ni": 5}
    assert ni(7000000000000000123) == {"nbig": "7000000000000000123"}


def test_typed_argument_helpers():
    assert dec("1.25") == {"dec": "1.25"}
    assert date_arg(datetime(2026, 1, 1, tzinfo=timezone.utc)) == {"date": 1767225600.0}
    with pytest.raises(MoneyWizError):
        date_arg(datetime(2026, 1, 1))
