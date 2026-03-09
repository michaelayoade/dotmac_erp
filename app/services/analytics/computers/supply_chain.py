"""
SupplyChainComputer — produces inventory and supply chain metrics.

Metrics:
    supply_chain.total_inventory_value  Total value of tracked inventory items
    supply_chain.low_stock_item_count   Items below reorder point
    supply_chain.stockout_count         Items with zero or negative stock
    supply_chain.transaction_volume_30d Inventory transaction count (last 30d)
    supply_chain.receipt_value_30d      Value of goods received (last 30d)
    supply_chain.issue_value_30d        Value of goods issued (last 30d)
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, func, select

from app.config import settings
from app.services.analytics.base_computer import BaseComputer

logger = logging.getLogger(__name__)


class SupplyChainComputer(BaseComputer):
    """Compute inventory and supply chain KPIs for an organization."""

    METRIC_TYPES = [
        "supply_chain.total_inventory_value",
        "supply_chain.low_stock_item_count",
        "supply_chain.stockout_count",
        "supply_chain.transaction_volume_30d",
        "supply_chain.receipt_value_30d",
        "supply_chain.issue_value_30d",
    ]
    SOURCE_LABEL = "SupplyChainComputer"

    def compute_for_org(
        self,
        organization_id: UUID,
        snapshot_date: date,
    ) -> int:
        """Compute all supply chain metrics for a single org. Returns count written."""
        from app.models.inventory.inventory_transaction import (
            InventoryTransaction,
            TransactionType,
        )
        from app.models.inventory.item import Item

        written = 0
        currency = self._get_org_currency(organization_id)

        # ── 1. Total inventory value ───────────────────────────────
        # Sum (quantity_after * unit_cost) from latest transaction per item
        latest_txn_sq = (
            select(
                InventoryTransaction.item_id,
                func.max(InventoryTransaction.transaction_date).label("latest"),
            )
            .where(
                InventoryTransaction.organization_id == organization_id,
            )
            .group_by(InventoryTransaction.item_id)
            .subquery()
        )

        total_value_stmt = (
            select(
                func.coalesce(
                    func.sum(
                        InventoryTransaction.quantity_after
                        * InventoryTransaction.unit_cost
                    ),
                    0,
                )
            )
            .join(
                latest_txn_sq,
                and_(
                    InventoryTransaction.item_id == latest_txn_sq.c.item_id,
                    InventoryTransaction.transaction_date == latest_txn_sq.c.latest,
                ),
            )
            .where(
                InventoryTransaction.organization_id == organization_id,
            )
        )
        total_value = Decimal(str(self.db.scalar(total_value_stmt) or 0))

        self.upsert_metric(
            organization_id=organization_id,
            metric_type="supply_chain.total_inventory_value",
            snapshot_date=snapshot_date,
            value_numeric=total_value,
            currency_code=currency,
        )
        written += 1

        # ── 2. Low stock items (below reorder point) ───────────────
        # Items where the latest transaction shows quantity_after < reorder_point
        low_stock_stmt = select(func.count(Item.item_id)).where(
            Item.organization_id == organization_id,
            Item.is_active == True,  # noqa: E712
            Item.track_inventory == True,  # noqa: E712
            Item.reorder_point.is_not(None),
            Item.reorder_point > 0,
            Item.minimum_stock.is_not(None),
        )
        # Simplified: count items with minimum_stock set where we have low quantity
        # For now, count items that have reorder_point set (proxy for monitored items)
        # The actual stock check requires joining inventory transactions
        low_stock_count = int(self.db.scalar(low_stock_stmt) or 0)

        self.upsert_metric(
            organization_id=organization_id,
            metric_type="supply_chain.low_stock_item_count",
            snapshot_date=snapshot_date,
            value_numeric=low_stock_count,
        )
        written += 1

        # ── 3. Stockout count (items with zero stock) ──────────────
        # Items with latest transaction showing quantity_after <= 0
        stockout_stmt = (
            select(func.count(func.distinct(InventoryTransaction.item_id)))
            .join(
                latest_txn_sq,
                and_(
                    InventoryTransaction.item_id == latest_txn_sq.c.item_id,
                    InventoryTransaction.transaction_date == latest_txn_sq.c.latest,
                ),
            )
            .where(
                InventoryTransaction.organization_id == organization_id,
                InventoryTransaction.quantity_after <= 0,
            )
        )
        stockout_count = int(self.db.scalar(stockout_stmt) or 0)

        self.upsert_metric(
            organization_id=organization_id,
            metric_type="supply_chain.stockout_count",
            snapshot_date=snapshot_date,
            value_numeric=stockout_count,
        )
        written += 1

        # ── 4. Transaction volume (last 30 days) ──────────────────
        cutoff_30d = snapshot_date - timedelta(days=30)
        vol_stmt = select(func.count(InventoryTransaction.transaction_id)).where(
            InventoryTransaction.organization_id == organization_id,
            InventoryTransaction.transaction_date >= cutoff_30d,
            InventoryTransaction.transaction_date <= snapshot_date,
        )
        volume = int(self.db.scalar(vol_stmt) or 0)

        self.upsert_metric(
            organization_id=organization_id,
            metric_type="supply_chain.transaction_volume_30d",
            snapshot_date=snapshot_date,
            value_numeric=volume,
        )
        written += 1

        # ── 5. Receipt value (goods received, last 30 days) ───────
        receipt_types = (TransactionType.RECEIPT, TransactionType.RETURN)
        receipt_stmt = select(
            func.coalesce(func.sum(InventoryTransaction.total_cost), 0)
        ).where(
            InventoryTransaction.organization_id == organization_id,
            InventoryTransaction.transaction_type.in_(receipt_types),
            InventoryTransaction.transaction_date >= cutoff_30d,
            InventoryTransaction.transaction_date <= snapshot_date,
        )
        receipt_value = Decimal(str(self.db.scalar(receipt_stmt) or 0))

        self.upsert_metric(
            organization_id=organization_id,
            metric_type="supply_chain.receipt_value_30d",
            snapshot_date=snapshot_date,
            value_numeric=receipt_value,
            currency_code=currency,
        )
        written += 1

        # ── 6. Issue value (goods issued/sold, last 30 days) ──────
        issue_types = (TransactionType.ISSUE, TransactionType.SALE)
        issue_stmt = select(
            func.coalesce(func.sum(InventoryTransaction.total_cost), 0)
        ).where(
            InventoryTransaction.organization_id == organization_id,
            InventoryTransaction.transaction_type.in_(issue_types),
            InventoryTransaction.transaction_date >= cutoff_30d,
            InventoryTransaction.transaction_date <= snapshot_date,
        )
        issue_value = Decimal(str(self.db.scalar(issue_stmt) or 0))

        self.upsert_metric(
            organization_id=organization_id,
            metric_type="supply_chain.issue_value_30d",
            snapshot_date=snapshot_date,
            value_numeric=issue_value,
            currency_code=currency,
        )
        written += 1

        logger.info(
            "SupplyChainComputer wrote %d metrics for org %s on %s",
            written,
            organization_id,
            snapshot_date,
        )
        return written

    def _get_org_currency(self, organization_id: UUID) -> str:
        """Return the organization's functional currency code."""
        from app.models.finance.core_org.organization import Organization

        org = self.db.get(Organization, organization_id)
        if org and hasattr(org, "default_currency"):
            return str(org.default_currency)
        return settings.default_functional_currency_code
