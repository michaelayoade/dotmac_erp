"""
Reusable column-sorting helper for list endpoints.

Provides a whitelist-based ``apply_sort()`` that prevents SQL injection by
mapping caller-supplied column names to real SQLAlchemy columns.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import asc, desc
from sqlalchemy.orm import Query
from sqlalchemy.sql import Select


def apply_sort(
    query: Query | Select,
    sort: str | None,
    sort_dir: str | None,
    column_map: dict[str, Any],
    default: Any | None = None,
) -> Query | Select:
    """Apply user-requested sorting to a SQLAlchemy query.

    Args:
        query: The query to sort.
        sort: Column name from the request (must be a key in *column_map*).
        sort_dir: ``"asc"`` or ``"desc"``.  Defaults to ``"desc"``.
        column_map: Whitelist of ``{name: SQLAlchemy column expression}``.
        default: Fallback ``order_by`` clause when *sort* is ``None`` or
            not in *column_map*.  May be a column, a list, or ``None``.

    Returns:
        The query with an ``order_by`` clause applied.
    """
    direction = desc if (sort_dir or "").lower() != "asc" else asc

    if sort and sort in column_map:
        return query.order_by(direction(column_map[sort]))

    # Default fallback
    if default is not None:
        if isinstance(default, (list, tuple)):
            return query.order_by(*default)
        return query.order_by(default)

    return query
