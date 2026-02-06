"""
Inventory Count Line Model - Inventory Schema.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    DateTime,
    ForeignKey,
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


class InventoryCountLine(Base):
    """
    Inventory count line item.
    """

    __tablename__ = "inventory_count_line"
    __table_args__ = (
        UniqueConstraint(
            "count_id", "item_id", "lot_id", name="uq_inventory_count_line"
        ),
        {"schema": "inv"},
    )

    line_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    count_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inv.inventory_count.count_id"),
        nullable=False,
    )

    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inv.item.item_id"),
        nullable=False,
    )
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inv.warehouse.warehouse_id"),
        nullable=False,
    )
    location_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    lot_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # System quantity (frozen at count creation)
    system_quantity: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    uom: Mapped[str] = mapped_column(String(20), nullable=False)

    # Count results
    counted_quantity: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(20, 6), nullable=True
    )
    recount_quantity: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(20, 6), nullable=True
    )
    final_quantity: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(20, 6), nullable=True
    )

    # Variance
    variance_quantity: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(20, 6), nullable=True
    )
    variance_value: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(20, 6), nullable=True
    )
    variance_percent: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(10, 4), nullable=True
    )

    # Cost at count date
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)

    # Count metadata
    counted_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    counted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    recounted_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    recounted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Adjustment reason
    reason_code: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

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
