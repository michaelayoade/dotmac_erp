"""Shared helpers for expense dashboard services."""

from __future__ import annotations

from decimal import Decimal

from app.services.formatters import format_currency


def _format_currency(amount: Decimal, currency: str) -> str:
    """Format amount as currency string."""
    return format_currency(amount, currency, none_value=f"{currency} 0")
