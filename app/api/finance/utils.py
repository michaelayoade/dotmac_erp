"""
IFRS API Utilities.

Common utility functions for API routes.
"""

from enum import Enum
from typing import TypeVar

E = TypeVar("E", bound=Enum)


def parse_enum(enum_class: type[E], value: str | None) -> E | None:
    """
    Parse a string value to an enum, handling case-insensitivity and None values.

    This utility standardizes enum conversion across API routes, providing:
    - Case-insensitive parsing (converts to uppercase)
    - None passthrough
    - Consistent error messages

    Args:
        enum_class: The enum class to parse into
        value: The string value to parse, or None

    Returns:
        The parsed enum value, or None if input is None

    Raises:
        ValueError: If the value is not a valid enum member

    Examples:
        >>> parse_enum(JournalStatus, "draft")
        JournalStatus.DRAFT

        >>> parse_enum(JournalStatus, None)
        None

        >>> parse_enum(JournalStatus, "DRAFT")
        JournalStatus.DRAFT
    """
    if value is None:
        return None
    return enum_class(value.upper())


def parse_enum_safe(
    enum_class: type[E], value: str | None, default: E | None = None
) -> E | None:
    """
    Parse a string value to an enum, returning a default on invalid values.

    Like parse_enum, but returns the default value instead of raising ValueError
    if the value is not a valid enum member. Useful for optional filters where
    invalid values should be ignored.

    Args:
        enum_class: The enum class to parse into
        value: The string value to parse, or None
        default: The default value to return if parsing fails

    Returns:
        The parsed enum value, the default if parsing fails, or None if input is None

    Examples:
        >>> parse_enum_safe(JournalStatus, "invalid")
        None

        >>> parse_enum_safe(JournalStatus, "invalid", JournalStatus.DRAFT)
        JournalStatus.DRAFT
    """
    if value is None:
        return default
    try:
        return enum_class(value.upper())
    except (ValueError, KeyError):
        return default
