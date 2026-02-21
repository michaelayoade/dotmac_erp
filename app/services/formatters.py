"""
Shared formatting and parsing utilities for all modules.

This is the **single source of truth** for value formatting/parsing across the
entire ERP.  Every web-service, PDF generator, export service, etc. should
import from here instead of defining local helpers.

Functions are organised into three groups:

* **Parsers** – convert untrusted string input (form fields, query params,
  CSV cells) into typed Python values, returning ``None`` on failure.
* **Formatters** – convert typed Python values into human-readable strings
  for templates, PDFs, and exports.
* **Enum helpers** – safely parse / display ``Enum`` members.

Org-aware formatting
~~~~~~~~~~~~~~~~~~~~
When ``set_formatting_prefs()`` has been called for the current request (see
``app.services.formatting_context``), the formatters automatically use the
organisation's date format, number separators, and currency code.  Callers
that pass an **explicit** format parameter override the org setting.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import TypeVar, Union

from app.config import settings

logger = logging.getLogger(__name__)

# Type variable for Enum classes
E = TypeVar("E", bound=Enum)

# ---------------------------------------------------------------------------
# Sentinel defaults — used to distinguish "caller passed an explicit format"
# from "caller used the default".  We check identity (``is``) not equality.
# ---------------------------------------------------------------------------

_DEFAULT_DATE_FMT: str = "%Y-%m-%d"
_DEFAULT_DATETIME_FMT: str = "%Y-%m-%d %H:%M"

# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


def parse_date(
    value: str | None,
    fmt: str = _DEFAULT_DATE_FMT,
    *,
    format: str | None = None,
) -> date | None:
    """Parse a date string into a :class:`date`.

    Tries ``date.fromisoformat`` first (handles ``YYYY-MM-DD`` and full ISO
    datetime strings), then falls back to ``strptime`` with *fmt*.

    Returns ``None`` when *value* is empty or unparseable.
    """
    if not value or not str(value).strip():
        return None
    cleaned = str(value).strip()
    if format:
        fmt = format
    # Fast path – fromisoformat handles YYYY-MM-DD and YYYY-MM-DDTHH:MM:SS
    try:
        return date.fromisoformat(cleaned)
    except ValueError:
        pass
    # datetime strings that are not strict ISO (e.g. "2024-01-15 10:30:00")
    try:
        return datetime.fromisoformat(cleaned).date()
    except ValueError:
        pass
    # Explicit format fallback
    try:
        return datetime.strptime(cleaned, fmt).date()
    except (ValueError, TypeError):
        return None


def parse_datetime(
    value: str | None,
    fmt: str = "%Y-%m-%d %H:%M:%S",
) -> datetime | None:
    """Parse a string into a :class:`datetime`.

    Tries ``datetime.fromisoformat`` first, then *fmt*.
    """
    if not value or not str(value).strip():
        return None
    cleaned = str(value).strip()
    try:
        return datetime.fromisoformat(cleaned)
    except (ValueError, TypeError):
        pass
    try:
        return datetime.strptime(cleaned, fmt)
    except (ValueError, TypeError):
        return None


def parse_date_end_of_day(value: str | None) -> datetime | None:
    """Parse a date string and return a datetime at 23:59:59.999999.

    Useful for date-range filters where the upper bound should include the
    entire day.
    """
    d = parse_date(value)
    if d is None:
        return None
    return datetime.combine(d, time.max)


def parse_decimal(
    value: str | None,
    default: Decimal | None = None,
) -> Decimal | None:
    """Parse a string into a :class:`Decimal`.

    When an org formatting context is active, removes the org's thousand
    separator and normalises the decimal separator to ``.`` before parsing.
    Otherwise falls back to stripping commas (US-style).

    Returns *default* on failure.
    """
    if not value:
        return default
    try:
        cleaned = str(value).strip()
        # Use org-aware separator handling when context is available
        from app.services.formatting_context import get_formatting_prefs

        prefs = get_formatting_prefs()
        if prefs is not None:
            # Remove thousand separator, then normalise decimal separator
            cleaned = cleaned.replace(prefs.thousand_sep, "")
            if prefs.decimal_sep != ".":
                cleaned = cleaned.replace(prefs.decimal_sep, ".")
        else:
            # Legacy behaviour: strip commas
            cleaned = cleaned.replace(",", "")
        return Decimal(cleaned)
    except (InvalidOperation, ValueError, TypeError):
        return default


def parse_int(value: str | None) -> int | None:
    """Parse a string to ``int``, returning ``None`` on failure."""
    if not value:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def parse_bool(
    value: str | None,
    default: bool = False,
) -> bool:
    """Parse a boolean from a string (form checkbox, query param, etc.)."""
    if value is None:
        return default
    return str(value).lower() in {"1", "true", "on", "yes"}


def parse_time(value: str | None, fmt: str = "%H:%M") -> time | None:
    """Parse a time string (``HH:MM``) into a :class:`time`."""
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), fmt).time()
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Number formatting helper (private)
# ---------------------------------------------------------------------------


def _format_number_with_seps(
    value: Decimal,
    *,
    decimal_places: int = 2,
    thousand_sep: str = ",",
    decimal_sep: str = ".",
) -> str:
    """Format a Decimal with the given thousand/decimal separators.

    Uses Python's built-in ``{:,.Nf}`` (always US-style ``1,234.56``) then
    performs a 3-step replacement via a ``\\x00`` placeholder so that
    separators don't collide during substitution.
    """
    format_str = f"{{:,.{decimal_places}f}}"
    us_formatted = format_str.format(value)

    # Fast path — if separators match US defaults, nothing to replace
    if thousand_sep == "," and decimal_sep == ".":
        return us_formatted

    # 3-step swap: comma → placeholder, dot → decimal_sep, placeholder → thousand_sep
    result = us_formatted.replace(",", "\x00")
    result = result.replace(".", decimal_sep)
    result = result.replace("\x00", thousand_sep)
    return result


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------


def format_date(
    value: date | None,
    fmt: str = _DEFAULT_DATE_FMT,
    *,
    format: str | None = None,
) -> str:
    """Format a date as a string.  Returns ``""`` for ``None``.

    When the caller uses the default *fmt* and an org formatting context is
    active, the org's preferred date format is applied automatically.
    """
    if value is None:
        return ""
    try:
        if isinstance(value, datetime):
            value = value.date()
        if format:
            fmt = format
        elif fmt is _DEFAULT_DATE_FMT:
            # No explicit override — try org context
            from app.services.formatting_context import get_formatting_prefs

            prefs = get_formatting_prefs()
            if prefs is not None:
                fmt = prefs.date_strftime
        return value.strftime(fmt)
    except (ValueError, AttributeError):
        return ""


def format_date_display(value: date | None, fmt: str = "%d %b %Y") -> str:
    """Human-friendly date (e.g. ``15 Jan 2024``)."""
    return format_date(value, fmt)


def format_datetime(
    value: datetime | None,
    fmt: str = _DEFAULT_DATETIME_FMT,
) -> str:
    """Format a datetime as a string.  Returns ``""`` for ``None``.

    When the caller uses the default *fmt* and an org formatting context is
    active, the org's preferred datetime format is used.  If the org has a
    timezone configured and the datetime is tz-aware, it is converted first.
    """
    if value is None:
        return ""
    try:
        if fmt is _DEFAULT_DATETIME_FMT:
            from app.services.formatting_context import get_formatting_prefs

            prefs = get_formatting_prefs()
            if prefs is not None:
                fmt = prefs.datetime_strftime
                # Timezone conversion for tz-aware datetimes
                if (
                    prefs.timezone_name
                    and hasattr(value, "tzinfo")
                    and value.tzinfo is not None
                ):
                    try:
                        from zoneinfo import ZoneInfo

                        value = value.astimezone(ZoneInfo(prefs.timezone_name))
                    except (KeyError, ImportError):
                        pass  # unknown tz or missing tzdata — use as-is
        return value.strftime(fmt)
    except (ValueError, AttributeError):
        return ""


def format_currency(
    amount: Union[Decimal, float, int] | None,
    currency: str | None = None,
    *,
    none_value: str = "",
    show_symbol: bool = True,
    decimal_places: int = 2,
) -> str:
    """Format an amount as currency.

    When an org formatting context is active, uses the org's number separators
    and (if *currency* is ``None``) the org's currency code.

    Parameters
    ----------
    amount:
        The numeric value.  Accepts ``Decimal``, ``float``, ``int``, or ``None``.
    currency:
        Currency code.  Falls back to org pref, then
        ``settings.default_presentation_currency_code``.
    none_value:
        Returned when *amount* is ``None``.
    show_symbol:
        Whether to prefix the currency code.
    decimal_places:
        Number of decimal digits.
    """
    if amount is None:
        return none_value
    try:
        value = Decimal(str(amount))

        # Accounting notation: use absolute value, wrap result in parens
        is_negative = value < 0
        if is_negative:
            value = abs(value)

        # Determine separators and currency from context
        from app.services.formatting_context import get_formatting_prefs

        prefs = get_formatting_prefs()
        if prefs is not None:
            thousand_sep = prefs.thousand_sep
            decimal_sep = prefs.decimal_sep
            fallback_currency = prefs.currency_code
        else:
            thousand_sep = ","
            decimal_sep = "."
            fallback_currency = settings.default_presentation_currency_code

        formatted = _format_number_with_seps(
            value,
            decimal_places=decimal_places,
            thousand_sep=thousand_sep,
            decimal_sep=decimal_sep,
        )
        if show_symbol:
            currency_code = currency or fallback_currency
            formatted = f"{currency_code} {formatted}"

        if is_negative:
            return f"({formatted})"
        return formatted
    except (InvalidOperation, ValueError, TypeError):
        return none_value


def format_currency_compact(
    amount: Union[Decimal, float, int] | None,
    *,
    none_value: str = "",
    decimal_places: int = 2,
) -> str:
    """Format without currency symbol (e.g. ``1,234.56``)."""
    return format_currency(
        amount,
        none_value=none_value,
        show_symbol=False,
        decimal_places=decimal_places,
    )


def format_number(
    value: Union[Decimal, float, int] | None,
    *,
    none_value: str = "0",
    decimal_places: int = 2,
) -> str:
    """Format a number with org-aware thousand/decimal separators.

    This is the public API for number formatting without a currency symbol.
    """
    if value is None:
        return none_value
    try:
        dec_value = Decimal(str(value))

        from app.services.formatting_context import get_formatting_prefs

        prefs = get_formatting_prefs()
        if prefs is not None:
            thousand_sep = prefs.thousand_sep
            decimal_sep = prefs.decimal_sep
        else:
            thousand_sep = ","
            decimal_sep = "."

        return _format_number_with_seps(
            dec_value,
            decimal_places=decimal_places,
            thousand_sep=thousand_sep,
            decimal_sep=decimal_sep,
        )
    except (InvalidOperation, ValueError, TypeError):
        return none_value


def format_file_size(size: int | None, precision: int = 1) -> str:
    """Format file size for display (e.g. ``1.5 MB``)."""
    if size is None or size < 0:
        return "0 B"
    if size < 1024:
        return f"{size} B"
    elif size < 1024 * 1024:
        return f"{size / 1024:.{precision}f} KB"
    elif size < 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024):.{precision}f} MB"
    else:
        return f"{size / (1024 * 1024 * 1024):.{precision}f} GB"


def format_percentage(
    value: Decimal | None,
    *,
    none_value: str = "",
    decimal_places: int = 2,
    show_symbol: bool = True,
) -> str:
    """Format a decimal as a percentage (``0.15`` → ``15.00%``)."""
    if value is None:
        return none_value
    try:
        percentage = Decimal(str(value)) * 100
        format_str = f"{{:.{decimal_places}f}}"
        formatted = format_str.format(percentage)
        return f"{formatted}%" if show_symbol else formatted
    except (InvalidOperation, ValueError, TypeError):
        return none_value


def format_boolean(
    value: bool | None,
    true_text: str = "Yes",
    false_text: str = "No",
    none_text: str = "",
) -> str:
    """Format a boolean for display."""
    if value is None:
        return none_text
    return true_text if value else false_text


def truncate_text(
    text: str | None,
    max_length: int = 50,
    suffix: str = "...",
) -> str:
    """Truncate text to *max_length* characters."""
    if not text:
        return ""
    if len(text) <= max_length:
        return text
    return text[: max_length - len(suffix)] + suffix


# ---------------------------------------------------------------------------
# Enum helpers
# ---------------------------------------------------------------------------


def parse_enum_safe(
    enum_class: type[E],
    value: str | None,
    default: E | None = None,
) -> E | None:
    """Safely parse a string into an enum value.

    Tries exact match, then uppercase, then lowercase, then by name.
    """
    if not value:
        return default
    for attempt in (value, value.upper(), value.lower()):
        try:
            return enum_class(attempt)
        except (ValueError, KeyError):
            continue
    # Try by name
    try:
        return enum_class[value.upper()]
    except (KeyError, AttributeError):
        pass
    return default


def format_enum(value: Enum | None, none_value: str = "") -> str:
    """Return the enum's ``.value`` as a string."""
    if value is None:
        return none_value
    return str(value.value) if hasattr(value, "value") else str(value.name)


def format_enum_display(value: Enum | None, none_value: str = "") -> str:
    """Format ``UPPER_SNAKE_CASE`` enum value as ``Title Case``."""
    if value is None:
        return none_value
    text = str(value.value) if hasattr(value, "value") else str(value.name)
    return text.replace("_", " ").title()
