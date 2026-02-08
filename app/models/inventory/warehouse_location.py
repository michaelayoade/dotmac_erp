"""
Warehouse Location Model - Inventory Schema.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class WarehouseLocation(Base):
    """
    Storage location within a warehouse (bin, shelf, zone).
    """

    __tablename__ = "warehouse_location"
    __table_args__ = (
        UniqueConstraint("warehouse_id", "location_code", name="uq_warehouse_location"),
        {"schema": "inv"},
    )

    location_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inv.warehouse.warehouse_id"),
        nullable=False,
    )

    location_code: Mapped[str] = mapped_column(String(30), nullable=False)
    location_name: Mapped[str] = mapped_column(String(100), nullable=False)

    # Hierarchy (Zone > Aisle > Rack > Shelf > Bin)
    parent_location_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inv.warehouse_location.location_id"),
        nullable=True,
    )
    location_type: Mapped[str] = mapped_column(String(30), nullable=False)

    # Physical coordinates
    zone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    aisle: Mapped[str | None] = mapped_column(String(20), nullable=True)
    rack: Mapped[str | None] = mapped_column(String(20), nullable=True)
    shelf: Mapped[str | None] = mapped_column(String(20), nullable=True)
    bin: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Location type flags
    is_picking: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_receiving: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_shipping: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_quarantine: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

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
