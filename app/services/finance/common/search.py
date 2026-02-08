"""
Common search and filter utilities for IFRS services.

Provides reusable search filter functions for list queries.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, TypeVar

from sqlalchemy import or_
from sqlalchemy.orm import Query
from sqlalchemy.orm.attributes import InstrumentedAttribute

T = TypeVar("T")


def build_search_pattern(term: str, match_type: str = "contains") -> str:
    """
    Build a SQL LIKE pattern from a search term.

    Args:
        term: Search term
        match_type: Type of match - "contains" (default), "starts_with", "ends_with", "exact"

    Returns:
        SQL LIKE pattern string
    """
    # Escape special characters
    escaped = term.replace("%", r"\%").replace("_", r"\_")

    patterns = {
        "starts_with": f"{escaped}%",
        "ends_with": f"%{escaped}",
        "exact": escaped,
        "contains": f"%{escaped}%",
    }
    return patterns.get(match_type, patterns["contains"])


def apply_search_filter(
    query: Query,
    search_term: str | None,
    fields: Sequence[InstrumentedAttribute],
    match_type: str = "contains",
) -> Query:
    """
    Apply a search filter across multiple fields.

    Uses case-insensitive ILIKE matching with OR logic.

    Args:
        query: SQLAlchemy query to filter
        search_term: Search string (if None or empty, query is returned unchanged)
        fields: Sequence of model fields to search
        match_type: Type of match - "contains" (default), "starts_with", "ends_with"

    Returns:
        Filtered query

    Example:
        query = apply_search_filter(
            query,
            search_term="acme",
            fields=[Supplier.supplier_code, Supplier.legal_name, Supplier.trading_name],
        )
    """
    if not search_term or not fields:
        return query

    pattern = build_search_pattern(search_term, match_type)

    # Build OR conditions for all fields
    conditions = [field.ilike(pattern) for field in fields]

    return query.filter(or_(*conditions))


def apply_code_name_search(
    query: Query,
    search_term: str | None,
    code_field: InstrumentedAttribute,
    name_field: InstrumentedAttribute,
    additional_fields: Sequence[InstrumentedAttribute] | None = None,
) -> Query:
    """
    Apply a common code/name search pattern.

    This is a convenience wrapper for the common case of searching
    by code, name, and optionally additional fields.

    Args:
        query: SQLAlchemy query to filter
        search_term: Search string
        code_field: The code field (e.g., Supplier.supplier_code)
        name_field: The name field (e.g., Supplier.legal_name)
        additional_fields: Optional additional fields to include

    Returns:
        Filtered query
    """
    if not search_term:
        return query

    fields = [code_field, name_field]
    if additional_fields:
        fields.extend(additional_fields)

    return apply_search_filter(query, search_term, fields)


def apply_multi_field_filter(
    query: Query,
    filters: dict[InstrumentedAttribute, Any],
) -> Query:
    """
    Apply multiple equality filters to a query.

    Only applies filters where the value is not None.

    Args:
        query: SQLAlchemy query to filter
        filters: Dictionary mapping fields to values

    Returns:
        Filtered query

    Example:
        query = apply_multi_field_filter(
            query,
            {
                Supplier.supplier_type: supplier_type,
                Supplier.is_active: is_active,
            },
        )
    """
    for field, value in filters.items():
        if value is not None:
            query = query.filter(field == value)

    return query


def apply_date_range_filter(
    query: Query,
    date_field: InstrumentedAttribute,
    start_date: Any | None = None,
    end_date: Any | None = None,
) -> Query:
    """
    Apply a date range filter to a query.

    Args:
        query: SQLAlchemy query to filter
        date_field: The date field to filter on
        start_date: Minimum date (inclusive)
        end_date: Maximum date (inclusive)

    Returns:
        Filtered query
    """
    if start_date is not None:
        query = query.filter(date_field >= start_date)
    if end_date is not None:
        query = query.filter(date_field <= end_date)

    return query


def apply_amount_range_filter(
    query: Query,
    amount_field: InstrumentedAttribute,
    min_amount: Any | None = None,
    max_amount: Any | None = None,
) -> Query:
    """
    Apply an amount range filter to a query.

    Args:
        query: SQLAlchemy query to filter
        amount_field: The amount field to filter on
        min_amount: Minimum amount (inclusive)
        max_amount: Maximum amount (inclusive)

    Returns:
        Filtered query
    """
    if min_amount is not None:
        query = query.filter(amount_field >= min_amount)
    if max_amount is not None:
        query = query.filter(amount_field <= max_amount)

    return query


def apply_status_filter(
    query: Query,
    status_field: InstrumentedAttribute,
    statuses: Sequence[Any] | None,
) -> Query:
    """
    Apply a status filter (multiple values using IN).

    Args:
        query: SQLAlchemy query to filter
        status_field: The status field to filter on
        statuses: List of status values to include

    Returns:
        Filtered query
    """
    if statuses:
        query = query.filter(status_field.in_(statuses))

    return query
