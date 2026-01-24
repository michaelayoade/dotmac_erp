"""
Base mapping utilities for ERPNext to DotMac ERP transformations.
"""
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Optional


@dataclass
class FieldMapping:
    """Define mapping from ERPNext field to DotMac ERP field."""

    source: str  # ERPNext field name
    target: str  # DotMac ERP field name
    required: bool = False
    default: Any = None
    transformer: Optional[Callable[[Any], Any]] = None

    def transform(self, value: Any) -> Any:
        """Transform value from source to target format."""
        if value is None or value == "":
            return self.default
        if self.transformer:
            return self.transformer(value)
        return value


@dataclass
class DocTypeMapping:
    """Base mapping configuration for a DocType."""

    source_doctype: str  # ERPNext DocType name
    target_table: str  # DotMac ERP table (schema.table)
    fields: list[FieldMapping] = field(default_factory=list)
    unique_key: str = "name"  # ERPNext unique identifier field

    def transform_record(self, record: dict[str, Any]) -> dict[str, Any]:
        """Transform an ERPNext record to DotMac ERP format."""
        result = {}
        for mapping in self.fields:
            source_value = record.get(mapping.source)
            result[mapping.target] = mapping.transform(source_value)
        return result


# --------------------------
# Common transformers
# --------------------------


def parse_date(value: Any) -> Optional[date]:
    """Parse date from ERPNext format.

    Handles both date-only and datetime formats from ERPNext.
    """
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        # Try multiple formats - ERPNext sometimes sends datetime for date fields
        for fmt in ["%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"]:
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                continue
        return None
    return None


def parse_datetime(value: Any) -> Optional[datetime]:
    """Parse datetime from ERPNext format."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    if isinstance(value, str):
        for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d"]:
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
        return None
    return None


def parse_decimal(value: Any) -> Optional[Decimal]:
    """Parse decimal from ERPNext format."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def parse_int(value: Any) -> Optional[int]:
    """Parse integer from ERPNext format."""
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def invert_bool(value: Any) -> bool:
    """Invert boolean (e.g., disabled -> is_active)."""
    if value is None:
        return True
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value == 0
    if isinstance(value, str):
        return value.lower() not in ("1", "true", "yes")
    return True


def clean_string(value: Any, max_length: Optional[int] = None) -> Optional[str]:
    """Clean and truncate string value."""
    if value is None:
        return None
    result = str(value).strip()
    if not result:
        return None
    if max_length and len(result) > max_length:
        result = result[:max_length]
    return result


def default_currency(value: Any) -> str:
    """Return default currency if value is empty."""
    if value and str(value).strip():
        return str(value).strip().upper()[:3]
    return "NGN"
