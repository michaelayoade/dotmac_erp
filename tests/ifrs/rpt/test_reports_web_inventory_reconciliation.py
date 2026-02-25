"""Tests for reports web inventory valuation reconciliation context."""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.services.finance.rpt.web import ReportsWebService


def test_inventory_valuation_reconciliation_context_success():
    db = MagicMock()
    org_id = uuid4()
    mock_result = SimpleNamespace(
        fiscal_period_id=uuid4(),
        inventory_total=Decimal("1300.00"),
        gl_total=Decimal("1000.00"),
        difference=Decimal("300.00"),
        is_balanced=False,
    )

    with patch(
        "app.services.finance.rpt.web.ValuationReconciliationService.reconcile"
    ) as mock_reconcile:
        mock_reconcile.return_value = mock_result
        context = ReportsWebService.inventory_valuation_reconciliation_context(
            db, str(org_id)
        )

    assert context["has_data"] is True
    assert context["fiscal_period_id"] == str(mock_result.fiscal_period_id)
    assert context["difference_raw"] == 300.0
    assert context["is_balanced"] is False


def test_inventory_valuation_reconciliation_context_no_period():
    db = MagicMock()
    org_id = uuid4()

    with patch(
        "app.services.finance.rpt.web.ValuationReconciliationService.reconcile"
    ) as mock_reconcile:
        mock_reconcile.side_effect = ValueError("No fiscal period found")
        context = ReportsWebService.inventory_valuation_reconciliation_context(
            db, str(org_id)
        )

    assert context["has_data"] is False
    assert context["fiscal_period_id"] == ""
    assert context["difference_raw"] == 0.0
    assert context["is_balanced"] is True
