"""Modular AR API routers."""

from app.api.finance.ar_routes.base import router
from app.api.finance.ar_routes import (
    aging,
    contracts,
    credit_notes,
    customers,
    invoices,
    receipts,
)

__all__ = [
    "aging",
    "contracts",
    "credit_notes",
    "customers",
    "invoices",
    "receipts",
    "router",
]
