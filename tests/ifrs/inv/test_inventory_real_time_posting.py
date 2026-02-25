"""Tests for real-time inventory valuation posting behavior."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.models.inventory.inventory_transaction import TransactionType
from app.models.inventory.item import CostingMethod
from app.services.inventory.transaction import (
    InventoryTransactionService,
    TransactionInput,
)


def _input(
    *,
    transaction_type: TransactionType,
    item_id,
    warehouse_id,
    quantity: Decimal,
) -> TransactionInput:
    return TransactionInput(
        transaction_type=transaction_type,
        transaction_date=datetime.now(UTC),
        fiscal_period_id=uuid4(),
        item_id=item_id,
        warehouse_id=warehouse_id,
        quantity=quantity,
        unit_cost=Decimal("10"),
        uom="EA",
        currency_code="USD",
    )


def test_create_receipt_real_time_posts_and_updates_wac():
    service = InventoryTransactionService()
    db = MagicMock()
    org_id = uuid4()
    user_id = uuid4()
    item_id = uuid4()
    wh_id = uuid4()
    item = SimpleNamespace(
        item_id=item_id,
        organization_id=org_id,
        costing_method=CostingMethod.WEIGHTED_AVERAGE,
        average_cost=Decimal("0"),
        standard_cost=None,
        last_purchase_cost=None,
        track_lots=False,
    )
    warehouse = SimpleNamespace(
        warehouse_id=wh_id,
        organization_id=org_id,
        is_receiving=True,
    )
    db.get.side_effect = [item, warehouse]

    with (
        patch.object(
            InventoryTransactionService,
            "get_current_balance",
            return_value=Decimal("0"),
        ),
        patch.object(
            InventoryTransactionService,
            "calculate_weighted_average_cost",
            return_value=Decimal("10.500000"),
        ),
        patch(
            "app.services.settings_cache.settings_cache.get_setting_value",
            return_value="real_time",
        ),
        patch.object(
            InventoryTransactionService, "_post_inventory_transaction"
        ) as mock_post,
    ):
        service.create_receipt(
            db,
            org_id,
            _input(
                transaction_type=TransactionType.RECEIPT,
                item_id=item_id,
                warehouse_id=wh_id,
                quantity=Decimal("25"),
            ),
            user_id,
        )

    mock_post.assert_called_once()
    assert item.average_cost == Decimal("10.500000")
    db.commit.assert_called_once()


def test_create_issue_manual_mode_skips_gl_posting():
    service = InventoryTransactionService()
    db = MagicMock()
    org_id = uuid4()
    user_id = uuid4()
    item_id = uuid4()
    wh_id = uuid4()
    item = SimpleNamespace(
        item_id=item_id,
        organization_id=org_id,
        costing_method=CostingMethod.WEIGHTED_AVERAGE,
        average_cost=Decimal("12"),
        standard_cost=None,
        last_purchase_cost=None,
        track_lots=False,
    )
    warehouse = SimpleNamespace(
        warehouse_id=wh_id,
        organization_id=org_id,
        is_receiving=True,
    )
    db.get.side_effect = [item, warehouse]

    with (
        patch.object(
            InventoryTransactionService,
            "get_current_balance",
            return_value=Decimal("100"),
        ),
        patch(
            "app.services.settings_cache.settings_cache.get_setting_value",
            return_value="manual",
        ),
        patch("app.services.inventory.wac_valuation.WACValuationService") as mock_wac,
        patch.object(
            InventoryTransactionService, "_post_inventory_transaction"
        ) as mock_post,
    ):
        mock_wac.return_value.apply_issue.return_value = SimpleNamespace(
            new_wac=Decimal("12.000000")
        )
        service.create_issue(
            db,
            org_id,
            _input(
                transaction_type=TransactionType.ISSUE,
                item_id=item_id,
                warehouse_id=wh_id,
                quantity=Decimal("10"),
            ),
            user_id,
        )

    mock_post.assert_not_called()
    db.commit.assert_called_once()


def test_create_adjustment_real_time_posts():
    service = InventoryTransactionService()
    db = MagicMock()
    org_id = uuid4()
    user_id = uuid4()
    item_id = uuid4()
    wh_id = uuid4()
    item = SimpleNamespace(
        item_id=item_id,
        organization_id=org_id,
        costing_method=CostingMethod.STANDARD_COST,
        average_cost=Decimal("10"),
        standard_cost=Decimal("10"),
        track_lots=False,
    )
    warehouse = SimpleNamespace(
        warehouse_id=wh_id,
        organization_id=org_id,
        is_receiving=True,
    )
    db.get.side_effect = [item, warehouse]

    with (
        patch.object(
            InventoryTransactionService,
            "get_current_balance",
            return_value=Decimal("100"),
        ),
        patch(
            "app.services.settings_cache.settings_cache.get_setting_value",
            return_value="real_time",
        ),
        patch.object(
            InventoryTransactionService, "_post_inventory_transaction"
        ) as mock_post,
    ):
        service.create_adjustment(
            db,
            org_id,
            _input(
                transaction_type=TransactionType.ADJUSTMENT,
                item_id=item_id,
                warehouse_id=wh_id,
                quantity=Decimal("5"),
            ),
            user_id,
        )

    mock_post.assert_called_once()
    db.commit.assert_called_once()
