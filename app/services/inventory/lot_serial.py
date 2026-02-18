"""
LotSerialService - Lot and Serial Number Tracking.

Manages inventory lots, batches, and serial number tracking.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import cast
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.inventory.inventory_lot import InventoryLot
from app.models.inventory.item import Item
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin

logger = logging.getLogger(__name__)


@dataclass
class LotInput:
    """Input for creating an inventory lot."""

    item_id: UUID
    lot_number: str
    received_date: date
    unit_cost: Decimal
    initial_quantity: Decimal
    manufacture_date: date | None = None
    expiry_date: date | None = None
    supplier_id: UUID | None = None
    supplier_lot_number: str | None = None
    purchase_order_id: UUID | None = None
    certificate_of_analysis: str | None = None


@dataclass
class SerialNumber:
    """A serial number entry."""

    serial_number: str
    lot_id: UUID | None = None
    item_id: UUID | None = None
    status: str = "AVAILABLE"
    location: str | None = None


@dataclass
class LotAllocation:
    """Lot allocation for an order."""

    lot_id: UUID
    quantity: Decimal
    serial_numbers: list[str] = field(default_factory=list)


@dataclass
class LotTraceability:
    """Traceability information for a lot."""

    lot_id: UUID
    lot_number: str
    item_id: UUID
    item_code: str
    supplier_lot: str | None
    received_date: date
    expiry_date: date | None
    total_received: Decimal
    total_remaining: Decimal
    total_consumed: Decimal


class LotSerialService(ListResponseMixin):
    """
    Service for lot and serial number tracking.

    Manages lot creation, allocation, quarantine, and traceability.
    """

    @staticmethod
    def create_lot(
        db: Session,
        organization_id: UUID,
        input: LotInput,
    ) -> InventoryLot:
        """
        Create a new inventory lot.

        Args:
            db: Database session
            organization_id: Organization scope
            input: Lot input data

        Returns:
            Created InventoryLot
        """
        org_id = coerce_uuid(organization_id)
        item_id = coerce_uuid(input.item_id)

        # Validate item
        item = db.scalar(
            select(Item)
            .where(Item.item_id == item_id)
            .where(Item.organization_id == org_id)
        )

        if not item:
            raise HTTPException(status_code=404, detail="Item not found")

        if not item.track_lots:
            raise HTTPException(
                status_code=400, detail="Item is not configured for lot tracking"
            )

        # Check for duplicate lot number
        existing = db.scalar(
            select(InventoryLot)
            .where(InventoryLot.item_id == item_id)
            .where(InventoryLot.lot_number == input.lot_number)
        )

        if existing:
            raise HTTPException(
                status_code=400,
                detail=f"Lot number {input.lot_number} already exists for this item",
            )

        lot = InventoryLot(
            organization_id=org_id,
            item_id=item_id,
            lot_number=input.lot_number,
            manufacture_date=input.manufacture_date,
            expiry_date=input.expiry_date,
            received_date=input.received_date,
            supplier_id=coerce_uuid(input.supplier_id) if input.supplier_id else None,
            supplier_lot_number=input.supplier_lot_number,
            purchase_order_id=coerce_uuid(input.purchase_order_id)
            if input.purchase_order_id
            else None,
            unit_cost=input.unit_cost,
            initial_quantity=input.initial_quantity,
            quantity_on_hand=input.initial_quantity,
            quantity_allocated=Decimal("0"),
            quantity_available=input.initial_quantity,
            certificate_of_analysis=input.certificate_of_analysis,
        )

        db.add(lot)
        db.commit()
        db.refresh(lot)

        return lot

    @staticmethod
    def allocate_from_lot(
        db: Session,
        organization_id: UUID | None,
        lot_id: UUID | Decimal,
        quantity: Decimal | None = None,
        reference: str | None = None,
    ) -> InventoryLot:
        """
        Allocate quantity from a lot.

        Args:
            db: Database session
            lot_id: Lot to allocate from
            quantity: Quantity to allocate
            reference: Allocation reference

        Returns:
            Updated InventoryLot
        """
        lot_id_value: UUID
        quantity_value: Decimal
        org_id = organization_id
        if quantity is None:
            if organization_id is None:
                raise HTTPException(
                    status_code=400, detail="Organization id is required"
                )
            lot_id_value = coerce_uuid(organization_id)
            quantity_value = cast(Decimal, lot_id)
            org_id = None
        else:
            lot_id_value = coerce_uuid(cast(UUID, lot_id))
            quantity_value = quantity

        lot = db.scalar(select(InventoryLot).where(InventoryLot.lot_id == lot_id_value))

        if not lot:
            raise HTTPException(status_code=404, detail="Lot not found")
        if org_id is not None:
            org_id_value = coerce_uuid(org_id)
            if lot.organization_id != org_id_value:
                raise HTTPException(status_code=404, detail="Lot not found")

        if lot.is_quarantined:
            raise HTTPException(
                status_code=400, detail=f"Lot {lot.lot_number} is quarantined"
            )

        if quantity_value > lot.quantity_available:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Insufficient available quantity. "
                    f"Available: {lot.quantity_available}"
                ),
            )

        lot.quantity_allocated += quantity_value
        lot.quantity_available = lot.quantity_on_hand - lot.quantity_allocated
        if reference:
            lot.allocation_reference = reference

        db.commit()
        db.refresh(lot)

        return lot

    @staticmethod
    def deallocate_from_lot(
        db: Session,
        organization_id: UUID | None,
        lot_id: UUID | Decimal,
        quantity: Decimal | None = None,
    ) -> InventoryLot:
        """
        Release allocation from a lot.

        Args:
            db: Database session
            lot_id: Lot to deallocate from
            quantity: Quantity to release

        Returns:
            Updated InventoryLot
        """
        lot_id_value: UUID
        quantity_value: Decimal
        org_id = organization_id
        if quantity is None:
            if organization_id is None:
                raise HTTPException(
                    status_code=400, detail="Organization id is required"
                )
            lot_id_value = coerce_uuid(organization_id)
            quantity_value = cast(Decimal, lot_id)
            org_id = None
        else:
            lot_id_value = coerce_uuid(cast(UUID, lot_id))
            quantity_value = quantity

        lot = db.scalar(select(InventoryLot).where(InventoryLot.lot_id == lot_id_value))

        if not lot:
            raise HTTPException(status_code=404, detail="Lot not found")
        if org_id is not None:
            org_id_value = coerce_uuid(org_id)
            if lot.organization_id != org_id_value:
                raise HTTPException(status_code=404, detail="Lot not found")

        if quantity_value > lot.quantity_allocated:
            quantity_value = lot.quantity_allocated

        lot.quantity_allocated -= quantity_value
        lot.quantity_available = lot.quantity_on_hand - lot.quantity_allocated

        db.commit()
        db.refresh(lot)

        return lot

    @staticmethod
    def consume_from_lot(
        db: Session,
        organization_id: UUID | None,
        lot_id: UUID | Decimal,
        quantity: Decimal | None = None,
    ) -> InventoryLot:
        """
        Consume quantity from a lot (reduce on-hand).

        Args:
            db: Database session
            lot_id: Lot to consume from
            quantity: Quantity to consume

        Returns:
            Updated InventoryLot
        """
        lot_id_value: UUID
        quantity_value: Decimal
        org_id = organization_id
        if quantity is None:
            if organization_id is None:
                raise HTTPException(
                    status_code=400, detail="Organization id is required"
                )
            lot_id_value = coerce_uuid(organization_id)
            quantity_value = cast(Decimal, lot_id)
            org_id = None
        else:
            lot_id_value = coerce_uuid(cast(UUID, lot_id))
            quantity_value = quantity

        lot = db.scalar(select(InventoryLot).where(InventoryLot.lot_id == lot_id_value))

        if not lot:
            raise HTTPException(status_code=404, detail="Lot not found")
        if org_id is not None:
            org_id_value = coerce_uuid(org_id)
            if lot.organization_id != org_id_value:
                raise HTTPException(status_code=404, detail="Lot not found")

        if quantity_value > lot.quantity_on_hand:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot consume {quantity_value}. "
                f"On hand: {lot.quantity_on_hand}",
            )

        lot.quantity_on_hand -= quantity_value

        # Also reduce allocated if necessary
        if lot.quantity_allocated > lot.quantity_on_hand:
            lot.quantity_allocated = lot.quantity_on_hand

        lot.quantity_available = lot.quantity_on_hand - lot.quantity_allocated

        # Deactivate if depleted
        if lot.quantity_on_hand <= 0:
            lot.is_active = False

        db.commit()
        db.refresh(lot)

        return lot

    @staticmethod
    def quarantine_lot(
        db: Session,
        organization_id: UUID | None,
        lot_id: UUID | str,
        reason: str | None = None,
    ) -> InventoryLot:
        """
        Place a lot in quarantine.

        Args:
            db: Database session
            lot_id: Lot to quarantine
            reason: Reason for quarantine

        Returns:
            Updated InventoryLot
        """
        org_id = organization_id
        if reason is None:
            if organization_id is None:
                raise HTTPException(
                    status_code=400, detail="Organization id is required"
                )
            lot_id, reason = organization_id, str(lot_id)
            org_id = None

        lot_id = coerce_uuid(lot_id)

        lot = db.scalar(select(InventoryLot).where(InventoryLot.lot_id == lot_id))

        if not lot:
            raise HTTPException(status_code=404, detail="Lot not found")
        if org_id is not None:
            org_id_value = coerce_uuid(org_id)
            if lot.organization_id != org_id_value:
                raise HTTPException(status_code=404, detail="Lot not found")

        lot.is_quarantined = True
        lot.quarantine_reason = reason
        lot.quantity_available = Decimal("0")

        db.commit()
        db.refresh(lot)

        return lot

    @staticmethod
    def release_quarantine(
        db: Session,
        organization_id: UUID | None,
        lot_id: UUID | str | None = None,
        qc_status: str = "PASSED",
    ) -> InventoryLot:
        """
        Release a lot from quarantine.

        Args:
            db: Database session
            lot_id: Lot to release
            qc_status: QC status after review

        Returns:
            Updated InventoryLot
        """
        org_id = organization_id
        if lot_id is None:
            if organization_id is None:
                raise HTTPException(
                    status_code=400, detail="Organization id is required"
                )
            lot_id = organization_id
            org_id = None
        elif isinstance(lot_id, str) and qc_status == "PASSED":
            # Legacy signature: (db, lot_id, qc_status)
            if organization_id is None:
                raise HTTPException(
                    status_code=400, detail="Organization id is required"
                )
            lot_id, qc_status = organization_id, str(lot_id)
            org_id = None

        lot_id = coerce_uuid(lot_id)

        lot = db.scalar(select(InventoryLot).where(InventoryLot.lot_id == lot_id))

        if not lot:
            raise HTTPException(status_code=404, detail="Lot not found")
        if org_id is not None:
            org_id_value = coerce_uuid(org_id)
            if lot.organization_id != org_id_value:
                raise HTTPException(status_code=404, detail="Lot not found")

        lot.is_quarantined = False
        lot.quarantine_reason = None
        lot.qc_status = qc_status
        lot.quantity_available = lot.quantity_on_hand - lot.quantity_allocated

        db.commit()
        db.refresh(lot)

        return lot

    @staticmethod
    def get_expiring_lots(
        db: Session,
        organization_id: UUID,
        days_ahead: int = 30,
    ) -> list[InventoryLot]:
        """
        Get lots expiring within specified days.

        Args:
            db: Database session
            organization_id: Organization scope
            days_ahead: Days to look ahead

        Returns:
            List of expiring InventoryLot objects
        """
        org_id = coerce_uuid(organization_id)
        from datetime import timedelta

        cutoff_date = date.today() + timedelta(days=days_ahead)

        return db.scalars(
            select(InventoryLot)
            .join(Item, InventoryLot.item_id == Item.item_id)
            .where(Item.organization_id == org_id)
            .where(InventoryLot.expiry_date <= cutoff_date)
            .where(InventoryLot.expiry_date >= date.today())
            .where(InventoryLot.quantity_on_hand > 0)
            .where(InventoryLot.is_active.is_(True))
            .order_by(InventoryLot.expiry_date.asc())
        ).all()

    @staticmethod
    def get_expired_lots(
        db: Session,
        organization_id: UUID,
    ) -> list[InventoryLot]:
        """
        Get already expired lots.

        Args:
            db: Database session
            organization_id: Organization scope

        Returns:
            List of expired InventoryLot objects
        """
        org_id = coerce_uuid(organization_id)

        return db.scalars(
            select(InventoryLot)
            .join(Item, InventoryLot.item_id == Item.item_id)
            .where(Item.organization_id == org_id)
            .where(InventoryLot.expiry_date < date.today())
            .where(InventoryLot.quantity_on_hand > 0)
            .where(InventoryLot.is_active.is_(True))
            .order_by(InventoryLot.expiry_date.asc())
        ).all()

    @staticmethod
    def get_traceability(
        db: Session,
        organization_id: UUID | None,
        lot_id: UUID | None = None,
    ) -> LotTraceability:
        """
        Get traceability information for a lot.

        Args:
            db: Database session
            lot_id: Lot ID

        Returns:
            LotTraceability object
        """
        org_id = organization_id
        if lot_id is None:
            if organization_id is None:
                raise HTTPException(
                    status_code=400, detail="Organization id is required"
                )
            lot_id = organization_id
            org_id = None

        lot_id = coerce_uuid(lot_id)

        lot = db.scalar(select(InventoryLot).where(InventoryLot.lot_id == lot_id))

        if not lot:
            raise HTTPException(status_code=404, detail="Lot not found")
        if org_id is not None:
            org_id_value = coerce_uuid(org_id)
            if lot.organization_id != org_id_value:
                raise HTTPException(status_code=404, detail="Lot not found")

        item = db.scalar(select(Item).where(Item.item_id == lot.item_id))

        return LotTraceability(
            lot_id=lot.lot_id,
            lot_number=lot.lot_number,
            item_id=lot.item_id,
            item_code=item.item_code if item else "Unknown",
            supplier_lot=lot.supplier_lot_number,
            received_date=lot.received_date,
            expiry_date=lot.expiry_date,
            total_received=lot.initial_quantity,
            total_remaining=lot.quantity_on_hand,
            total_consumed=lot.initial_quantity - lot.quantity_on_hand,
        )

    @staticmethod
    def get(
        db: Session,
        lot_id: str,
        organization_id: UUID | None = None,
    ) -> InventoryLot | None:
        """Get a lot by ID."""
        lot = db.scalar(
            select(InventoryLot).where(InventoryLot.lot_id == coerce_uuid(lot_id))
        )
        if not lot:
            return None
        if organization_id is not None and lot.organization_id != coerce_uuid(
            organization_id
        ):
            return None
        return lot

    @staticmethod
    def get_by_number(
        db: Session,
        item_id: UUID,
        lot_number: str,
    ) -> InventoryLot | None:
        """Get a lot by number."""
        return db.scalar(
            select(InventoryLot)
            .where(InventoryLot.item_id == coerce_uuid(item_id))
            .where(InventoryLot.lot_number == lot_number)
        )

    @staticmethod
    def list_by_item(
        db: Session,
        item_id: UUID,
        include_inactive: bool = False,
    ) -> list[InventoryLot]:
        """List all lots for an item."""
        query = select(InventoryLot).where(InventoryLot.item_id == coerce_uuid(item_id))

        if not include_inactive:
            query = query.where(InventoryLot.is_active.is_(True))

        return db.scalars(query.order_by(InventoryLot.received_date.desc())).all()

    @staticmethod
    def list(
        db: Session,
        organization_id: str | None = None,
        item_id: str | None = None,
        is_quarantined: bool | None = None,
        has_expiry: bool | None = None,
        include_zero_quantity: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[InventoryLot]:
        """
        List lots with filters.

        Args:
            db: Database session
            organization_id: Filter by organization
            item_id: Filter by item
            is_quarantined: Filter by quarantine status
            has_expiry: Filter lots with expiry date
            include_zero_quantity: Include depleted lots
            limit: Maximum results
            offset: Pagination offset

        Returns:
            List of InventoryLot objects
        """
        query = select(InventoryLot)

        if item_id:
            query = query.where(InventoryLot.item_id == coerce_uuid(item_id))

        if organization_id:
            query = query.join(Item, InventoryLot.item_id == Item.item_id).where(
                Item.organization_id == coerce_uuid(organization_id)
            )

        if is_quarantined is not None:
            query = query.where(InventoryLot.is_quarantined == is_quarantined)

        if has_expiry is not None:
            if has_expiry:
                query = query.where(InventoryLot.expiry_date.isnot(None))
            else:
                query = query.where(InventoryLot.expiry_date.is_(None))

        if not include_zero_quantity:
            query = query.where(InventoryLot.quantity_on_hand > 0)

        return db.scalars(
            query.order_by(InventoryLot.received_date.desc())
            .offset(offset)
            .limit(limit)
        ).all()


# Module-level instance
lot_serial_service = LotSerialService()
