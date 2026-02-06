"""
Item Model - Inventory Schema.
"""

import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ItemType(str, enum.Enum):
    INVENTORY = "INVENTORY"
    NON_INVENTORY = "NON_INVENTORY"
    SERVICE = "SERVICE"
    KIT = "KIT"


class CostingMethod(str, enum.Enum):
    FIFO = "FIFO"
    WEIGHTED_AVERAGE = "WEIGHTED_AVERAGE"
    SPECIFIC_IDENTIFICATION = "SPECIFIC_IDENTIFICATION"
    STANDARD_COST = "STANDARD_COST"


class Item(Base):
    """
    Inventory item master.
    """

    __tablename__ = "item"
    __table_args__ = (
        UniqueConstraint("organization_id", "item_code", name="uq_item"),
        Index("idx_item_category", "category_id"),
        {"schema": "inv"},
    )

    item_id: Mapped[uuid.UUID] = mapped_column(
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

    item_code: Mapped[str] = mapped_column(String(50), nullable=False)
    item_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    item_type: Mapped[ItemType] = mapped_column(
        Enum(ItemType, name="item_type"),
        nullable=False,
        default=ItemType.INVENTORY,
    )
    category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inv.item_category.category_id"),
        nullable=False,
    )

    # Units
    base_uom: Mapped[str] = mapped_column(String(20), nullable=False)
    purchase_uom: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    sales_uom: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    # Costing
    costing_method: Mapped[CostingMethod] = mapped_column(
        Enum(CostingMethod, name="costing_method"),
        nullable=False,
        default=CostingMethod.WEIGHTED_AVERAGE,
    )
    standard_cost: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(20, 6), nullable=True
    )
    last_purchase_cost: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(20, 6), nullable=True
    )
    average_cost: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(20, 6), nullable=True
    )

    # Pricing
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    list_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 6), nullable=True)

    # Stock tracking
    track_inventory: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    track_lots: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    track_serial_numbers: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    # Reorder
    reorder_point: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(20, 6), nullable=True
    )
    reorder_quantity: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(20, 6), nullable=True
    )
    minimum_stock: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(20, 6), nullable=True
    )
    maximum_stock: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(20, 6), nullable=True
    )

    # Lead time
    lead_time_days: Mapped[Optional[int]] = mapped_column(Numeric(10, 0), nullable=True)

    # Physical attributes
    weight: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 6), nullable=True)
    weight_uom: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    volume: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 6), nullable=True)
    volume_uom: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    # Barcodes
    barcode: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    manufacturer_part_number: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True
    )

    # Tax
    tax_code_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    is_taxable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Account overrides
    inventory_account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    cogs_account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    revenue_account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # Default supplier
    default_supplier_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_purchaseable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_saleable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )
