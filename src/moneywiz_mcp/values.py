"""Input parsing and output formatting shared by all domains."""
from __future__ import annotations

import re
from datetime import date as Date, datetime, time
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN

from .native import MoneyWizError

_AMOUNT = re.compile(r"^-?\d{1,15}(\.\d{1,8})?$")


def amount(value, *, field: str = "amount", allow_zero: bool = False, allow_negative: bool = False) -> Decimal:
    """Parse a decimal amount given as string or number."""
    text = str(value).strip() if value is not None else ""
    if isinstance(value, float):
        text = repr(value)
    if not _AMOUNT.match(text):
        raise MoneyWizError(f"{field}: expected a decimal number such as \"12.50\", got {value!r}")
    try:
        result = Decimal(text)
    except InvalidOperation as exc:  # pragma: no cover - regex already guards
        raise MoneyWizError(f"{field}: invalid number {value!r}") from exc
    if result < 0 and not allow_negative:
        raise MoneyWizError(f"{field} must not be negative (direction comes from the transaction kind)")
    if result == 0 and not allow_zero:
        raise MoneyWizError(f"{field} must not be zero")
    return result


def money(value) -> str | None:
    """Format a stored double as a short decimal string (stored values are IEEE doubles)."""
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
