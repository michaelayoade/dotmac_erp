"""Stock reservation model for explicit inventory holds."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from app.models.inventory.inventory_lot import InventoryLot
    from app.models.inventory.item import Item
    from app.models.inventory.warehouse import Warehouse


class ReservationStatus(str, enum.Enum):
    """Reservation lifecycle status."""

    RESERVED = "RESERVED"
    PARTIALLY_FULFILLED = "PARTIALLY_FULFILLED"
    FULFILLED = "FULFILLED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class ReservationSourceType(str, enum.Enum):
    """Document type that owns a reservation."""

    SALES_ORDER = "SALES_ORDER"
    TRANSFER_ORDER = "TRANSFER_ORDER"
    MANUFACTURING_ORDER = "MANUFACTURING_ORDER"


class StockReservation(Base):
    """Explicit stock reservation linked to demand documents."""

    __tablename__ = "stock_reservation"
    __table_args__ = (
        UniqueConstraint(
            "source_type",
            "source_line_id",
            "lot_id",
            name="uq_reservation_source_lot",
        ),
        Index("ix_reservation_org_status", "organization_id", "status"),
        Index("ix_reservation_expires", "status", "expires_at"),
        Index("ix_reservation_item", "organization_id", "item_id", "warehouse_id"),
        {"schema": "inv"},
    )

    reservation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
    )

    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inv.item.item_id"),
        nullable=False,
    )
    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inv.warehouse.warehouse_id"),
        nullable=True,
    )
    lot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inv.inventory_lot.lot_id"),
        nullable=True,
    )

    quantity_reserved: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    quantity_fulfilled: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=Decimal("0"),
        server_default=text("0"),
    )
    quantity_cancelled: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=Decimal("0"),
        server_default=text("0"),
    )

    source_type: Mapped[ReservationSourceType] = mapped_column(
        Enum(ReservationSourceType, name="reservation_source_type"),
        nullable=False,
    )
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_line_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )

    status: Mapped[ReservationStatus] = mapped_column(
        Enum(ReservationStatus, name="reservation_status"),
        nullable=False,
        default=ReservationStatus.RESERVED,
        server_default=text("'RESERVED'"),
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    priority: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=10,
        server_default=text("10"),
    )

    reserved_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    reserved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    fulfilled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    cancellation_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)

    item: Mapped[Item] = relationship("Item", foreign_keys=[item_id], lazy="noload")
    warehouse: Mapped[Warehouse | None] = relationship(
        "Warehouse",
        foreign_keys=[warehouse_id],
        lazy="noload",
    )
    lot: Mapped[InventoryLot | None] = relationship(
        "InventoryLot",
        foreign_keys=[lot_id],
        lazy="noload",
    )

    @property
    def quantity_remaining(self) -> Decimal:
        """Remaining quantity held by this reservation."""
        return (
            self.quantity_reserved
            - (self.quantity_fulfilled or Decimal("0"))
            - (self.quantity_cancelled or Decimal("0"))
        )

    @property
    def is_fully_fulfilled(self) -> bool:
        """Return True when reserved quantity has been fully fulfilled."""
        return self.quantity_fulfilled >= self.quantity_reserved
