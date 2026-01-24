"""
Common formatting utilities for IFRS services.

Provides date, currency, enum, and other formatting functions used across web views.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Optional, Type, TypeVar

from app.config import settings

# Type variable for Enum classes
E = TypeVar("E", bound=Enum)


def parse_date(value: Optional[str], format: str = "%Y-%m-%d") -> Optional[date]:
    """
    Parse a date string into a date object.

    Args:
        value: Date string to parse
        format: Date format string (default: ISO format YYYY-MM-DD)

    Returns:
        Parsed date or None if parsing fails or value is empty
    """
    if not value:
        return None
    try:
        return datetime.strptime(value, format).date()
    except (ValueError, TypeError):
        return None


def format_date(value: Optional[date], format: str = "%Y-%m-%d") -> str:
    """
    Format a date object to string.

    Args:
        value: Date to format
        format: Output format string (default: ISO format YYYY-MM-DD)

    Returns:
        Formatted date string or empty string if value is None
    """
    if value is None:
        return ""
    try:
        return value.strftime(format)
    except (ValueError, AttributeError):
        return ""


def format_date_display(value: Optional[date], format: str = "%d %b %Y") -> str:
    """
    Format a date for display (e.g., "15 Jan 2024").

    Args:
        value: Date to format
        format: Output format string (default: "15 Jan 2024" style)

    Returns:
        Formatted date string or empty string if value is None
    """
    return format_date(value, format)


def parse_decimal(value: Optional[str], default: Optional[Decimal] = None) -> Optional[Decimal]:
    """
    Parse a string into a Decimal.

    Args:
        value: String to parse
        default: Default value if parsing fails

    Returns:
        Parsed Decimal or default value
    """
    if not value:
        return default
    try:
        # Remove common formatting characters
        cleaned = str(value).replace(",", "").strip()
        return Decimal(cleaned)
    except (InvalidOperation, ValueError, TypeError):
        return default


def format_currency(
    amount: Optional[Decimal],
    currency: Optional[str] = None,
    none_value: str = "",
    show_symbol: bool = True,
    decimal_places: int = 2,
) -> str:
    """
    Format an amount as currency.

    Args:
        amount: Amount to format
        currency: Currency code (default: settings.default_presentation_currency_code)
        none_value: Value to return if amount is None
        show_symbol: Whether to include currency code prefix
        decimal_places: Number of decimal places

    Returns:
        Formatted currency string
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
    currency: Optional[str] = None,
    none_value: str = "",
) -> str:
    """
    Format an amount as compact currency (no symbol, 2 decimal places).

    Args:
        amount: Amount to format
        currency: Currency code (unused, for signature consistency)
        none_value: Value to return if amount is None

    Returns:
        Formatted number string without currency symbol
    """
    return format_currency(amount, currency, none_value, show_symbol=False)


def parse_enum_safe(
    enum_class: Type[E],
    value: Optional[str],
    default: Optional[E] = None,
) -> Optional[E]:
    """
    Safely parse a string into an enum value.

    Tries exact match first, then case-insensitive match.

    Args:
        enum_class: The Enum class to parse into
        value: String value to parse
        default: Default value if parsing fails

    Returns:
        Parsed enum value or default
    """
    if not value:
        return default

    # Try exact match
    try:
        return enum_class(value)
    except ValueError:
        pass

    # Try uppercase match (common for form values)
    try:
        return enum_class(value.upper())
    except ValueError:
        pass

    # Try lowercase match
    try:
        return enum_class(value.lower())
    except ValueError:
        pass

    # Try by name
    try:
        return enum_class[value.upper()]
    except (KeyError, AttributeError):
        pass

    return default


def format_enum(value: Optional[Enum], none_value: str = "") -> str:
    """
    Format an enum value for display.

    Args:
        value: Enum value to format
        none_value: Value to return if value is None

    Returns:
        The enum's value or name, or none_value if None
    """
    if value is None:
        return none_value
    return str(value.value) if hasattr(value, "value") else str(value.name)


def format_enum_display(value: Optional[Enum], none_value: str = "") -> str:
    """
    Format an enum value for human-readable display.

    Converts UPPER_SNAKE_CASE to Title Case.

    Args:
        value: Enum value to format
        none_value: Value to return if value is None

    Returns:
        Human-readable string
    """
    if value is None:
        return none_value
    # Get the value or name
    text = str(value.value) if hasattr(value, "value") else str(value.name)
    # Convert UPPER_SNAKE_CASE to Title Case
    return text.replace("_", " ").title()


def format_file_size(size: Optional[int], precision: int = 1) -> str:
    """
    Format file size for display with appropriate units.

    Args:
        size: Size in bytes
        precision: Decimal precision for KB/MB/GB

    Returns:
        Formatted size string (e.g., "1.5 MB")
    """
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
    none_value: str = "",
    decimal_places: int = 2,
    show_symbol: bool = True,
) -> str:
    """
    Format a decimal as a percentage.

    Args:
        value: Decimal value (0.15 = 15%)
        none_value: Value to return if value is None
        decimal_places: Number of decimal places
        show_symbol: Whether to include % symbol

    Returns:
        Formatted percentage string
    """
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
    """
    Format a boolean for display.

    Args:
        value: Boolean value
        true_text: Text for True
        false_text: Text for False
        none_text: Text for None

    Returns:
        Formatted string
    """
    if value is None:
        return none_text
    return true_text if value else false_text


def truncate_text(
    text: Optional[str],
    max_length: int = 50,
    suffix: str = "...",
) -> str:
    """
    Truncate text to a maximum length.

    Args:
        text: Text to truncate
        max_length: Maximum length including suffix
        suffix: Suffix to add when truncated

    Returns:
        Truncated text or original if shorter than max_length
    """
    if not text:
        return ""
    if len(text) <= max_length:
        return text
    return text[: max_length - len(suffix)] + suffix
