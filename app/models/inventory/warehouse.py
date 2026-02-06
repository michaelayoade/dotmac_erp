"""
Warehouse Model - Inventory Schema.
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Warehouse(Base):
    """
    Warehouse/storage facility.
    """

    __tablename__ = "warehouse"
    __table_args__ = (
        UniqueConstraint("organization_id", "warehouse_code", name="uq_warehouse"),
        {"schema": "inv"},
    )

    warehouse_id: Mapped[uuid.UUID] = mapped_column(
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

    warehouse_code: Mapped[str] = mapped_column(String(30), nullable=False)
    warehouse_name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Location linkage
    location_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.location.location_id"),
        nullable=True,
    )

    # Address
    address: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    # Contact
    contact_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    contact_phone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    contact_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Type flags
    is_receiving: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_shipping: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_consignment: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_transit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Default cost center
    cost_center_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

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
