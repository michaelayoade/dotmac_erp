"""
InventoryCountService - Physical inventory counts and cycle counts.

Manages inventory counts, variance calculation, and adjustment posting.
"""

from __future__ import annotations

import builtins
import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone

try:
    from datetime import UTC  # type: ignore
except ImportError:  # pragma: no cover
    UTC = timezone.utc

from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session

from app.models.inventory.inventory_count import CountStatus, InventoryCount
from app.models.inventory.inventory_count_line import InventoryCountLine
from app.models.inventory.inventory_transaction import TransactionType
from app.models.inventory.item import Item
from app.models.inventory.warehouse import Warehouse
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin

logger = logging.getLogger(__name__)


@dataclass
class CountInput:
    """Input for creating an inventory count."""

    count_number: str
    count_date: date
    fiscal_period_id: UUID
    count_description: str | None = None
    warehouse_id: UUID | None = None
    location_id: UUID | None = None
    category_id: UUID | None = None
    is_full_count: bool = False
    is_cycle_count: bool = False


@dataclass
class CountLineInput:
    """Input for recording a count."""

    item_id: UUID
    warehouse_id: UUID
    counted_quantity: Decimal
    lot_id: UUID | None = None
    location_id: UUID | None = None
    reason_code: str | None = None
    notes: str | None = None


@dataclass
class BulkCountLineInput:
    """Input for bulk-recording line quantities."""

    line_id: UUID
    counted_quantity: Decimal


@dataclass
class CountSummary:
    """Summary statistics for an inventory count."""

    count_id: UUID
    count_number: str
    status: str
    total_items: int
    items_counted: int
    items_with_variance: int
    total_variance_value: Decimal
    positive_variance_value: Decimal
    negative_variance_value: Decimal


class InventoryCountService(ListResponseMixin):
    """
    Service for inventory counts and cycle counts.

    Handles count creation, recording, variance calculation, and posting.
    """

    @staticmethod
    def _get_scoped_items(
        db: Session,
        organization_id: UUID,
        input: CountInput,
    ) -> builtins.list[Item]:
        """Get inventory-tracked items matching the count scope."""
        items_query = select(Item).where(
            and_(
                Item.organization_id == organization_id,
                Item.is_active == True,
                Item.track_inventory == True,
            )
        )

        if input.category_id:
            items_query = items_query.where(
                Item.category_id == coerce_uuid(input.category_id)
            )

        return list(db.scalars(items_query).all())

    @staticmethod
    def _get_scoped_warehouses(
        db: Session,
        organization_id: UUID,
        input: CountInput,
    ) -> builtins.list[Warehouse]:
        """Get warehouses matching the count scope."""
        if input.warehouse_id:
            warehouse = db.get(Warehouse, coerce_uuid(input.warehouse_id))
            if (
                warehouse
                and warehouse.organization_id == organization_id
                and warehouse.is_active
            ):
                return [warehouse]
            raise HTTPException(status_code=404, detail="Warehouse not found")

        return list(
            db.scalars(
                select(Warehouse).where(
                    and_(
                        Warehouse.organization_id == organization_id,
                        Warehouse.is_active == True,
                    )
                )
            ).all()
        )

    @staticmethod
    def _recalculate_count_stats(
        db: Session,
        count: InventoryCount,
    ) -> None:
        """Refresh count header statistics from current lines."""
        count.total_items = (
            db.scalar(
                select(func.count(InventoryCountLine.line_id)).where(
                    InventoryCountLine.count_id == count.count_id
                )
            )
            or 0
        )
        count.items_counted = (
            db.scalar(
                select(func.count(InventoryCountLine.line_id)).where(
                    and_(
                        InventoryCountLine.count_id == count.count_id,
                        InventoryCountLine.counted_quantity.isnot(None),
                    )
                )
            )
            or 0
        )
        count.items_with_variance = (
            db.scalar(
                select(func.count(InventoryCountLine.line_id)).where(
                    and_(
                        InventoryCountLine.count_id == count.count_id,
                        InventoryCountLine.variance_quantity.isnot(None),
                        InventoryCountLine.variance_quantity != 0,
                    )
                )
            )
            or 0
        )

    @staticmethod
    def _apply_count_to_line(
        *,
        line: InventoryCountLine,
        counted_quantity: Decimal,
        counted_by_user_id: UUID,
        reason_code: str | None = None,
        notes: str | None = None,
    ) -> None:
        """Apply count values and variance calculations to a single line."""
        user_id = coerce_uuid(counted_by_user_id)

        if line.counted_quantity is None:
            line.counted_quantity = counted_quantity
            line.counted_by_user_id = user_id
            line.counted_at = datetime.now(UTC)
        else:
            line.recount_quantity = counted_quantity
            line.recounted_by_user_id = user_id
            line.recounted_at = datetime.now(UTC)

        line.final_quantity = counted_quantity
        line.reason_code = reason_code
        line.notes = notes

        line.variance_quantity = line.final_quantity - line.system_quantity
        line.variance_value = line.variance_quantity * line.unit_cost

        if line.system_quantity > 0:
            line.variance_percent = (
                (line.variance_quantity / line.system_quantity) * 100
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        else:
            line.variance_percent = (
                Decimal("100") if line.variance_quantity > 0 else Decimal("0")
            )

    @staticmethod
    def _build_count_snapshot(
        db: Session,
        count: InventoryCount,
    ) -> None:
        """Create frozen count lines for the count scope."""
        from app.services.inventory.balance import inventory_balance_service

        existing_lines = db.scalar(
            select(func.count(InventoryCountLine.line_id)).where(
                InventoryCountLine.count_id == count.count_id
            )
        )
        if existing_lines:
            raise HTTPException(
                status_code=400,
                detail="Count snapshot already exists for this count",
            )

        count_input = CountInput(
            count_number=count.count_number,
            count_date=count.count_date,
            fiscal_period_id=count.fiscal_period_id,
            count_description=count.count_description,
            warehouse_id=count.warehouse_id,
            location_id=count.location_id,
            category_id=count.category_id,
            is_full_count=count.is_full_count,
            is_cycle_count=count.is_cycle_count,
        )
        items = InventoryCountService._get_scoped_items(
            db, count.organization_id, count_input
        )
        warehouses = InventoryCountService._get_scoped_warehouses(
            db, count.organization_id, count_input
        )

        for item in items:
            for warehouse in warehouses:
                system_qty = inventory_balance_service.get_on_hand(
                    db=db,
                    organization_id=count.organization_id,
                    item_id=item.item_id,
                    warehouse_id=warehouse.warehouse_id,
                )

                if system_qty > 0 or count.is_full_count:
                    db.add(
                        InventoryCountLine(
                            count_id=count.count_id,
                            item_id=item.item_id,
                            warehouse_id=warehouse.warehouse_id,
                            location_id=count.location_id,
                            system_quantity=system_qty,
                            uom=item.base_uom,
                            unit_cost=item.average_cost
                            or item.standard_cost
                            or Decimal("0"),
                        )
                    )

        InventoryCountService._recalculate_count_stats(db, count)

    @staticmethod
    def start_count(
        db: Session,
        organization_id: UUID,
        count_id: UUID,
        started_by_user_id: UUID,
    ) -> InventoryCount:
        """Mark a draft count as in progress."""
        org_id = coerce_uuid(organization_id)
        cnt_id = coerce_uuid(count_id)
        _ = coerce_uuid(started_by_user_id)

        count = db.get(InventoryCount, cnt_id)
        if not count or count.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Count not found")

        if count.status != CountStatus.DRAFT:
            raise HTTPException(status_code=400, detail="Count must be in DRAFT status")

        InventoryCountService._build_count_snapshot(db, count)
        count.status = CountStatus.IN_PROGRESS
        db.flush()
        return count

    @staticmethod
    def create_count(
        db: Session,
        organization_id: UUID,
        input: CountInput,
        created_by_user_id: UUID,
    ) -> InventoryCount:
        """
        Create a new inventory count.

        Snapshots current system quantities for items in scope.

        Args:
            db: Database session
            organization_id: Organization scope
            input: Count input data
            created_by_user_id: User creating

        Returns:
            Created InventoryCount with lines
        """
        org_id = coerce_uuid(organization_id)
        user_id = coerce_uuid(created_by_user_id)

        # Check for duplicate count number
        existing = db.scalars(
            select(InventoryCount).where(
                and_(
                    InventoryCount.organization_id == org_id,
                    InventoryCount.count_number == input.count_number,
                )
            )
        ).first()

        if existing:
            raise HTTPException(
                status_code=400,
                detail=f"Count number '{input.count_number}' already exists",
            )

        # Create count header
        count = InventoryCount(
            organization_id=org_id,
            count_number=input.count_number,
            count_description=input.count_description,
            count_date=input.count_date,
            fiscal_period_id=coerce_uuid(input.fiscal_period_id),
            warehouse_id=coerce_uuid(input.warehouse_id)
            if input.warehouse_id
            else None,
            location_id=coerce_uuid(input.location_id) if input.location_id else None,
            category_id=coerce_uuid(input.category_id) if input.category_id else None,
            is_full_count=input.is_full_count,
            is_cycle_count=input.is_cycle_count,
            status=CountStatus.DRAFT,
            created_by_user_id=user_id,
        )

        db.add(count)
        db.flush()
        db.flush()
        return count

    @staticmethod
    def record_count(
        db: Session,
        organization_id: UUID,
        count_id: UUID,
        input: CountLineInput,
        counted_by_user_id: UUID,
    ) -> InventoryCountLine:
        """
        Record a counted quantity for an item.

        Args:
            db: Database session
            organization_id: Organization scope
            count_id: Count ID
            input: Count line input data
            counted_by_user_id: User recording

        Returns:
            Updated InventoryCountLine
        """
        org_id = coerce_uuid(organization_id)
        cnt_id = coerce_uuid(count_id)
        user_id = coerce_uuid(counted_by_user_id)
        itm_id = coerce_uuid(input.item_id)
        wh_id = coerce_uuid(input.warehouse_id)

        # Get count
        count = db.get(InventoryCount, cnt_id)
        if not count or count.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Count not found")

        if count.status in [CountStatus.POSTED, CountStatus.CANCELLED]:
            raise HTTPException(
                status_code=400,
                detail="Cannot record counts on posted or cancelled counts",
            )
        if count.status != CountStatus.IN_PROGRESS:
            raise HTTPException(
                status_code=400,
                detail="Count must be IN_PROGRESS before recording quantities",
            )

        # Find or create line
        line = db.scalars(
            select(InventoryCountLine).where(
                and_(
                    InventoryCountLine.count_id == cnt_id,
                    InventoryCountLine.item_id == itm_id,
                    InventoryCountLine.warehouse_id == wh_id,
                    InventoryCountLine.lot_id
                    == (coerce_uuid(input.lot_id) if input.lot_id else None),
                )
            )
        ).first()

        if not line:
            # Create new line (for items not in original snapshot)
            item = db.get(Item, itm_id)
            if not item or item.organization_id != org_id:
                raise HTTPException(status_code=404, detail="Item not found")
            warehouse = db.get(Warehouse, wh_id)
            if not warehouse or warehouse.organization_id != org_id:
                raise HTTPException(status_code=404, detail="Warehouse not found")

            line = InventoryCountLine(
                count_id=cnt_id,
                item_id=itm_id,
                warehouse_id=wh_id,
                location_id=coerce_uuid(input.location_id)
                if input.location_id
                else None,
                lot_id=coerce_uuid(input.lot_id) if input.lot_id else None,
                system_quantity=Decimal("0"),  # New item not in snapshot
                uom=item.base_uom,
                unit_cost=item.average_cost or item.standard_cost or Decimal("0"),
            )
            db.add(line)

        InventoryCountService._apply_count_to_line(
            line=line,
            counted_quantity=input.counted_quantity,
            counted_by_user_id=user_id,
            reason_code=input.reason_code,
            notes=input.notes,
        )

        InventoryCountService._recalculate_count_stats(db, count)

        db.flush()
        return line

    @staticmethod
    def record_count_bulk(
        db: Session,
        organization_id: UUID,
        count_id: UUID,
        inputs: builtins.list[BulkCountLineInput],
        counted_by_user_id: UUID,
    ) -> builtins.list[InventoryCountLine]:
        """Record counted quantities for multiple existing count lines."""
        org_id = coerce_uuid(organization_id)
        cnt_id = coerce_uuid(count_id)
        user_id = coerce_uuid(counted_by_user_id)

        count = db.get(InventoryCount, cnt_id)
        if not count or count.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Count not found")
        if count.status != CountStatus.IN_PROGRESS:
            raise HTTPException(
                status_code=400,
                detail="Count must be IN_PROGRESS before recording quantities",
            )

        updated_lines: builtins.list[InventoryCountLine] = []
        for entry in inputs:
            line = db.get(InventoryCountLine, coerce_uuid(entry.line_id))
            if not line or line.count_id != cnt_id:
                raise HTTPException(status_code=404, detail="Count line not found")

            InventoryCountService._apply_count_to_line(
                line=line,
                counted_quantity=entry.counted_quantity,
                counted_by_user_id=user_id,
            )
            updated_lines.append(line)

        InventoryCountService._recalculate_count_stats(db, count)
        db.flush()
        return updated_lines

    @staticmethod
    def complete_count(
        db: Session,
        organization_id: UUID,
        count_id: UUID,
    ) -> InventoryCount:
        """
        Mark count as completed (ready for review/approval).

        Args:
            db: Database session
            organization_id: Organization scope
            count_id: Count ID

        Returns:
            Updated InventoryCount
        """
        org_id = coerce_uuid(organization_id)
        cnt_id = coerce_uuid(count_id)

        count = db.get(InventoryCount, cnt_id)
        if not count or count.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Count not found")

        if count.status != CountStatus.IN_PROGRESS:
            raise HTTPException(
                status_code=400,
                detail="Count must be IN_PROGRESS before completion",
            )

        InventoryCountService._recalculate_count_stats(db, count)
        if count.total_items == 0:
            raise HTTPException(
                status_code=400,
                detail="Count has no lines. Start the count to generate items first",
            )
        if count.items_counted < count.total_items:
            raise HTTPException(
                status_code=400,
                detail="All count lines must be recorded before completion",
            )

        count.status = CountStatus.COMPLETED
        db.flush()
        return count

    @staticmethod
    def approve_count(
        db: Session,
        organization_id: UUID,
        count_id: UUID,
        approved_by_user_id: UUID,
    ) -> InventoryCount:
        """
        Approve a completed count for posting.

        Args:
            db: Database session
            organization_id: Organization scope
            count_id: Count ID
            approved_by_user_id: Approving user

        Returns:
            Updated InventoryCount
        """
        org_id = coerce_uuid(organization_id)
        cnt_id = coerce_uuid(count_id)
        user_id = coerce_uuid(approved_by_user_id)

        count = db.get(InventoryCount, cnt_id)
        if not count or count.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Count not found")

        if count.status != CountStatus.COMPLETED:
            raise HTTPException(
                status_code=400,
                detail="Count must be COMPLETED before approval",
            )

        count.approved_by_user_id = user_id
        count.approved_at = datetime.now(UTC)

        db.flush()
        return count

    @staticmethod
    def post_count(
        db: Session,
        organization_id: UUID,
        count_id: UUID,
        posted_by_user_id: UUID,
    ) -> InventoryCount:
        """
        Post count adjustments as COUNT_ADJUSTMENT transactions.

        Creates adjustment transactions for all lines with variances.

        Args:
            db: Database session
            organization_id: Organization scope
            count_id: Count ID
            posted_by_user_id: Posting user

        Returns:
            Updated InventoryCount
        """
        from app.services.inventory.transaction import (
            TransactionInput,
            inventory_transaction_service,
        )

        org_id = coerce_uuid(organization_id)
        cnt_id = coerce_uuid(count_id)
        user_id = coerce_uuid(posted_by_user_id)

        count = db.get(InventoryCount, cnt_id)
        if not count or count.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Count not found")

        if count.status == CountStatus.POSTED:
            raise HTTPException(status_code=400, detail="Count already posted")

        if count.status != CountStatus.COMPLETED:
            raise HTTPException(
                status_code=400,
                detail="Count must be COMPLETED before posting",
            )

        # Get lines with variances
        lines = list(
            db.scalars(
                select(InventoryCountLine).where(
                    and_(
                        InventoryCountLine.count_id == cnt_id,
                        InventoryCountLine.variance_quantity != 0,
                        InventoryCountLine.variance_quantity.isnot(None),
                    )
                )
            ).all()
        )

        # Resolve every referenced item before creating the first adjustment.
        # The request transaction still owns atomicity if a later adjustment
        # fails, while this preflight prevents a missing item from producing a
        # partially prepared post in callers that inspect the session before
        # the request boundary rolls it back.
        resolved_lines: builtins.list[tuple[InventoryCountLine, Item]] = []
        for line in lines:
            item = db.get(Item, line.item_id)
            if not item:
                raise HTTPException(
                    status_code=404,
                    detail=f"Item not found for count line {line.line_id}",
                )
            resolved_lines.append((line, item))

        # Create adjustment transactions
        for line, item in resolved_lines:
            variance_qty = line.variance_quantity or Decimal("0")
            txn_input = TransactionInput(
                transaction_type=TransactionType.COUNT_ADJUSTMENT,
                transaction_date=datetime.combine(
                    count.count_date, datetime.min.time()
                ),
                fiscal_period_id=count.fiscal_period_id,
                item_id=line.item_id,
                warehouse_id=line.warehouse_id,
                quantity=variance_qty,
                unit_cost=line.unit_cost,
                uom=line.uom,
                currency_code=item.currency_code,
                location_id=line.location_id,
                lot_id=line.lot_id,
                source_document_type="INVENTORY_COUNT",
                source_document_id=count.count_id,
                source_document_line_id=line.line_id,
                reference=f"Count {count.count_number}",
                reason_code=line.reason_code,
            )

            inventory_transaction_service.create_adjustment(
                db=db,
                organization_id=org_id,
                input=txn_input,
                created_by_user_id=user_id,
            )

        count.status = CountStatus.POSTED
        count.posted_by_user_id = user_id
        count.posted_at = datetime.now(UTC)

        db.flush()
        return count

    @staticmethod
    def get_count_summary(
        db: Session,
        organization_id: UUID,
        count_id: UUID,
    ) -> CountSummary:
        """Get summary statistics for a count."""
        org_id = coerce_uuid(organization_id)
        cnt_id = coerce_uuid(count_id)

        count = db.get(InventoryCount, cnt_id)
        if not count or count.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Count not found")

        # Calculate variance totals
        variance_stats = db.execute(
            select(
                func.sum(InventoryCountLine.variance_value).label("total"),
                func.sum(
                    case(
                        (
                            InventoryCountLine.variance_value > 0,
                            InventoryCountLine.variance_value,
                        ),
                        else_=Decimal("0"),
                    )
                ).label("positive"),
                func.sum(
                    case(
                        (
                            InventoryCountLine.variance_value < 0,
                            InventoryCountLine.variance_value,
                        ),
                        else_=Decimal("0"),
                    )
                ).label("negative"),
            ).where(InventoryCountLine.count_id == cnt_id)
        ).first()

        total_variance = Decimal("0")
        positive_variance = Decimal("0")
        negative_variance = Decimal("0")
        if variance_stats:
            total_variance = variance_stats.total or Decimal("0")
            positive_variance = variance_stats.positive or Decimal("0")
            negative_variance = variance_stats.negative or Decimal("0")

        return CountSummary(
            count_id=count.count_id,
            count_number=count.count_number,
            status=count.status.value,
            total_items=count.total_items,
            items_counted=count.items_counted,
            items_with_variance=count.items_with_variance,
            total_variance_value=total_variance,
            positive_variance_value=positive_variance,
            negative_variance_value=negative_variance,
        )

    @staticmethod
    def get(
        db: Session,
        count_id: str,
        organization_id: str | UUID | None = None,
    ) -> InventoryCount:
        """Get a count by ID, optionally enforcing organization scope."""
        count = db.get(InventoryCount, coerce_uuid(count_id))
        if not count:
            raise HTTPException(status_code=404, detail="Count not found")
        if organization_id is not None and count.organization_id != coerce_uuid(
            organization_id
        ):
            raise HTTPException(status_code=404, detail="Count not found")
        return count

    @staticmethod
    def list(
        db: Session,
        organization_id: str | None = None,
        warehouse_id: str | None = None,
        status: CountStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> builtins.list[InventoryCount]:
        """List inventory counts with optional filters."""
        query = select(InventoryCount)

        if organization_id:
            query = query.where(
                InventoryCount.organization_id == coerce_uuid(organization_id)
            )

        if warehouse_id:
            query = query.where(
                InventoryCount.warehouse_id == coerce_uuid(warehouse_id)
            )

        if status:
            query = query.where(InventoryCount.status == status)

        query = query.order_by(InventoryCount.count_date.desc())
        return list(db.scalars(query.limit(limit).offset(offset)).all())

    @staticmethod
    def list_lines(
        db: Session,
        count_id: str,
        has_variance: bool | None = None,
        is_counted: bool | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> builtins.list[InventoryCountLine]:
        """List count lines with optional filters."""
        cnt_id = coerce_uuid(count_id)

        query = select(InventoryCountLine).where(InventoryCountLine.count_id == cnt_id)

        if has_variance is True:
            query = query.where(
                and_(
                    InventoryCountLine.variance_quantity != 0,
                    InventoryCountLine.variance_quantity.isnot(None),
                )
            )
        elif has_variance is False:
            query = query.where(
                (InventoryCountLine.variance_quantity == 0)
                | (InventoryCountLine.variance_quantity.is_(None))
            )

        if is_counted is True:
            query = query.where(InventoryCountLine.counted_quantity.isnot(None))
        elif is_counted is False:
            query = query.where(InventoryCountLine.counted_quantity.is_(None))

        return list(db.scalars(query.limit(limit).offset(offset)).all())


# Module-level singleton instance
inventory_count_service = InventoryCountService()
