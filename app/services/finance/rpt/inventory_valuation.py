"""Inventory valuation reconciliation report context builder."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.services.common import coerce_uuid
from app.services.finance.rpt.common import _format_currency
from app.services.inventory.valuation_reconciliation import (
    ValuationReconciliationService,
)


def inventory_valuation_reconciliation_context(
    db: Session,
    organization_id: str,
) -> dict[str, Any]:
    """Get context for inventory valuation versus GL reconciliation."""
    org_id = coerce_uuid(organization_id)
    service = ValuationReconciliationService(db)
    try:
        result = service.reconcile(org_id)
        return {
            "has_data": True,
            "fiscal_period_id": str(result.fiscal_period_id),
            "inventory_total": _format_currency(result.inventory_total),
            "gl_total": _format_currency(result.gl_total),
            "difference": _format_currency(result.difference),
            "difference_raw": float(result.difference),
            "is_balanced": result.is_balanced,
        }
    except ValueError:
        return {
            "has_data": False,
            "fiscal_period_id": "",
            "inventory_total": _format_currency(Decimal("0")),
            "gl_total": _format_currency(Decimal("0")),
            "difference": _format_currency(Decimal("0")),
            "difference_raw": 0.0,
            "is_balanced": True,
        }
