"""Supply-chain health analyzer (deterministic, no LLM required).

Monitors stockout risk, dead stock, and reorder alerts.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, delete, func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.coach.insight import CoachInsight
from app.models.inventory.inventory_transaction import InventoryTransaction
from app.models.inventory.item import Item

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StockoutRiskSummary:
    """Items at or below reorder point."""

    items_below_reorder: int
    items_at_zero_stock: int
    total_tracked_items: int


@dataclass(frozen=True)
class DeadStockSummary:
    """Items with no movement in 90+ days."""

    dead_stock_count: int
    dead_stock_value: Decimal
    currency_code: str


def _severity_for_stockout(at_zero: int, below_reorder: int) -> str:
    if at_zero >= 5:
        return "WARNING"
    if at_zero >= 1 or below_reorder >= 10:
        return "ATTENTION"
    return "INFO"


def _severity_for_dead_stock(count: int) -> str:
    if count >= 20:
        return "WARNING"
    if count >= 5:
        return "ATTENTION"
    return "INFO"


class SupplyChainAnalyzer:
    """Deterministic supply-chain health analyzer.

    Generates org-wide Operations insights for stockout risk
    and dead stock accumulation.
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # MetricStore fast-path
    # ------------------------------------------------------------------
    def _quick_check_from_store(self, organization_id: UUID) -> bool:
        """Return True if MetricStore shows zero stockout items."""
        from app.services.coach.analyzers import metric_is_fresh

        fresh, value = metric_is_fresh(
            self.db, organization_id, "supply_chain.stockout_count"
        )
        if fresh and value is not None and value <= 0:
            # Also check low-stock
            fresh_ls, ls_val = metric_is_fresh(
                self.db, organization_id, "supply_chain.low_stock_item_count"
            )
            if fresh_ls and (ls_val or Decimal("0")) <= 0:
                logger.debug(
                    "SupplyChain fast-path: zero stockout + low stock, skipping"
                )
                return True
        return False

    # ------------------------------------------------------------------
    # Core computations
    # ------------------------------------------------------------------
    def stockout_risk(self, organization_id: UUID) -> StockoutRiskSummary:
        """Items at or below reorder point, and items at zero stock."""
        active_inv = and_(
            Item.organization_id == organization_id,
            Item.is_active.is_(True),
            Item.track_inventory.is_(True),
        )

        total_tracked = int(
            self.db.scalar(select(func.count()).select_from(Item).where(active_inv))
            or 0
        )

        # Items below reorder point (using current stock from latest transaction)
        # Subquery: latest quantity_after per item
        latest_txn = (
            select(
                InventoryTransaction.item_id,
                func.max(InventoryTransaction.transaction_id).label("max_txn"),
            )
            .where(InventoryTransaction.organization_id == organization_id)
            .group_by(InventoryTransaction.item_id)
            .subquery()
        )

        current_stock_sq = (
            select(
                InventoryTransaction.item_id,
                InventoryTransaction.quantity_after.label("current_qty"),
            )
            .join(
                latest_txn,
                and_(
                    InventoryTransaction.item_id == latest_txn.c.item_id,
                    InventoryTransaction.transaction_id == latest_txn.c.max_txn,
                ),
            )
            .subquery()
        )

        # Items below reorder point
        below_reorder_stmt = (
            select(func.count())
            .select_from(Item)
            .join(
                current_stock_sq,
                Item.item_id == current_stock_sq.c.item_id,
                isouter=True,
            )
            .where(
                active_inv,
                Item.reorder_point.is_not(None),
                Item.reorder_point > 0,
                func.coalesce(current_stock_sq.c.current_qty, 0) <= Item.reorder_point,
            )
        )
        below_reorder = int(self.db.scalar(below_reorder_stmt) or 0)

        # Items at zero stock
        at_zero_stmt = (
            select(func.count())
            .select_from(Item)
            .join(
                current_stock_sq,
                Item.item_id == current_stock_sq.c.item_id,
                isouter=True,
            )
            .where(
                active_inv,
                func.coalesce(current_stock_sq.c.current_qty, 0) <= 0,
            )
        )
        at_zero = int(self.db.scalar(at_zero_stmt) or 0)

        return StockoutRiskSummary(
            items_below_reorder=below_reorder,
            items_at_zero_stock=at_zero,
            total_tracked_items=total_tracked,
        )

    def dead_stock(self, organization_id: UUID) -> DeadStockSummary:
        """Items with inventory tracking but no movement in 90+ days."""
        from app.models.finance.core_org.organization import Organization

        org = self.db.scalar(
            select(Organization).where(Organization.organization_id == organization_id)
        )
        currency_code = (
            org.functional_currency_code
            if org
            else settings.default_functional_currency_code
        )

        cutoff_90d = datetime.now(UTC) - timedelta(days=90)

        # Items that have transactions but none in last 90 days
        items_with_recent = (
            select(InventoryTransaction.item_id)
            .where(
                InventoryTransaction.organization_id == organization_id,
                InventoryTransaction.created_at >= cutoff_90d,
            )
            .distinct()
            .subquery()
        )

        items_with_any = (
            select(InventoryTransaction.item_id)
            .where(InventoryTransaction.organization_id == organization_id)
            .distinct()
            .subquery()
        )

        dead_stmt = (
            select(
                func.count().label("cnt"),
                func.coalesce(func.sum(Item.standard_cost), 0).label("value"),
            )
            .select_from(Item)
            .join(items_with_any, Item.item_id == items_with_any.c.item_id)
            .where(
                Item.organization_id == organization_id,
                Item.is_active.is_(True),
                Item.track_inventory.is_(True),
                Item.item_id.not_in(select(items_with_recent.c.item_id)),
            )
        )
        row = self.db.execute(dead_stmt).one()

        return DeadStockSummary(
            dead_stock_count=int(row.cnt or 0),
            dead_stock_value=Decimal(str(row.value or "0")),
            currency_code=currency_code,
        )

    # ------------------------------------------------------------------
    # Insight generation
    # ------------------------------------------------------------------
    def generate_stockout_risk_insight(
        self, organization_id: UUID
    ) -> CoachInsight | None:
        if self._quick_check_from_store(organization_id):
            return None

        risk = self.stockout_risk(organization_id)
        if risk.total_tracked_items == 0:
            return None
        if risk.items_below_reorder == 0 and risk.items_at_zero_stock == 0:
            return None

        severity = _severity_for_stockout(
            risk.items_at_zero_stock, risk.items_below_reorder
        )
        title = "Stockout risk alert"
        summary_text = (
            f"{risk.items_at_zero_stock} item(s) at zero stock, "
            f"{risk.items_below_reorder} item(s) below reorder point "
            f"(out of {risk.total_tracked_items} tracked)."
        )
        coaching_action = (
            "Review zero-stock items and create purchase orders immediately "
            "for critical items. For items below reorder point, check lead "
            "times and place orders before stockout occurs."
        )

        return CoachInsight(
            insight_id=uuid.uuid4(),
            organization_id=organization_id,
            audience="OPERATIONS",
            target_employee_id=None,
            category="SUPPLY_CHAIN",
            severity=severity,
            title=title,
            summary=summary_text,
            detail=None,
            coaching_action=coaching_action,
            confidence=0.9,
            data_sources={"inv.item": risk.total_tracked_items},
            evidence={
                "items_at_zero_stock": risk.items_at_zero_stock,
                "items_below_reorder": risk.items_below_reorder,
                "total_tracked_items": risk.total_tracked_items,
            },
            status="GENERATED",
            delivered_at=None,
            read_at=None,
            dismissed_at=None,
            feedback=None,
            valid_until=date.today() + timedelta(days=1),
            created_at=datetime.now(UTC),
        )

    def generate_dead_stock_insight(self, organization_id: UUID) -> CoachInsight | None:
        dead = self.dead_stock(organization_id)
        if dead.dead_stock_count == 0:
            return None

        severity = _severity_for_dead_stock(dead.dead_stock_count)
        title = "Dead stock accumulation"
        summary_text = (
            f"{dead.dead_stock_count} item(s) with no movement in 90+ days. "
            f"Estimated value: {dead.currency_code} {dead.dead_stock_value:,.2f}."
        )
        coaching_action = (
            "Review dead stock items for disposal, markdown, or return to supplier. "
            "Holding costs accumulate on stagnant inventory. Consider adjusting "
            "reorder quantities to prevent future over-ordering."
        )

        return CoachInsight(
            insight_id=uuid.uuid4(),
            organization_id=organization_id,
            audience="OPERATIONS",
            target_employee_id=None,
            category="SUPPLY_CHAIN",
            severity=severity,
            title=title,
            summary=summary_text,
            detail=None,
            coaching_action=coaching_action,
            confidence=0.85,
            data_sources={"inv.item": dead.dead_stock_count},
            evidence={
                "dead_stock_count": dead.dead_stock_count,
                "dead_stock_value": str(dead.dead_stock_value),
                "currency_code": dead.currency_code,
            },
            status="GENERATED",
            delivered_at=None,
            read_at=None,
            dismissed_at=None,
            feedback=None,
            valid_until=date.today() + timedelta(days=1),
            created_at=datetime.now(UTC),
        )

    def upsert_daily_org_insights(self, organization_id: UUID) -> int:
        today = date.today()
        written = 0

        self.db.execute(
            delete(CoachInsight).where(
                CoachInsight.organization_id == organization_id,
                CoachInsight.target_employee_id.is_(None),
                CoachInsight.category == "SUPPLY_CHAIN",
                CoachInsight.audience == "OPERATIONS",
                func.date(CoachInsight.created_at) == today,
                CoachInsight.title.in_(
                    ["Stockout risk alert", "Dead stock accumulation"]
                ),
            )
        )

        for gen in (
            self.generate_stockout_risk_insight,
            self.generate_dead_stock_insight,
        ):
            insight = gen(organization_id)
            if insight:
                self.db.add(insight)
                written += 1

        if written:
            self.db.flush()
        return written
