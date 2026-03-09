"""
Organization-aware formatting context using contextvars.

Provides per-request formatting preferences (date format, number separators,
timezone, currency) derived from the Organization model.  Formatters in
``app.services.formatters`` read these preferences automatically so that
700+ existing call-sites get org-aware formatting with zero signature changes.

Usage in middleware / ``base_context()``::

    from app.services.formatting_context import (
        set_formatting_prefs, clear_formatting_prefs, resolve_from_org,
    )

    prefs = resolve_from_org(organization)
    set_formatting_prefs(prefs)
    ...
    clear_formatting_prefs()   # in finally block

Usage in formatters (internal)::

    from app.services.formatting_context import get_formatting_prefs
    prefs = get_formatting_prefs()  # None when no request context
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.config import settings

if TYPE_CHECKING:
    from app.models.finance.core_org.organization import Organization

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Mapping constants — translate Organisation model values to Python equivalents
# ---------------------------------------------------------------------------

DATE_FORMAT_MAP: dict[str, str] = {
    "YYYY-MM-DD": "%Y-%m-%d",
    "DD/MM/YYYY": "%d/%m/%Y",
    "MM/DD/YYYY": "%m/%d/%Y",
    "DD-MM-YYYY": "%d-%m-%Y",
    "DD.MM.YYYY": "%d.%m.%Y",
    "DD MMM YYYY": "%d %b %Y",
}

# Maps the org-level "number_format" display string to (thousand_sep, decimal_sep)
NUMBER_FORMAT_MAP: dict[str, tuple[str, str]] = {
    "1,234.56": (",", "."),
    "1.234,56": (".", ","),
    "1 234.56": ("\u00a0", "."),  # non-breaking space
    "1 234,56": ("\u00a0", ","),
}

# ---------------------------------------------------------------------------
# Choice lists for Settings UI (single source of truth)
# ---------------------------------------------------------------------------

DATE_FORMAT_CHOICES: list[tuple[str, str]] = [
    ("YYYY-MM-DD", "2025-01-10"),
    ("DD/MM/YYYY", "10/01/2025"),
    ("MM/DD/YYYY", "01/10/2025"),
    ("DD-MM-YYYY", "10-01-2025"),
    ("DD.MM.YYYY", "10.01.2025"),
    ("DD MMM YYYY", "10 Jan 2025"),
]

NUMBER_FORMAT_CHOICES: list[tuple[str, str]] = [
    ("1,234.56", "Comma thousand, dot decimal"),
    ("1.234,56", "Dot thousand, comma decimal"),
    ("1 234.56", "Space thousand, dot decimal"),
    ("1 234,56", "Space thousand, comma decimal"),
]

COMMON_TIMEZONES: list[tuple[str, str]] = [
    ("UTC", "UTC"),
    ("America/New_York", "Eastern Time (US)"),
    ("America/Chicago", "Central Time (US)"),
    ("America/Denver", "Mountain Time (US)"),
    ("America/Los_Angeles", "Pacific Time (US)"),
    ("Europe/London", "London"),
    ("Europe/Paris", "Paris"),
    ("Europe/Berlin", "Berlin"),
    ("Asia/Tokyo", "Tokyo"),
    ("Asia/Shanghai", "Shanghai"),
    ("Asia/Singapore", "Singapore"),
    ("Australia/Sydney", "Sydney"),
    ("Africa/Lagos", "Lagos"),
    ("Africa/Johannesburg", "Johannesburg"),
]


# ---------------------------------------------------------------------------
# Frozen dataclass — holds resolved formatting preferences for one request
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OrgFormattingPrefs:
    """Per-request formatting preferences resolved from an Organization."""

    date_strftime: str = "%Y-%m-%d"
    datetime_strftime: str = "%Y-%m-%d %H:%M"
    thousand_sep: str = ","
    decimal_sep: str = "."
    decimal_places: int = 2
    currency_code: str = settings.default_presentation_currency_code
    timezone_name: str | None = None


# ---------------------------------------------------------------------------
# Context variable — one per request/task
# ---------------------------------------------------------------------------

_formatting_prefs: ContextVar[OrgFormattingPrefs | None] = ContextVar(
    "formatting_prefs",
    default=None,
)


def set_formatting_prefs(prefs: OrgFormattingPrefs) -> None:
    """Set formatting preferences for the current request context."""
    _formatting_prefs.set(prefs)


def get_formatting_prefs() -> OrgFormattingPrefs | None:
    """Return the current formatting preferences, or ``None`` outside a request."""
    return _formatting_prefs.get()


def clear_formatting_prefs() -> None:
    """Reset the context variable (call in middleware ``finally``)."""
    _formatting_prefs.set(None)


# ---------------------------------------------------------------------------
# Builder — derives prefs from an Organization model instance
# ---------------------------------------------------------------------------


def resolve_from_org(org: Organization | None) -> OrgFormattingPrefs:
    """Build :class:`OrgFormattingPrefs` from an :class:`Organization`.

    Falls back to sensible defaults when fields are ``None`` or unrecognised.
    """
    if org is None:
        return OrgFormattingPrefs()

    # Date format
    date_strftime = DATE_FORMAT_MAP.get(org.date_format or "", "%Y-%m-%d")
    # Derive datetime from date by appending time portion
    datetime_strftime = f"{date_strftime} %H:%M"

    # Number format
    thousand_sep, decimal_sep = NUMBER_FORMAT_MAP.get(
        org.number_format or "", (",", ".")
    )

    # Currency code — prefer presentation currency, then functional
    currency_code = (
        getattr(org, "presentation_currency_code", None)
        or getattr(org, "functional_currency_code", None)
        or settings.default_presentation_currency_code
    )

    return OrgFormattingPrefs(
        date_strftime=date_strftime,
        datetime_strftime=datetime_strftime,
        thousand_sep=thousand_sep,
        decimal_sep=decimal_sep,
        decimal_places=2,
        currency_code=currency_code,
        timezone_name=org.timezone or None,
    )
