"""Input parsing and output formatting shared by all domains."""
from __future__ import annotations

import re
from datetime import date as Date, datetime, time
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN

from .native import MoneyWizError

# MoneyWiz stores amounts as IEEE doubles, which hold 15 significant digits.
SIGNIFICANT = 15


def amount(value, *, field: str = "amount") -> Decimal:
    """Parse a number given as a decimal string or a number.

    Only what is needed to hand MoneyWiz a double. Sign, size and precision
    are left to MoneyWiz, like everything it accepts from its own editors.
    """
    text = repr(value) if isinstance(value, float) else (str(value).strip() if value is not None else "")
    try:
        result = Decimal(text)
    except InvalidOperation:
        raise MoneyWizError(f'{field}: expected a number such as "12.50", got {value!r}') from None
    if not result.is_finite():
        raise MoneyWizError(f"{field}: {value!r} is not a finite number")
    return result


def stored(value) -> str | None:
    """Format one stored field (an amount, fee, price, share count or rate).

    Rounded to the 15 significant digits a double holds, which keeps every
    digit that was written and drops the noise of a single multiplication
    (10 x 25.1 = 251.00000000000003 reads as 251).
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    number = float(value)
    if number == 0:
        return "0"
    d = Decimal(repr(number))
    d = d.quantize(Decimal(1).scaleb(d.adjusted() - SIGNIFICANT + 1), rounding=ROUND_HALF_EVEN).normalize()
    return format(d, "f")


def money(value) -> str | None:
    """Format a computed value such as a balance or total.

    Sums of doubles carry absolute noise (a cleared balance of
    4.547473508864641e-13), so these are rounded to 8 decimals.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    d = Decimal(repr(float(value))).quantize(Decimal("0.00000001"), rounding=ROUND_HALF_EVEN).normalize()
    if d == 0:
        return "0"
    text = format(d, "f")
    return text


def local_tz():
    return datetime.now().astimezone().tzinfo


def parse_when(value: str, *, field: str = "date") -> datetime:
    """Accept YYYY-MM-DD (local noon, avoids day shifts) or an ISO 8601 datetime."""
    if not isinstance(value, str) or not value:
        raise MoneyWizError(f"{field}: expected YYYY-MM-DD or ISO 8601 datetime")
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return datetime.combine(Date.fromisoformat(value), time(12, 0), tzinfo=local_tz())
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MoneyWizError(f"{field}: expected YYYY-MM-DD or ISO 8601 datetime, got {value!r}") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=local_tz())


def day_start(value: str, *, field: str = "date") -> datetime:
    """The local start of a day, as MoneyWiz's editors pass dates (timelessDateFromDate:)."""
    moment = parse_when(value, field=field).astimezone(local_tz())
    return datetime.combine(moment.date(), time(0, 0), tzinfo=local_tz())


def parse_bound(value: str | None, *, end: bool = False, field: str = "date") -> datetime | None:
    """Range bound: a bare date means local midnight (end bound: the following midnight)."""
    if value is None:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value or ""):
        day = Date.fromisoformat(value)
        start = datetime.combine(day, time(0, 0), tzinfo=local_tz())
        if end:
            from datetime import timedelta
            start += timedelta(days=1)
        return start
    return parse_when(value, field=field)


def show_date(value: str | None) -> str | None:
    """Agent dates arrive as UTC ISO strings; present them in local time."""
    if value is None:
        return None
    return datetime.fromisoformat(value).astimezone(local_tz()).isoformat(timespec="seconds")


def flag(value) -> bool | None:
    return None if value is None else bool(value)


def clean(record: dict) -> dict:
    """Drop None values to keep tool output compact."""
    return {k: v for k, v in record.items() if v is not None and v != [] and v != ""}
