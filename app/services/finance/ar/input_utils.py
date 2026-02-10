"""
AR input parsing utilities.

Shared helpers for parsing and validating web/API payloads before
calling service-layer operations.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

from app.services.common import coerce_uuid
from app.services.finance.platform.org_context import org_context_service


def parse_date_str(
    value: str | None, field_name: str, required: bool = False
) -> date | None:
    """Parse ISO date string (YYYY-MM-DD)."""
    if not value:
        if required:
            raise ValueError(f"{field_name} is required")
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"Invalid {field_name}") from exc


def parse_decimal(
    value: Any, field_name: str, default: Decimal = Decimal("0")
) -> Decimal:
    """Parse numeric into Decimal."""
    if value is None or value == "":
        return default
    try:
        return Decimal(str(value))
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Invalid {field_name}") from exc


def parse_json_list(value: Any, field_name: str) -> list[dict]:
    """Parse list of dicts from JSON string or pass-through list."""
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid {field_name}") from exc
    if not isinstance(parsed, list):
        raise ValueError(f"Invalid {field_name}")
    return parsed


def require_uuid(value: str | None, field_name: str) -> UUID:
    """Parse and require a UUID field."""
    if not value:
        raise ValueError(f"{field_name} is required")
    return cast(UUID, coerce_uuid(value))


def resolve_currency_code(db, organization_id: UUID, currency_code: str | None) -> str:
    """Resolve currency code, falling back to org functional currency."""
    if currency_code:
        return currency_code
    return org_context_service.get_functional_currency(db, organization_id)
