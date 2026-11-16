"""Input parsing and output formatting shared by all domains."""
from __future__ import annotations

import re
from datetime import date as Date, datetime, time, timedelta
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


# Local time. Every conversion goes through the system time zone for that very
# moment (a naive datetime's astimezone()), never a fixed offset: an offset
# taken today is an hour off for dates on the other side of a daylight-saving
# change, which put day starts on the previous day.

def local_time(day: Date, at: time = time(0, 0)) -> datetime:
    """A wall-clock time on a calendar day in the local zone, with that day's offset."""
    return datetime.combine(day, at).astimezone()


def today() -> Date:
    return datetime.now().astimezone().date()


def parse_when(value: str, *, field: str = "date") -> datetime:
    """Accept YYYY-MM-DD (local noon, avoids day shifts) or an ISO 8601 datetime."""
    if not isinstance(value, str) or not value:
        raise MoneyWizError(f"{field}: expected YYYY-MM-DD or ISO 8601 datetime")
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return local_time(Date.fromisoformat(value), time(12, 0))
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MoneyWizError(f"{field}: expected YYYY-MM-DD or ISO 8601 datetime, got {value!r}") from exc
    return parsed if parsed.tzinfo else parsed.astimezone()


def local_day(value: str, *, field: str = "date") -> Date:
    """The local calendar day a date or timestamp falls on."""
    return parse_when(value, field=field).astimezone().date()


def day_start(value: str, *, field: str = "date") -> datetime:
    """The local start of a day, as MoneyWiz's editors pass dates (timelessDateFromDate:)."""
    return local_time(local_day(value, field=field))


def next_day_start(value: str, *, field: str = "date") -> datetime:
    """The local start of the following day: an exclusive end, as MoneyWiz stores one."""
    return local_time(local_day(value, field=field) + timedelta(days=1))


def parse_bound(value: str | None, *, end: bool = False, field: str = "date") -> datetime | None:
    """Range bound: a bare date means local midnight (end bound: the following midnight)."""
    if value is None:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value or ""):
        return next_day_start(value, field=field) if end else day_start(value, field=field)
    return parse_when(value, field=field)


def show_date(value: str | None) -> str | None:
    """Agent dates arrive as UTC ISO strings; present them in local time."""
    if value is None:
        return None
    return datetime.fromisoformat(value).astimezone().isoformat(timespec="seconds")


def show_seconds(value) -> str | None:
    """An undecoded agent date ({"$date": unix seconds}) in local time."""
    if not isinstance(value, dict) or "$date" not in value:
        return None
    return datetime.fromtimestamp(value["$date"]).astimezone().isoformat(timespec="seconds")


def flag(value) -> bool | None:
    return None if value is None else bool(value)


def clean(record: dict) -> dict:
    """Drop None values to keep tool output compact."""
    return {k: v for k, v in record.items() if v is not None and v != [] and v != ""}


def find_one(items: list[dict], ref, what: str, keys=("id", "group_id", "name")) -> dict:
    """The one item whose id, numeric id or name is ref (a number given as text matches too)."""
    text = str(ref)
    found = [x for x in items if any(x.get(k) is not None and (x[k] == ref or str(x[k]) == text) for k in keys)]
    if len(found) > 1:
        raise MoneyWizError(f"Several {what}s match {ref!r}; use the id")
    if not found:
        raise MoneyWizError(f"{what[0].upper()}{what[1:]} not found: {ref}")
    return found[0]
