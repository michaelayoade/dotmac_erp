"""
Inventory reorder-status decisions.

Stock quantities remain owned by ``InventoryBalanceService``. This service
applies the configured reorder policy to those quantities and produces the
single read model used by inventory dashboards and drill-downs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models.domain_settings import SettingDomain
from app.models.inventory.item import Item
from app.models.inventory.item_category import ItemCategory
from app.services.common import coerce_uuid
from app.services.inventory.balance import InventoryBalanceService
from app.services.settings_cache import get_setting_value

logger = logging.getLogger(__name__)

APPROACH_THRESHOLD_SETTING = "inventory_low_stock_threshold_percent"
DEFAULT_APPROACH_THRESHOLD_PERCENT = Decimal("20")


class ReorderStatus(str, Enum):
    """Canonical reorder states displayed by the inventory dashboard."""

    APPROACHING_REORDER = "approaching_reorder"
    AT_REORDER = "at_reorder"
    BELOW_REORDER = "below_reorder"


@dataclass(frozen=True)
class ReorderAttentionItem:
    """A tracked item currently requiring reorder attention."""

    item_id: UUID
    item_code: str
    item_name: str
    base_uom: str
    quantity_on_hand: Decimal
    quantity_reserved: Decimal
    quantity_available: Decimal
    reorder_point: Decimal
    stock_to_reorder_percent: Decimal
    status: ReorderStatus


@dataclass(frozen=True)
class ReorderDashboardData:
    """Complete, unfiltered reorder dashboard projection."""

    items: tuple[ReorderAttentionItem, ...]
    counts: dict[ReorderStatus, int]
    unconfigured_count: int
    approach_threshold_percent: Decimal

    @property
    def total_attention_count(self) -> int:
        """Return the number of items in all attention states."""
        return len(self.items)


class InventoryReorderService:
    """Own reorder-status classification for tracked inventory items."""

    @staticmethod
    def classify(
        quantity_available: Decimal,
        reorder_point: Decimal,
        approach_threshold_percent: Decimal,
    ) -> ReorderStatus | None:
        """
        Classify available stock relative to its reorder point.

        Healthy stock above the configured approach band returns ``None``.
        """
        if reorder_point <= 0:
            return None
        if quantity_available < reorder_point:
            return ReorderStatus.BELOW_REORDER
        if quantity_available == reorder_point:
            return ReorderStatus.AT_REORDER

        approach_limit = reorder_point * (
            Decimal("1") + (approach_threshold_percent / Decimal("100"))
        )
        if quantity_available <= approach_limit:
            return ReorderStatus.APPROACHING_REORDER
        return None

    @staticmethod
    def _resolve_approach_threshold(db: Session) -> Decimal:
        raw_value = get_setting_value(
            db,
            SettingDomain.inventory,
            APPROACH_THRESHOLD_SETTING,
            int(DEFAULT_APPROACH_THRESHOLD_PERCENT),
        )
        try:
            threshold = Decimal(str(raw_value))
        except (InvalidOperation, TypeError, ValueError):
            logger.warning(
                "Invalid inventory reorder approach threshold %r; using %s%%",
                raw_value,
                DEFAULT_APPROACH_THRESHOLD_PERCENT,
            )
            return DEFAULT_APPROACH_THRESHOLD_PERCENT

        if threshold < 1 or threshold > 100:
            logger.warning(
                "Inventory reorder approach threshold %s is outside 1-100; using %s%%",
                threshold,
                DEFAULT_APPROACH_THRESHOLD_PERCENT,
            )
            return DEFAULT_APPROACH_THRESHOLD_PERCENT
        return threshold

    def get_dashboard_data(
        self,
        db: Session,
        organization_id: UUID,
    ) -> ReorderDashboardData:
        """Build the organization-wide reorder dashboard with batch stock queries."""
        org_id = coerce_uuid(organization_id)
        approach_threshold = self._resolve_approach_threshold(db)

        rows = db.execute(
            select(Item, ItemCategory)
            .join(
                ItemCategory,
                and_(
                    Item.category_id == ItemCategory.category_id,
                    ItemCategory.organization_id == org_id,
                ),
            )
            .where(
                Item.organization_id == org_id,
                Item.is_active.is_(True),
                Item.track_inventory.is_(True),
            )
        ).all()

        item_ids = [item.item_id for item, _category in rows]
        stock_levels = InventoryBalanceService.get_batch_stock_levels(
            db,
            org_id,
            item_ids,
        )

        attention_items: list[ReorderAttentionItem] = []
        unconfigured_count = 0

        for item, category in rows:
            effective_reorder_point = (
                item.reorder_point
                if item.reorder_point is not None
                else category.reorder_point
            )
            if effective_reorder_point is None or effective_reorder_point <= 0:
                unconfigured_count += 1
                continue

            on_hand, reserved = stock_levels.get(
                item.item_id,
                (Decimal("0"), Decimal("0")),
            )
            available = on_hand - reserved
            status = self.classify(
                available,
                effective_reorder_point,
                approach_threshold,
            )
            if status is None:
                continue

            attention_items.append(
                ReorderAttentionItem(
                    item_id=item.item_id,
                    item_code=item.item_code,
                    item_name=item.item_name,
                    base_uom=item.base_uom,
                    quantity_on_hand=on_hand,
                    quantity_reserved=reserved,
                    quantity_available=available,
                    reorder_point=effective_reorder_point,
                    stock_to_reorder_percent=(
                        available / effective_reorder_point * Decimal("100")
                    ),
                    status=status,
                )
            )

        status_rank = {
            ReorderStatus.BELOW_REORDER: 0,
            ReorderStatus.AT_REORDER: 1,
            ReorderStatus.APPROACHING_REORDER: 2,
        }
        attention_items.sort(
            key=lambda item: (
                status_rank[item.status],
                item.stock_to_reorder_percent,
                item.item_name.casefold(),
            )
        )

        counts = {
            status: sum(1 for item in attention_items if item.status == status)
            for status in ReorderStatus
        }
        return ReorderDashboardData(
            items=tuple(attention_items),
            counts=counts,
            unconfigured_count=unconfigured_count,
            approach_threshold_percent=approach_threshold,
        )


inventory_reorder_service = InventoryReorderService()
