"""
Inventory Lot Model - Inventory Schema.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
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
    from app.models.inventory.item import Item
    from app.models.inventory.warehouse import Warehouse

if TYPE_CHECKING:
    from app.models.inventory.item import Item
    from app.models.inventory.warehouse import Warehouse


class InventoryLot(Base):
    """
    Inventory lot/batch for lot-tracked items.
    """

    __tablename__ = "inventory_lot"
    __table_args__ = (
        UniqueConstraint("item_id", "lot_number", name="uq_inventory_lot"),
        Index("idx_lot_item", "item_id"),
        Index("idx_lot_org", "organization_id"),
        Index("idx_lot_warehouse", "warehouse_id"),
        {"schema": "inv"},
    )

    lot_id: Mapped[uuid.UUID] = mapped_column(
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

    lot_number: Mapped[str] = mapped_column(String(50), nullable=False)

    # Dates
    manufacture_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    received_date: Mapped[date] = mapped_column(Date, nullable=False)

    # Supplier/source
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    supplier_lot_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    purchase_order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    # Cost
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)

    # Quantity tracking
    initial_quantity: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    quantity_on_hand: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    quantity_allocated: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )
    quantity_available: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    allocation_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Status
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_quarantined: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    quarantine_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # Certificate/QC
    certificate_of_analysis: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    qc_status: Mapped[str | None] = mapped_column(String(30), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )

    # Relationships
    item: Mapped["Item"] = relationship(
        "Item",
        foreign_keys=[item_id],
        lazy="noload",
    )
    warehouse: Mapped[Optional["Warehouse"]] = relationship(
        "Warehouse",
        foreign_keys=[warehouse_id],
        lazy="noload",
    )
