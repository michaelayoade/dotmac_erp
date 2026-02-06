"""
Common formatting utilities for IFRS services.

**DEPRECATED** — All implementations have moved to :mod:`app.services.formatters`.
This module re-exports everything for backward compatibility.  New code should
import directly from ``app.services.formatters``.
"""

from app.services.formatters import (  # noqa: F401
    format_boolean,
    format_currency,
    format_currency_compact,
    format_date,
    format_date_display,
    format_enum,
    format_enum_display,
    format_file_size,
    format_percentage,
    parse_date,
    parse_decimal,
    parse_enum_safe,
    truncate_text,
)

__all__ = [
    "format_boolean",
    "format_currency",
    "format_currency_compact",
    "format_date",
    "format_date_display",
    "format_enum",
    "format_enum_display",
    "format_file_size",
    "format_percentage",
    "parse_date",
    "parse_decimal",
    "parse_enum_safe",
    "truncate_text",
]
