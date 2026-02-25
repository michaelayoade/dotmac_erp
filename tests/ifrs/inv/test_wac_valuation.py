"""Tests for WAC valuation and reconciliation services."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.services.inventory.valuation_reconciliation import (
    ValuationReconciliationService,
)
from app.services.inventory.wac_valuation import WACValuationService


def test_wac_receipt_calculation():
    service = WACValuationService(MagicMock())

    with patch.object(
        service,
        "get_snapshot",
        return_value=SimpleNamespace(
            quantity=Decimal("100"),
            wac=Decimal("10"),
            total_value=Decimal("1000"),
        ),
    ):
        result = service.calculate_receipt_cost(
            uuid4(),
            uuid4(),
            uuid4(),
            receipt_qty=Decimal("50"),
            receipt_unit_cost=Decimal("16"),
        )

    assert result.new_wac == Decimal("12.000000")
    assert result.new_balance_qty == Decimal("150")
    assert result.new_balance_value == Decimal("1800.000000")


def test_wac_issue_uses_current_wac():
    service = WACValuationService(MagicMock())

    with patch.object(
        service,
        "get_snapshot",
        return_value=SimpleNamespace(
            quantity=Decimal("150"),
            wac=Decimal("12"),
            total_value=Decimal("1800"),
        ),
    ):
        result = service.calculate_issue_cost(
            uuid4(),
            uuid4(),
            uuid4(),
            issue_qty=Decimal("30"),
        )

    assert result.unit_cost == Decimal("12")
    assert result.new_wac == Decimal("12")
    assert result.total_cost == Decimal("360.000000")
    assert result.new_balance_qty == Decimal("120")


def test_wac_issue_insufficient_stock_raises():
    service = WACValuationService(MagicMock())

    with patch.object(
        service,
        "get_snapshot",
        return_value=SimpleNamespace(
            quantity=Decimal("10"),
            wac=Decimal("100"),
            total_value=Decimal("1000"),
        ),
    ):
        with pytest.raises(ValueError, match="Insufficient stock"):
            service.calculate_issue_cost(
                uuid4(),
                uuid4(),
                uuid4(),
                issue_qty=Decimal("20"),
            )


def test_reconciliation_uses_latest_period_when_unspecified():
    org_id = uuid4()
    period_id = uuid4()
    db = MagicMock()
    db.scalar.side_effect = [
        period_id,  # latest period id
        Decimal("1200.00"),  # inventory total
        Decimal("1000.00"),  # gl total
    ]
    service = ValuationReconciliationService(db)

    result = service.reconcile(org_id)

    assert result.fiscal_period_id == period_id
    assert result.inventory_total == Decimal("1200.00")
    assert result.gl_total == Decimal("1000.00")
    assert result.difference == Decimal("200.00")
    assert result.is_balanced is False
