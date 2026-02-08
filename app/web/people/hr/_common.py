"""Common imports and utilities for HR web routes."""

from app.models.finance.core_org.location import LocationType


def _parse_bool(value: str | None, default: bool = False) -> bool:
    """Parse a string value to boolean."""
    if value is None:
        return default
    return str(value).lower() in {"1", "true", "on", "yes"}


def _parse_location_type(value: str | None) -> LocationType | None:
    """Parse a string value to LocationType enum."""
    if not value:
        return None
    try:
        return LocationType(value)
    except ValueError:
        return None
