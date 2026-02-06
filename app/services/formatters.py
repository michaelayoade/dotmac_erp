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
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Optional, Type, TypeVar

from app.config import settings

logger = logging.getLogger(__name__)

# Type variable for Enum classes
E = TypeVar("E", bound=Enum)

# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


def parse_date(
    value: Optional[str],
    fmt: str = "%Y-%m-%d",
) -> Optional[date]:
    """Parse a date string into a :class:`date`.

    Tries ``date.fromisoformat`` first (handles ``YYYY-MM-DD`` and full ISO
    datetime strings), then falls back to ``strptime`` with *fmt*.

    Returns ``None`` when *value* is empty or unparseable.
    """
    if not value or not str(value).strip():
        return None
    cleaned = str(value).strip()
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
    value: Optional[str],
    fmt: str = "%Y-%m-%d %H:%M:%S",
) -> Optional[datetime]:
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


def parse_date_end_of_day(value: Optional[str]) -> Optional[datetime]:
    """Parse a date string and return a datetime at 23:59:59.999999.

    Useful for date-range filters where the upper bound should include the
    entire day.
    """
    d = parse_date(value)
    if d is None:
        return None
    return datetime.combine(d, time.max)


def parse_decimal(
    value: Optional[str],
    default: Optional[Decimal] = None,
) -> Optional[Decimal]:
    """Parse a string into a :class:`Decimal`.

    Strips commas and whitespace before conversion.  Returns *default* on
    failure.
    """
    if not value:
        return default
    try:
        cleaned = str(value).replace(",", "").strip()
        return Decimal(cleaned)
    except (InvalidOperation, ValueError, TypeError):
        return default


def parse_int(value: Optional[str]) -> Optional[int]:
    """Parse a string to ``int``, returning ``None`` on failure."""
    if not value:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def parse_bool(
    value: Optional[str],
    default: bool = False,
) -> bool:
    """Parse a boolean from a string (form checkbox, query param, etc.)."""
    if value is None:
        return default
    return str(value).lower() in {"1", "true", "on", "yes"}


def parse_time(value: Optional[str], fmt: str = "%H:%M") -> Optional[time]:
    """Parse a time string (``HH:MM``) into a :class:`time`."""
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), fmt).time()
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------


def format_date(value: Optional[date], fmt: str = "%Y-%m-%d") -> str:
    """Format a date as a string.  Returns ``""`` for ``None``."""
    if value is None:
        return ""
    try:
        if isinstance(value, datetime):
            value = value.date()
        return value.strftime(fmt)
    except (ValueError, AttributeError):
        return ""


def format_date_display(value: Optional[date], fmt: str = "%d %b %Y") -> str:
    """Human-friendly date (e.g. ``15 Jan 2024``)."""
    return format_date(value, fmt)


def format_datetime(
    value: Optional[datetime],
    fmt: str = "%Y-%m-%d %H:%M",
) -> str:
    """Format a datetime as a string.  Returns ``""`` for ``None``."""
    if value is None:
        return ""
    try:
        return value.strftime(fmt)
    except (ValueError, AttributeError):
        return ""


def format_currency(
    amount: Optional[Decimal],
    currency: Optional[str] = None,
    *,
    none_value: str = "",
    show_symbol: bool = True,
    decimal_places: int = 2,
) -> str:
    """Format an amount as currency.

    Parameters
    ----------
    amount:
        The numeric value.  Accepts ``Decimal``, ``float``, ``int``, or ``None``.
    currency:
        Currency code (default: ``settings.default_presentation_currency_code``).
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
        format_str = f"{{:,.{decimal_places}f}}"
        formatted = format_str.format(value)
        if show_symbol:
            currency_code = currency or settings.default_presentation_currency_code
            return f"{currency_code} {formatted}"
        return formatted
    except (InvalidOperation, ValueError, TypeError):
        return none_value


def format_currency_compact(
    amount: Optional[Decimal],
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


def format_file_size(size: Optional[int], precision: int = 1) -> str:
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
    value: Optional[Decimal],
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
    value: Optional[bool],
    true_text: str = "Yes",
    false_text: str = "No",
    none_text: str = "",
) -> str:
    """Format a boolean for display."""
    if value is None:
        return none_text
    return true_text if value else false_text


def truncate_text(
    text: Optional[str],
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
    enum_class: Type[E],
    value: Optional[str],
    default: Optional[E] = None,
) -> Optional[E]:
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


def format_enum(value: Optional[Enum], none_value: str = "") -> str:
    """Return the enum's ``.value`` as a string."""
    if value is None:
        return none_value
    return str(value.value) if hasattr(value, "value") else str(value.name)


def format_enum_display(value: Optional[Enum], none_value: str = "") -> str:
    """Format ``UPPER_SNAKE_CASE`` enum value as ``Title Case``."""
    if value is None:
        return none_value
    text = str(value.value) if hasattr(value, "value") else str(value.name)
    return text.replace("_", " ").title()
