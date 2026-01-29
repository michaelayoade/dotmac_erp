"""
InventoryCountService - Physical inventory counts and cycle counts.

Manages inventory counts, variance calculation, and adjustment posting.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from app.models.finance.inv.inventory_count import InventoryCount, CountStatus
from app.models.finance.inv.inventory_count_line import InventoryCountLine
from app.models.finance.inv.item import Item
from app.models.finance.inv.warehouse import Warehouse
from app.models.finance.inv.inventory_transaction import TransactionType
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin


@dataclass
class CountInput:
    """Input for creating an inventory count."""

    count_number: str
    count_date: date
    fiscal_period_id: UUID
    count_description: Optional[str] = None
    warehouse_id: Optional[UUID] = None
    location_id: Optional[UUID] = None
    category_id: Optional[UUID] = None
    is_full_count: bool = False
    is_cycle_count: bool = False


@dataclass
class CountLineInput:
    """Input for recording a count."""

    item_id: UUID
    warehouse_id: UUID
    counted_quantity: Decimal
    lot_id: Optional[UUID] = None
    location_id: Optional[UUID] = None
    reason_code: Optional[str] = None
    notes: Optional[str] = None


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

        count.status = CountStatus.IN_PROGRESS
        db.commit()
        db.refresh(count)

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
        from app.services.finance.inv.balance import inventory_balance_service

        org_id = coerce_uuid(organization_id)
        user_id = coerce_uuid(created_by_user_id)

        # Check for duplicate count number
        existing = db.query(InventoryCount).filter(
            and_(
                InventoryCount.organization_id == org_id,
                InventoryCount.count_number == input.count_number,
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
            warehouse_id=coerce_uuid(input.warehouse_id) if input.warehouse_id else None,
            location_id=coerce_uuid(input.location_id) if input.location_id else None,
            category_id=coerce_uuid(input.category_id) if input.category_id else None,
            is_full_count=input.is_full_count,
            is_cycle_count=input.is_cycle_count,
            status=CountStatus.DRAFT,
            created_by_user_id=user_id,
        )

        db.add(count)
        db.flush()  # Get count_id

        # Build query for items in scope
        items_query = db.query(Item).filter(
            and_(
                Item.organization_id == org_id,
                Item.is_active == True,
                Item.track_inventory == True,
            )
        )

        if input.category_id:
            items_query = items_query.filter(Item.category_id == coerce_uuid(input.category_id))

        items = items_query.all()

        # Create count lines for each item
        total_items = 0
        warehouses = []

        if input.warehouse_id:
            wh = db.get(Warehouse, coerce_uuid(input.warehouse_id))
            if wh:
                warehouses = [wh]
        else:
            warehouses = db.query(Warehouse).filter(
                and_(
                    Warehouse.organization_id == org_id,
                    Warehouse.is_active == True,
                )
            ).all()

        for item in items:
            for warehouse in warehouses:
                # Get system quantity
                system_qty = inventory_balance_service.get_on_hand(
                    db=db,
                    organization_id=org_id,
                    item_id=item.item_id,
                    warehouse_id=warehouse.warehouse_id,
                )

                # Only include items with stock (or all for full count)
                if system_qty > 0 or input.is_full_count:
                    line = InventoryCountLine(
                        count_id=count.count_id,
                        item_id=item.item_id,
                        warehouse_id=warehouse.warehouse_id,
                        location_id=count.location_id,
                        system_quantity=system_qty,
                        uom=item.base_uom,
                        unit_cost=item.average_cost or item.standard_cost or Decimal("0"),
                    )
                    db.add(line)
                    total_items += 1

        count.total_items = total_items
        db.commit()
        db.refresh(count)

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

        # Find or create line
        line = db.query(InventoryCountLine).filter(
            and_(
                InventoryCountLine.count_id == cnt_id,
                InventoryCountLine.item_id == itm_id,
                InventoryCountLine.warehouse_id == wh_id,
                InventoryCountLine.lot_id == (coerce_uuid(input.lot_id) if input.lot_id else None),
            )
        ).first()

        if not line:
            # Create new line (for items not in original snapshot)
            item = db.get(Item, itm_id)
            if not item or item.organization_id != org_id:
                raise HTTPException(status_code=404, detail="Item not found")

            line = InventoryCountLine(
                count_id=cnt_id,
                item_id=itm_id,
                warehouse_id=wh_id,
                location_id=coerce_uuid(input.location_id) if input.location_id else None,
                lot_id=coerce_uuid(input.lot_id) if input.lot_id else None,
                system_quantity=Decimal("0"),  # New item not in snapshot
                uom=item.base_uom,
                unit_cost=item.average_cost or item.standard_cost or Decimal("0"),
            )
            db.add(line)
            count.total_items += 1

        # Record count
        if line.counted_quantity is None:
            line.counted_quantity = input.counted_quantity
            line.counted_by_user_id = user_id
            line.counted_at = datetime.now(timezone.utc)
            count.items_counted += 1
        else:
            # Recount
            line.recount_quantity = input.counted_quantity
            line.recounted_by_user_id = user_id
            line.recounted_at = datetime.now(timezone.utc)

        line.final_quantity = input.counted_quantity
        line.reason_code = input.reason_code
        line.notes = input.notes

        # Calculate variance
        line.variance_quantity = line.final_quantity - line.system_quantity
        line.variance_value = line.variance_quantity * line.unit_cost

        if line.system_quantity > 0:
            line.variance_percent = (
                (line.variance_quantity / line.system_quantity) * 100
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        else:
            line.variance_percent = Decimal("100") if line.variance_quantity > 0 else Decimal("0")

        # Update count stats
        if line.variance_quantity != 0:
            # Recalculate items with variance
            variance_count = db.query(func.count(InventoryCountLine.line_id)).filter(
                and_(
                    InventoryCountLine.count_id == cnt_id,
                    InventoryCountLine.variance_quantity != 0,
                    InventoryCountLine.variance_quantity.isnot(None),
                )
            ).scalar()
            count.items_with_variance = variance_count or 0

        # Update status to IN_PROGRESS
        if count.status == CountStatus.DRAFT:
            count.status = CountStatus.IN_PROGRESS

        db.commit()
        db.refresh(line)

        return line

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

        if count.status not in [CountStatus.DRAFT, CountStatus.IN_PROGRESS]:
            raise HTTPException(
                status_code=400,
                detail="Count must be in DRAFT or IN_PROGRESS status",
            )

        count.status = CountStatus.COMPLETED
        db.commit()
        db.refresh(count)

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
        count.approved_at = datetime.now(timezone.utc)

        db.commit()
        db.refresh(count)

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
        from app.services.finance.inv.transaction import inventory_transaction_service, TransactionInput

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
        lines = db.query(InventoryCountLine).filter(
            and_(
                InventoryCountLine.count_id == cnt_id,
                InventoryCountLine.variance_quantity != 0,
                InventoryCountLine.variance_quantity.isnot(None),
            )
        ).all()

        # Create adjustment transactions
        for line in lines:
            item = db.get(Item, line.item_id)
            if not item:
                continue

            variance_qty = line.variance_quantity or Decimal("0")
            txn_input = TransactionInput(
                transaction_type=TransactionType.COUNT_ADJUSTMENT,
                transaction_date=datetime.combine(count.count_date, datetime.min.time()),
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
        count.posted_at = datetime.now(timezone.utc)

        db.commit()
        db.refresh(count)

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
        variance_stats = db.query(
            func.sum(InventoryCountLine.variance_value).label("total"),
            func.sum(
                func.case(
                    (InventoryCountLine.variance_value > 0, InventoryCountLine.variance_value),
                    else_=Decimal("0"),
                )
            ).label("positive"),
            func.sum(
                func.case(
                    (InventoryCountLine.variance_value < 0, InventoryCountLine.variance_value),
                    else_=Decimal("0"),
                )
            ).label("negative"),
        ).filter(
            InventoryCountLine.count_id == cnt_id
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
    ) -> InventoryCount:
        """Get a count by ID."""
        count = db.get(InventoryCount, coerce_uuid(count_id))
        if not count:
            raise HTTPException(status_code=404, detail="Count not found")
        return count

    @staticmethod
    def list(
        db: Session,
        organization_id: Optional[str] = None,
        warehouse_id: Optional[str] = None,
        status: Optional[CountStatus] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[InventoryCount]:
        """List inventory counts with optional filters."""
        query = db.query(InventoryCount)

        if organization_id:
            query = query.filter(InventoryCount.organization_id == coerce_uuid(organization_id))

        if warehouse_id:
            query = query.filter(InventoryCount.warehouse_id == coerce_uuid(warehouse_id))

        if status:
            query = query.filter(InventoryCount.status == status)

        query = query.order_by(InventoryCount.count_date.desc())
        return query.limit(limit).offset(offset).all()

    @staticmethod
    def list_lines(
        db: Session,
        count_id: str,
        has_variance: Optional[bool] = None,
        is_counted: Optional[bool] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[InventoryCountLine]:
        """List count lines with optional filters."""
        cnt_id = coerce_uuid(count_id)

        query = db.query(InventoryCountLine).filter(InventoryCountLine.count_id == cnt_id)

        if has_variance is True:
            query = query.filter(
                and_(
                    InventoryCountLine.variance_quantity != 0,
                    InventoryCountLine.variance_quantity.isnot(None),
                )
            )
        elif has_variance is False:
            query = query.filter(
                (InventoryCountLine.variance_quantity == 0) |
                (InventoryCountLine.variance_quantity.is_(None))
            )

        if is_counted is True:
            query = query.filter(InventoryCountLine.counted_quantity.isnot(None))
        elif is_counted is False:
            query = query.filter(InventoryCountLine.counted_quantity.is_(None))

        return query.limit(limit).offset(offset).all()


# Module-level singleton instance
inventory_count_service = InventoryCountService()
